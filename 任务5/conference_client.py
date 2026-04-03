"""
任务5 - 多方语音会议系统客户端

控制面继续使用 TCP 信令，音频面使用 UDP over IP Multicast。
接收端按发送者分路缓存，每路分别做丢包恢复、时间戳保留、
低频噪音检测和高通滤波，最后按照主发言人优先策略混音成一路播放。
"""

from __future__ import annotations

import base64
import json
import queue
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import sys

try:
    import pyaudio
except ImportError:
    pyaudio = None


def _ensure_task2_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task2_dir = base / "任务2"
    if str(task2_dir) not in sys.path:
        sys.path.insert(0, str(task2_dir))


_ensure_task2_on_path()

from audio_config import (
    AUDIO_FORMAT,
    CHANNELS,
    CHUNK_SIZE,
    DEFAULT_HOST,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
)
from conference_protocol import (
    MESSAGE_DELIMITER,
    encode_contact_add,
    encode_contact_delete,
    encode_contact_list,
    encode_contact_search,
    encode_contact_update,
    encode_login,
    encode_logout,
    encode_online_query,
    encode_room_audio_chunk,
    encode_room_create,
    encode_room_dismiss,
    encode_room_invite,
    encode_room_join,
    encode_room_leave,
)
from multicast_audio import (
    MULTICAST_TTL,
    HighPassFilterState,
    NoiseMetrics,
    RedundantJitterBuffer,
    analyze_noise,
    frame_duration_ms,
    frame_duration_seconds,
    has_low_frequency_noise,
    high_pass_filter_pcm16,
    mix_pcm16_frames,
    pack_audio_packet,
    pcm16_rms,
    resolve_multicast_interface_ip,
    unpack_audio_packet,
)

DEFAULT_PORT = 8882


class RoomState:
    IDLE = "idle"
    IN_ROOM = "in_room"


@dataclass
class RemoteAudioStream:
    sender_id: bytes
    buffer: RedundantJitterBuffer
    display_name: str
    high_pass_state: HighPassFilterState = field(default_factory=HighPassFilterState)
    last_seen_monotonic: float = field(default_factory=time.monotonic)
    last_packet_timestamp_ms: int = 0
    last_played_timestamp_ms: int = 0
    smoothed_raw_rms: float = 0.0
    smoothed_voice_rms: float = 0.0
    smoothed_low_frequency_ratio: float = 0.0
    smoothed_zero_crossing_rate: float = 0.0
    low_frequency_noise: bool = False
    noise_score: int = 0


class ConferenceClient:

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        on_room_state_change=None,
        on_room_invite=None,
        on_room_member_update=None,
        on_room_dismissed=None,
    ) -> None:
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._recv_thread_started = False
        self._username = ""

        self._room_id = ""
        self._room_state = RoomState.IDLE
        self._is_creator = False
        self._my_position = -1
        self._audio_protocol = "udp"

        self._multicast_group = ""
        self._multicast_port = 0
        self._multicast_interface_ip = "0.0.0.0"
        self._multicast_recv_sock: Optional[socket.socket] = None
        self._multicast_send_sock: Optional[socket.socket] = None

        self._p = pyaudio.PyAudio() if pyaudio else None
        self._out_stream = None
        self._in_stream = None
        self._is_streaming = False

        self._frame_bytes = CHUNK_SIZE * CHANNELS * SAMPLE_WIDTH
        self._frame_duration_ms = frame_duration_ms(SAMPLE_RATE, CHUNK_SIZE)
        self._frame_interval = frame_duration_seconds(SAMPLE_RATE, CHUNK_SIZE)
        self._sender_id = uuid.uuid4().bytes
        self._packet_sequence = 0
        self._previous_capture_frame: Optional[bytes] = None
        self._previous_capture_timestamp_ms: Optional[int] = None

        self._audio_streams_lock = threading.RLock()
        self._remote_audio_streams: Dict[bytes, RemoteAudioStream] = {}
        self._audio_monitor_snapshot: Dict[str, dict] = {}
        self._main_speaker_sender_id: Optional[bytes] = None

        self._response_queue: queue.Queue = queue.Queue()
        self._contacts: List[str] = []

        self._on_room_state_change = on_room_state_change
        self._on_room_invite = on_room_invite
        self._on_room_member_update = on_room_member_update
        self._on_room_dismissed = on_room_dismissed

        self._cached_members: List[dict] = []
        self._cached_positions: dict = {}

    @property
    def username(self) -> str:
        return self._username

    @property
    def room_id(self) -> str:
        return self._room_id

    @property
    def room_state(self) -> str:
        return self._room_state

    @property
    def is_creator(self) -> bool:
        return self._is_creator

    @property
    def my_position(self) -> int:
        return self._my_position

    @property
    def audio_protocol(self) -> str:
        return self._audio_protocol

    @property
    def multicast_group(self) -> str:
        return self._multicast_group

    @property
    def multicast_port(self) -> int:
        return self._multicast_port

    def get_audio_monitor_snapshot(self) -> Dict[str, dict]:
        with self._audio_streams_lock:
            return {key: dict(value) for key, value in self._audio_monitor_snapshot.items()}

    def _ensure_connected(self) -> bool:
        if self._sock:
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((self._host, self._port))
            return True
        except OSError as exc:
            print(f"[Client] Connect failed: {exc}")
            self._sock = None
            return False

    def _start_recv_thread(self) -> None:
        if self._recv_thread_started:
            return
        if self._sock:
            self._sock.settimeout(None)
        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()
        self._recv_thread_started = True

    def login(self, username: str):
        if not username:
            return False, "用户名不能为空"
        if not self._ensure_connected():
            return False, "无法连接到服务器"

        self._send_raw(encode_login(username))
        success, message = self._sync_wait_response()
        if success:
            self._username = username
            if self._p:
                try:
                    self._out_stream = self._p.open(
                        format=AUDIO_FORMAT,
                        channels=CHANNELS,
                        rate=SAMPLE_RATE,
                        output=True,
                        frames_per_buffer=CHUNK_SIZE,
                    )
                except Exception as exc:
                    print(f"[Client] Open output device failed: {exc}")
            self._start_recv_thread()
        return success, message

    def _sync_wait_response(self, timeout: float = 5.0):
        try:
            if not self._sock:
                return False, "未连接到服务器"
            self._sock.settimeout(timeout)
            buffer = ""
            while True:
                data = self._sock.recv(4096)
                if not data:
                    return False, "连接断开"
                buffer += data.decode("utf-8", errors="ignore")
                if MESSAGE_DELIMITER not in buffer:
                    continue
                line, _ = buffer.split(MESSAGE_DELIMITER, 1)
                line = line.strip()
                if not line:
                    continue
                response = json.loads(line)
                return response.get("success", False), response.get("message", "")
        except socket.timeout:
            return False, "等待响应超时"
        except Exception as exc:
            return False, f"网络错误: {exc}"

    def logout(self) -> None:
        if self._sock and self._username:
            try:
                self._send_raw(encode_logout(self._username))
            except OSError:
                pass
        self.disconnect()
        self._username = ""

    def disconnect(self) -> None:
        self._running = False
        self._recv_thread_started = False
        self._stop_audio()
        self._teardown_udp()
        try:
            if self._out_stream:
                self._out_stream.stop_stream()
                self._out_stream.close()
        except Exception:
            pass
        self._out_stream = None
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._sock = None
        self._clear_room_state()

    def _clear_room_state(self) -> None:
        self._room_id = ""
        self._room_state = RoomState.IDLE
        self._is_creator = False
        self._my_position = -1
        self._audio_protocol = "udp"
        self._multicast_group = ""
        self._multicast_port = 0
        self._reset_remote_audio_streams()
        if self._on_room_state_change:
            self._on_room_state_change(self._room_state)

    def _request_response(self, message: str, timeout: float = 5.0):
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except queue.Empty:
                break
        self._send_raw(message)
        try:
            return self._response_queue.get(timeout=timeout)
        except queue.Empty:
            return False, "等待响应超时", {}

    def add_contact(self, contact_name: str):
        ok, msg, _ = self._request_response(
            encode_contact_add(self._username, contact_name)
        )
        return ok, msg

    def delete_contact(self, contact_name: str):
        ok, msg, _ = self._request_response(
            encode_contact_delete(self._username, contact_name)
        )
        return ok, msg

    def update_contact(self, old_name: str, new_name: str):
        ok, msg, _ = self._request_response(
            encode_contact_update(self._username, old_name, new_name)
        )
        return ok, msg

    def get_contacts(self) -> List[str]:
        ok, _, data = self._request_response(encode_contact_list(self._username))
        if ok:
            self._contacts = data.get("contacts", [])
            return self._contacts
        return []

    def search_contacts(self, keyword: str) -> List[str]:
        ok, _, data = self._request_response(
            encode_contact_search(self._username, keyword)
        )
        if ok:
            return data.get("contacts", [])
        return []

    def get_online_users(self) -> List[str]:
        ok, _, data = self._request_response(encode_online_query(self._username))
        if ok:
            return data.get("online_users", [])
        return []

    def create_room(self, audio_protocol: str = "udp"):
        if audio_protocol != "udp":
            audio_protocol = "udp"
        ok, msg, data = self._request_response(
            encode_room_create(self._username, audio_protocol)
        )
        if ok:
            self._room_id = data.get("room_id", "")
            self._my_position = data.get("position", -1)
            self._is_creator = True
            self._room_state = RoomState.IN_ROOM
            self._audio_protocol = data.get("audio_protocol", "udp")
            self._setup_udp(
                data.get("multicast_group", ""),
                data.get("multicast_port", 0),
            )
            self._start_audio()
            if self._on_room_state_change:
                self._on_room_state_change(self._room_state)
        return ok, msg, data

    def invite_to_room(self, target: str):
        if not self._room_id:
            return False, "您不在聊天室中"
        ok, msg, _ = self._request_response(
            encode_room_invite(self._room_id, self._username, target)
        )
        return ok, msg

    def join_room(self, room_id: str):
        ok, msg, data = self._request_response(encode_room_join(room_id, self._username))
        if ok:
            self._room_id = data.get("room_id", room_id)
            self._my_position = data.get("position", -1)
            self._is_creator = data.get("creator", "") == self._username
            self._room_state = RoomState.IN_ROOM
            self._audio_protocol = data.get("audio_protocol", "udp")
            self._setup_udp(
                data.get("multicast_group", ""),
                data.get("multicast_port", 0),
            )
            self._start_audio()
            if self._on_room_state_change:
                self._on_room_state_change(self._room_state)
        return ok, msg, data

    def leave_room(self) -> None:
        if not self._room_id:
            return
        room_id = self._room_id
        self._send_raw(encode_room_leave(room_id, self._username))
        self._stop_audio()
        self._teardown_udp()
        self._clear_room_state()

    def dismiss_room(self) -> None:
        if not self._room_id:
            return
        room_id = self._room_id
        self._send_raw(encode_room_dismiss(room_id, self._username))
        self._stop_audio()
        self._teardown_udp()
        self._clear_room_state()

    def _setup_udp(self, multicast_group: str, multicast_port: int) -> None:
        self._teardown_udp()
        if not multicast_group or not multicast_port:
            raise RuntimeError("服务器未返回有效的组播信息")

        interface_ip = resolve_multicast_interface_ip(self._host)
        send_interface_ip = interface_ip
        if interface_ip.startswith("127."):
            send_interface_ip = "0.0.0.0"

        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        recv_sock.bind(("", multicast_port))

        membership = socket.inet_aton(multicast_group) + socket.inet_aton(interface_ip)
        try:
            recv_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        except OSError:
            interface_ip = "0.0.0.0"
            membership = socket.inet_aton(multicast_group) + socket.inet_aton(interface_ip)
            recv_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        recv_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        recv_sock.settimeout(1.0)

        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, MULTICAST_TTL)
        send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        if send_interface_ip != "0.0.0.0":
            try:
                send_sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(send_interface_ip),
                )
            except OSError:
                pass

        self._multicast_group = multicast_group
        self._multicast_port = multicast_port
        self._multicast_interface_ip = interface_ip
        self._multicast_recv_sock = recv_sock
        self._multicast_send_sock = send_sock
        self._reset_remote_audio_streams()
        print(
            f"[Client] Multicast setup: group={multicast_group}:{multicast_port}, "
            f"iface={interface_ip}"
        )

    def _teardown_udp(self) -> None:
        self._packet_sequence = 0
        self._previous_capture_frame = None
        self._previous_capture_timestamp_ms = None
        self._reset_remote_audio_streams()

        if self._multicast_recv_sock and self._multicast_group:
            membership = socket.inet_aton(self._multicast_group) + socket.inet_aton(
                self._multicast_interface_ip or "0.0.0.0"
            )
            try:
                self._multicast_recv_sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_DROP_MEMBERSHIP,
                    membership,
                )
            except OSError:
                pass

        for sock in (self._multicast_recv_sock, self._multicast_send_sock):
            if not sock:
                continue
            try:
                sock.close()
            except OSError:
                pass

        self._multicast_recv_sock = None
        self._multicast_send_sock = None
        self._multicast_group = ""
        self._multicast_port = 0
        self._multicast_interface_ip = "0.0.0.0"

    def _start_audio(self) -> None:
        if not self._p or self._in_stream is not None:
            return
        try:
            self._in_stream = self._p.open(
                format=AUDIO_FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as exc:
            print(f"[Client] Open input device failed: {exc}")
            return

        self._is_streaming = True
        self._packet_sequence = 0
        self._previous_capture_frame = None
        self._previous_capture_timestamp_ms = None
        self._reset_remote_audio_streams()

        threading.Thread(target=self._send_audio_loop, daemon=True).start()
        if self._audio_protocol == "udp" and self._multicast_recv_sock:
            threading.Thread(target=self._udp_recv_audio_loop, daemon=True).start()
            threading.Thread(target=self._playout_audio_loop, daemon=True).start()

    def _stop_audio(self) -> None:
        self._is_streaming = False
        time.sleep(0.1)
        try:
            if self._in_stream:
                self._in_stream.stop_stream()
                self._in_stream.close()
        except Exception:
            pass
        self._in_stream = None
        self._reset_remote_audio_streams()

    def _reset_remote_audio_streams(self) -> None:
        with self._audio_streams_lock:
            self._remote_audio_streams = {}
            self._audio_monitor_snapshot = {}
            self._main_speaker_sender_id = None

    def _get_or_create_remote_stream(self, sender_id: bytes) -> RemoteAudioStream:
        with self._audio_streams_lock:
            stream = self._remote_audio_streams.get(sender_id)
            if stream is None:
                stream = RemoteAudioStream(
                    sender_id=sender_id,
                    buffer=RedundantJitterBuffer(
                        frame_bytes=self._frame_bytes,
                        frame_duration_ms=self._frame_duration_ms,
                    ),
                    display_name=sender_id.hex()[:8],
                )
                self._remote_audio_streams[sender_id] = stream
            return stream

    def _send_audio_loop(self) -> None:
        while self._is_streaming and self._room_id:
            try:
                data = self._in_stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception:
                break
            if not data:
                continue

            timestamp_ms = int(time.time() * 1000)
            if (
                self._audio_protocol == "udp"
                and self._multicast_send_sock
                and self._multicast_group
                and self._multicast_port
            ):
                try:
                    packet = pack_audio_packet(
                        sender_id=self._sender_id,
                        sequence=self._packet_sequence,
                        timestamp_ms=timestamp_ms,
                        primary_payload=data,
                        redundant_sequence=(
                            self._packet_sequence - 1
                            if self._previous_capture_frame is not None
                            else None
                        ),
                        redundant_timestamp_ms=self._previous_capture_timestamp_ms,
                        redundant_payload=self._previous_capture_frame or b"",
                    )
                    self._multicast_send_sock.sendto(
                        packet,
                        (self._multicast_group, self._multicast_port),
                    )
                except OSError:
                    break
            else:
                try:
                    if not self._sock:
                        break
                    message = encode_room_audio_chunk(self._room_id, self._username, data)
                    self._sock.sendall((message + MESSAGE_DELIMITER).encode("utf-8"))
                except OSError:
                    break

            self._previous_capture_frame = data
            self._previous_capture_timestamp_ms = timestamp_ms
            self._packet_sequence += 1
            time.sleep(0.001)

    def _udp_recv_audio_loop(self) -> None:
        while self._is_streaming and self._multicast_recv_sock:
            try:
                payload, _ = self._multicast_recv_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                packet = unpack_audio_packet(payload)
            except ValueError:
                continue
            if packet.sender_id == self._sender_id:
                continue

            stream = self._get_or_create_remote_stream(packet.sender_id)
            stream.buffer.push(packet)
            stream.last_seen_monotonic = time.monotonic()
            stream.last_packet_timestamp_ms = packet.timestamp_ms

    def _playout_audio_loop(self) -> None:
        next_deadline = time.monotonic()
        while self._is_streaming:
            now = time.monotonic()
            if now < next_deadline:
                time.sleep(min(0.005, next_deadline - now))
                continue

            route_frames = self._collect_route_frames()
            main_speaker = self._select_main_speaker(route_frames)
            self._update_main_speaker(main_speaker)

            weighted_frames = []
            for item in route_frames:
                stream = item["stream"]
                weight = self._get_mix_weight(stream, item["voice_rms"], main_speaker)
                weighted_frames.append((item["frame"], weight))

            mixed_frame = mix_pcm16_frames(weighted_frames)
            if mixed_frame and self._out_stream:
                try:
                    self._out_stream.write(mixed_frame, exception_on_underflow=False)
                except Exception:
                    pass

            self._prune_remote_audio_streams(now)
            next_deadline += self._frame_interval
            if next_deadline < now - self._frame_interval:
                next_deadline = now + self._frame_interval

    def _collect_route_frames(self) -> List[dict]:
        with self._audio_streams_lock:
            streams = list(self._remote_audio_streams.values())

        route_frames: List[dict] = []
        snapshot: Dict[str, dict] = {}
        for stream in streams:
            buffered_frame = stream.buffer.pop()
            if buffered_frame is None:
                continue

            stream.last_played_timestamp_ms = buffered_frame.timestamp_ms
            raw_frame = buffered_frame.payload
            raw_metrics = analyze_noise(raw_frame)
            self._update_noise_tracking(stream, raw_metrics)

            processed_frame = raw_frame
            if stream.low_frequency_noise:
                processed_frame = high_pass_filter_pcm16(
                    raw_frame,
                    stream.high_pass_state,
                    sample_rate=SAMPLE_RATE,
                )

            voice_rms = pcm16_rms(processed_frame)
            if stream.smoothed_voice_rms == 0.0:
                stream.smoothed_voice_rms = voice_rms
            else:
                stream.smoothed_voice_rms = stream.smoothed_voice_rms * 0.72 + voice_rms * 0.28

            route_frames.append(
                {
                    "stream": stream,
                    "frame": processed_frame,
                    "voice_rms": voice_rms,
                    "timestamp_ms": buffered_frame.timestamp_ms,
                }
            )
            snapshot[stream.display_name] = {
                "sender_id": stream.display_name,
                "rms": round(stream.smoothed_raw_rms, 2),
                "voice_rms": round(stream.smoothed_voice_rms, 2),
                "low_frequency_ratio": round(stream.smoothed_low_frequency_ratio, 4),
                "zero_crossing_rate": round(stream.smoothed_zero_crossing_rate, 4),
                "noise_filtered": stream.low_frequency_noise,
                "last_packet_timestamp_ms": stream.last_packet_timestamp_ms,
                "last_played_timestamp_ms": stream.last_played_timestamp_ms,
                "is_main_speaker": False,
            }

        with self._audio_streams_lock:
            self._audio_monitor_snapshot = snapshot
        return route_frames

    def _update_noise_tracking(self, stream: RemoteAudioStream, metrics: NoiseMetrics) -> None:
        if stream.smoothed_raw_rms == 0.0:
            stream.smoothed_raw_rms = metrics.rms
            stream.smoothed_low_frequency_ratio = metrics.low_frequency_ratio
            stream.smoothed_zero_crossing_rate = metrics.zero_crossing_rate
        else:
            stream.smoothed_raw_rms = stream.smoothed_raw_rms * 0.74 + metrics.rms * 0.26
            stream.smoothed_low_frequency_ratio = (
                stream.smoothed_low_frequency_ratio * 0.72 + metrics.low_frequency_ratio * 0.28
            )
            stream.smoothed_zero_crossing_rate = (
                stream.smoothed_zero_crossing_rate * 0.72 + metrics.zero_crossing_rate * 0.28
            )

        smoothed_metrics = NoiseMetrics(
            rms=stream.smoothed_raw_rms,
            low_frequency_ratio=stream.smoothed_low_frequency_ratio,
            zero_crossing_rate=stream.smoothed_zero_crossing_rate,
        )
        noisy = has_low_frequency_noise(smoothed_metrics)
        if noisy:
            stream.noise_score = min(stream.noise_score + 1, 6)
        else:
            stream.noise_score = max(stream.noise_score - 1, 0)

        previous_state = stream.low_frequency_noise
        stream.low_frequency_noise = stream.noise_score >= 2
        if previous_state != stream.low_frequency_noise:
            if stream.low_frequency_noise:
                print(f"[Audio] Low-frequency noise detected on route {stream.display_name}")
            else:
                print(f"[Audio] Low-frequency noise cleared on route {stream.display_name}")

    def _select_main_speaker(self, route_frames: List[dict]) -> Optional[bytes]:
        if not route_frames:
            return None

        scores: Dict[bytes, float] = {}
        for item in route_frames:
            stream = item["stream"]
            score = stream.smoothed_voice_rms
            if stream.low_frequency_noise:
                score *= 0.88
            if score >= 220.0:
                scores[stream.sender_id] = score

        if not scores:
            return None

        current = self._main_speaker_sender_id
        if current in scores:
            current_score = scores[current]
            best_sender, best_score = max(scores.items(), key=lambda value: value[1])
            if best_sender != current and best_score < current_score * 1.15:
                return current
        return max(scores.items(), key=lambda value: value[1])[0]

    def _update_main_speaker(self, sender_id: Optional[bytes]) -> None:
        if sender_id == self._main_speaker_sender_id:
            self._mark_main_speaker(sender_id)
            return
        self._main_speaker_sender_id = sender_id
        self._mark_main_speaker(sender_id)
        if sender_id is None:
            return
        print(f"[Audio] Main speaker -> {sender_id.hex()[:8]}")

    def _mark_main_speaker(self, sender_id: Optional[bytes]) -> None:
        with self._audio_streams_lock:
            for snapshot in self._audio_monitor_snapshot.values():
                snapshot["is_main_speaker"] = snapshot["sender_id"] == (
                    "" if sender_id is None else sender_id.hex()[:8]
                )

    def _get_mix_weight(
        self,
        stream: RemoteAudioStream,
        voice_rms: float,
        main_speaker: Optional[bytes],
    ) -> float:
        if main_speaker is not None and stream.sender_id == main_speaker:
            weight = 1.0
        elif voice_rms >= 320.0:
            weight = 0.58
        elif voice_rms >= 160.0:
            weight = 0.36
        else:
            weight = 0.22

        if stream.low_frequency_noise:
            weight *= 0.72 if stream.sender_id != main_speaker else 0.9
        return weight

    def _prune_remote_audio_streams(self, now: float) -> None:
        with self._audio_streams_lock:
            stale_sender_ids = [
                sender_id
                for sender_id, stream in self._remote_audio_streams.items()
                if now - stream.last_seen_monotonic > 3.0
            ]
            for sender_id in stale_sender_ids:
                display_name = self._remote_audio_streams[sender_id].display_name
                self._remote_audio_streams.pop(sender_id, None)
                self._audio_monitor_snapshot.pop(display_name, None)
                if self._main_speaker_sender_id == sender_id:
                    self._main_speaker_sender_id = None

    def _send_raw(self, message: str) -> None:
        if not self._sock:
            raise RuntimeError("未连接服务器")
        self._sock.sendall((message + MESSAGE_DELIMITER).encode("utf-8"))

    def _recv_loop(self) -> None:
        buffer = ""
        while self._running and self._sock:
            try:
                data = self._sock.recv(4096)
            except OSError:
                break
            if not data:
                break
            buffer += data.decode("utf-8", errors="ignore")
            while MESSAGE_DELIMITER in buffer:
                line, buffer = buffer.split(MESSAGE_DELIMITER, 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if "success" in obj and "type" not in obj:
                    success = obj.get("success", False)
                    message = obj.get("message", "")
                    data_field = obj.get("data", {})
                    self._response_queue.put((success, message, data_field))
                    continue

                message_type = obj.get("type")
                if message_type:
                    self._handle_message(message_type, obj)

        self._running = False
        self._recv_thread_started = False

    def _handle_message(self, message_type: str, payload: dict) -> None:
        if message_type == "room_invite_notify":
            room_id = payload.get("room_id", "")
            inviter = payload.get("inviter", "")
            if self._on_room_invite:
                self._on_room_invite(room_id, inviter)

        elif message_type == "room_member_update":
            room_id = payload.get("room_id", "")
            members = payload.get("members", [])
            positions = payload.get("positions", {})
            self._cached_members = members
            self._cached_positions = positions
            if self._on_room_member_update:
                self._on_room_member_update(room_id, members, positions)

        elif message_type == "room_dismissed_notify":
            room_id = payload.get("room_id", "")
            if not self._room_id:
                return

            self._is_streaming = False
            self._teardown_udp()

            def _deferred_close_input() -> None:
                try:
                    if self._in_stream:
                        self._in_stream.stop_stream()
                        self._in_stream.close()
                except Exception:
                    pass
                self._in_stream = None

            threading.Thread(target=_deferred_close_input, daemon=True).start()
            self._clear_room_state()
            if self._on_room_dismissed:
                self._on_room_dismissed(room_id)

        elif message_type == "room_audio_chunk":
            b64 = payload.get("data", "")
            try:
                raw = base64.b64decode(b64)
            except Exception:
                return
            if self._out_stream:
                try:
                    self._out_stream.write(raw, exception_on_underflow=False)
                except Exception:
                    pass
