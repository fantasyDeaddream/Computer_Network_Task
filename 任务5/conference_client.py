"""
任务5 - 多方语音会议系统客户端

在任务4客户端基础上扩展，增加聊天室功能。
"""

from __future__ import annotations

import base64
import json
import math
import queue
import socket
import struct
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Dict, List, Optional
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
    DEFAULT_HOST,
    SAMPLE_RATE,
    CHANNELS,
    AUDIO_FORMAT,
    CHUNK_SIZE,
)

from conference_protocol import (
    encode_login,
    encode_logout,
    encode_contact_add,
    encode_contact_delete,
    encode_contact_update,
    encode_contact_list,
    encode_contact_search,
    encode_online_query,
    encode_room_create,
    encode_room_invite,
    encode_room_join,
    encode_room_leave,
    encode_room_dismiss,
    encode_room_audio_chunk,
    MESSAGE_DELIMITER,
)

DEFAULT_PORT = 8882


class RoomState:
    IDLE = "idle"
    IN_ROOM = "in_room"


class ConferenceClient:

    def __init__(
        self,
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        on_room_state_change=None,
        on_room_invite=None,
        on_room_member_update=None,
        on_room_dismissed=None,
    ):
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._username = ""

        self._room_id = ""
        self._room_state = RoomState.IDLE
        self._is_creator = False
        self._my_position = -1
        self._audio_protocol = "tcp"  # 当前聊天室的音频协议

        self._p = pyaudio.PyAudio() if pyaudio else None
        self._out_stream = None
        self._in_stream = None
        self._is_streaming = False
        self._recv_thread_started = False

        # 音频混音与滤波
        self._incoming_audio: Dict[str, deque[bytes]] = {}
        self._incoming_audio_lock = threading.Lock()
        self._playback_thread: Optional[threading.Thread] = None
        self._playback_active = False
        self._sender_volumes: Dict[str, float] = {}
        self._lowpass_state: Dict[str, float] = {}

        # UDP音频相关
        self._udp_sock: Optional[socket.socket] = None
        self._udp_server_addr: Optional[tuple] = None  # (host, udp_port)

        self._response_queue: queue.Queue = queue.Queue()

        # callbacks
        self._on_room_state_change = on_room_state_change
        self._on_room_invite = on_room_invite
        self._on_room_member_update = on_room_member_update
        self._on_room_dismissed = on_room_dismissed

        self._contacts: List[str] = []

        # 缓存最新的成员更新数据，解决 ChatRoomFrame 创建时机问题
        self._cached_members: List[dict] = []
        self._cached_positions: dict = {}

    @property
    def username(self):
        return self._username

    @property
    def room_id(self):
        return self._room_id

    @property
    def room_state(self):
        return self._room_state

    @property
    def is_creator(self):
        return self._is_creator

    @property
    def my_position(self):
        return self._my_position

    @property
    def audio_protocol(self):
        return self._audio_protocol

    # ---- connection ----

    def _ensure_connected(self) -> bool:
        if self._sock:
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((self._host, self._port))
            return True
        except Exception as e:
            print(f"[Client] Connect failed: {e}")
            self._sock = None
            return False

    def _start_recv_thread(self):
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
                except Exception as e:
                    print(f"[Client] Open output device failed: {e}")
            self._start_recv_thread()
        return success, message

    def _sync_wait_response(self, timeout=5.0):
        try:
            self._sock.settimeout(timeout)
            buf = ""
            while True:
                data = self._sock.recv(4096)
                if not data:
                    return False, "连接断开"
                buf += data.decode("utf-8", errors="ignore")
                if MESSAGE_DELIMITER in buf:
                    line, _ = buf.split(MESSAGE_DELIMITER, 1)
                    line = line.strip()
                    if line:
                        obj = json.loads(line)
                        return obj.get("success", False), obj.get("message", "")
        except socket.timeout:
            return False, "等待响应超时"
        except Exception as e:
            return False, f"网络错误: {e}"

    def logout(self):
        if self._sock and self._username:
            try:
                self._send_raw(encode_logout(self._username))
            except Exception:
                pass
        self.disconnect()
        self._username = ""

    def disconnect(self):
        self._running = False
        self._is_streaming = False
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
        except Exception:
            pass
        self._sock = None
        self._room_id = ""
        self._room_state = RoomState.IDLE
        self._audio_protocol = "tcp"

    # ---- contacts ----

    def _request_response(self, msg, timeout=5.0):
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except queue.Empty:
                break
        self._send_raw(msg)
        try:
            return self._response_queue.get(timeout=timeout)
        except queue.Empty:
            return False, "等待响应超时", {}

    def add_contact(self, contact_name):
        ok, msg, _ = self._request_response(
            encode_contact_add(self._username, contact_name)
        )
        return ok, msg

    def delete_contact(self, contact_name):
        ok, msg, _ = self._request_response(
            encode_contact_delete(self._username, contact_name)
        )
        return ok, msg

    def update_contact(self, old_name, new_name):
        ok, msg, _ = self._request_response(
            encode_contact_update(self._username, old_name, new_name)
        )
        return ok, msg

    def get_contacts(self):
        ok, msg, data = self._request_response(encode_contact_list(self._username))
        if ok:
            self._contacts = data.get("contacts", [])
            return self._contacts
        return []

    def search_contacts(self, keyword):
        ok, msg, data = self._request_response(
            encode_contact_search(self._username, keyword)
        )
        if ok:
            return data.get("contacts", [])
        return []

    def get_online_users(self) -> List[str]:
        """查询当前在线的用户列表"""
        ok, msg, data = self._request_response(encode_online_query(self._username))
        if ok:
            return data.get("online_users", [])
        return []

    # ---- room ----

    def create_room(self, audio_protocol: str = "tcp"):
        """创建聊天室

        Args:
            audio_protocol: 音频传输协议，"tcp" 或 "udp"
        """
        ok, msg, data = self._request_response(
            encode_room_create(self._username, audio_protocol)
        )
        if ok:
            self._room_id = data.get("room_id", "")
            self._my_position = data.get("position", -1)
            self._is_creator = True
            self._room_state = RoomState.IN_ROOM
            self._audio_protocol = data.get("audio_protocol", "tcp")
            if self._audio_protocol == "udp":
                udp_port = data.get("udp_port", 0)
                self._setup_udp(udp_port)
            self._start_audio()
        return ok, msg, data

    def invite_to_room(self, target):
        if not self._room_id:
            return False, "您不在聊天室中"
        ok, msg, _ = self._request_response(
            encode_room_invite(self._room_id, self._username, target)
        )
        return ok, msg

    def join_room(self, room_id):
        ok, msg, data = self._request_response(
            encode_room_join(room_id, self._username)
        )
        if ok:
            self._room_id = data.get("room_id", room_id)
            self._my_position = data.get("position", -1)
            self._is_creator = data.get("creator", "") == self._username
            self._room_state = RoomState.IN_ROOM
            self._audio_protocol = data.get("audio_protocol", "tcp")
            if self._audio_protocol == "udp":
                udp_port = data.get("udp_port", 0)
                self._setup_udp(udp_port)
            self._start_audio()
        return ok, msg, data

    def leave_room(self):
        if not self._room_id:
            return
        rid = self._room_id
        self._send_raw(encode_room_leave(rid, self._username))
        self._stop_audio()
        self._teardown_udp()
        self._room_id = ""
        self._room_state = RoomState.IDLE
        self._is_creator = False
        self._my_position = -1
        self._audio_protocol = "tcp"

    def dismiss_room(self):
        if not self._room_id:
            return
        rid = self._room_id
        self._send_raw(encode_room_dismiss(rid, self._username))
        self._stop_audio()
        self._teardown_udp()
        self._room_id = ""
        self._room_state = RoomState.IDLE
        self._is_creator = False
        self._my_position = -1
        self._audio_protocol = "tcp"

    # ---- UDP ----

    def _setup_udp(self, server_udp_port: int) -> None:
        """建立UDP socket并记录服务器UDP地址"""
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.bind(("", 0))  # 绑定随机本地端口
            self._udp_server_addr = (self._host, server_udp_port)
            print(
                f"[Client] UDP setup: server={self._udp_server_addr}, "
                f"local={self._udp_sock.getsockname()}"
            )
        except Exception as e:
            print(f"[Client] UDP setup failed: {e}")
            self._udp_sock = None
            self._udp_server_addr = None

    def _teardown_udp(self) -> None:
        """关闭UDP socket"""
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None
        self._udp_server_addr = None

    # ---- audio ----

    def _start_audio(self):
        if not self._p:
            return
        if self._in_stream is not None:
            return
        try:
            self._in_stream = self._p.open(
                format=AUDIO_FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as e:
            print(f"[Client] Open input device failed: {e}")
            return
        self._is_streaming = True
        threading.Thread(target=self._send_audio_loop, daemon=True).start()
        # UDP模式下启动UDP接收线程
        if self._audio_protocol == "udp" and self._udp_sock:
            threading.Thread(target=self._udp_recv_audio_loop, daemon=True).start()
        self._start_playback_thread()

    def _stop_audio(self):
        self._is_streaming = False
        self._playback_active = False
        time.sleep(0.1)
        try:
            if self._in_stream:
                self._in_stream.stop_stream()
                self._in_stream.close()
        except Exception:
            pass
        self._in_stream = None
        with self._incoming_audio_lock:
            self._incoming_audio.clear()
            self._lowpass_state.clear()

    def _send_audio_loop(self):
        while self._is_streaming and self._room_id:
            try:
                data = self._in_stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception:
                break
            if not data:
                continue

            if (
                self._audio_protocol == "udp"
                and self._udp_sock
                and self._udp_server_addr
            ):
                # UDP模式：通过UDP发送音频
                try:
                    # 前32字节为用户名，其余为音频数据
                    username_bytes = self._username.encode("utf-8")[:32].ljust(
                        32, b"\x00"
                    )
                    self._udp_sock.sendto(username_bytes + data, self._udp_server_addr)
                except Exception:
                    break
            else:
                # TCP模式：通过TCP发送音频
                try:
                    if not self._sock:
                        break
                    msg = encode_room_audio_chunk(self._room_id, self._username, data)
                    self._sock.sendall((msg + MESSAGE_DELIMITER).encode("utf-8"))
                except Exception:
                    break
            time.sleep(0.001)

    def _udp_recv_audio_loop(self):
        """UDP模式下接收音频数据并播放"""
        while self._is_streaming and self._udp_sock:
            try:
                self._udp_sock.settimeout(1.0)
                data, _ = self._udp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if data and self._out_stream:
                try:
                    if len(data) >= 32:
                        sender = data[:32].rstrip(b"\x00").decode("utf-8", errors="ignore")
                        audio_data = data[32:]
                        if audio_data:
                            self._enqueue_received_audio(sender, audio_data)
                    else:
                        self._out_stream.write(data, exception_on_underflow=False)
                except Exception:
                    pass

    # ---- network ----

    def _send_raw(self, msg):
        if not self._sock:
            raise RuntimeError("未连接服务器")
        self._sock.sendall((msg + MESSAGE_DELIMITER).encode("utf-8"))

    def _recv_loop(self):
        buf = ""
        while self._running and self._sock:
            try:
                data = self._sock.recv(4096)
            except Exception:
                break
            if not data:
                break
            buf += data.decode("utf-8", errors="ignore")
            while MESSAGE_DELIMITER in buf:
                line, buf = buf.split(MESSAGE_DELIMITER, 1)
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

                t = obj.get("type")
                if t:
                    self._handle_message(t, obj)

        self._running = False
        self._recv_thread_started = False

    def _handle_message(self, mtype, payload):
        if mtype == "room_invite_notify":
            room_id = payload.get("room_id", "")
            inviter = payload.get("inviter", "")
            if self._on_room_invite:
                self._on_room_invite(room_id, inviter)

        elif mtype == "room_member_update":
            room_id = payload.get("room_id", "")
            members = payload.get("members", [])
            positions = payload.get("positions", {})
            # 缓存最新的成员数据
            self._cached_members = members
            self._cached_positions = positions
            if self._on_room_member_update:
                self._on_room_member_update(room_id, members, positions)

        elif mtype == "room_dismissed_notify":
            room_id = payload.get("room_id", "")
            # 如果当前已不在聊天室中（创建者自己解散时已清理），跳过处理
            if not self._room_id:
                return
            # 在接收线程中不调用 _stop_audio()（含 time.sleep），
            # 仅标记停止并清理状态，音频线程会自行退出
            self._is_streaming = False
            self._teardown_udp()

            # 延迟关闭 _in_stream，避免阻塞接收线程
            def _deferred_close_input():
                try:
                    if self._in_stream:
                        self._in_stream.stop_stream()
                        self._in_stream.close()
                except Exception:
                    pass
                self._in_stream = None

            threading.Thread(target=_deferred_close_input, daemon=True).start()
            self._room_id = ""
            self._room_state = RoomState.IDLE
            self._is_creator = False
            self._my_position = -1
            self._audio_protocol = "tcp"
            if self._on_room_dismissed:
                self._on_room_dismissed(room_id)

        elif mtype == "room_audio_chunk":
            sender = payload.get("sender", "")
            b64 = payload.get("data", "")
            try:
                raw = base64.b64decode(b64)
                if sender and raw:
                    self._enqueue_received_audio(sender, raw)
            except Exception:
                pass

    def set_sender_volume(self, sender: str, volume: float) -> None:
        with self._incoming_audio_lock:
            self._sender_volumes[sender] = max(0.0, min(volume, 2.0))

    def _enqueue_received_audio(self, sender: str, raw: bytes) -> None:
        if not raw:
            return
        filtered = self._apply_noise_lowpass(sender, raw)
        with self._incoming_audio_lock:
            queue = self._incoming_audio.setdefault(sender, deque())
            if len(queue) > 50:
                queue.popleft()
            queue.append(filtered)

    def _apply_noise_lowpass(self, sender: str, raw: bytes) -> bytes:
        if len(raw) < 2:
            return raw
        sample_count = len(raw) // 2
        try:
            samples = list(struct.unpack(f"<{sample_count}h", raw))
        except Exception:
            return raw

        rms = math.sqrt(sum(s * s for s in samples) / sample_count)
        cutoff = 2800 if rms < 300 else 6500
        prev = self._lowpass_state.get(sender, 0.0)
        dt = 1.0 / SAMPLE_RATE
        rc = 1.0 / (2 * math.pi * cutoff)
        alpha = dt / (rc + dt)

        volume = self._sender_volumes.get(sender, 1.0)
        filtered = []
        for sample in samples:
            prev += alpha * (sample - prev)
            scaled = int(round(prev * volume))
            if scaled > 32767:
                scaled = 32767
            elif scaled < -32768:
                scaled = -32768
            filtered.append(scaled)

        self._lowpass_state[sender] = prev
        try:
            return struct.pack(f"<{len(filtered)}h", *filtered)
        except Exception:
            return raw

    def _start_playback_thread(self) -> None:
        if self._playback_thread and self._playback_thread.is_alive():
            return
        self._playback_active = True
        self._playback_thread = threading.Thread(
            target=self._playback_loop, daemon=True
        )
        self._playback_thread.start()

    def _playback_loop(self) -> None:
        silence = b"\x00" * (CHUNK_SIZE * 2)
        while self._playback_active and self._out_stream:
            chunk = self._mix_next_chunk()
            if chunk:
                try:
                    self._out_stream.write(chunk, exception_on_underflow=False)
                except Exception:
                    pass
            else:
                try:
                    self._out_stream.write(silence, exception_on_underflow=False)
                except Exception:
                    pass
                time.sleep(0.01)

    def _mix_next_chunk(self) -> Optional[bytes]:
        with self._incoming_audio_lock:
            if not self._incoming_audio:
                return None
            active_chunks = []
            for sender, queue in list(self._incoming_audio.items()):
                if queue:
                    chunk = queue.popleft()
                    try:
                        samples = struct.unpack(f"<{len(chunk) // 2}h", chunk)
                    except Exception:
                        samples = None
                    if samples:
                        active_chunks.append(samples)
            if not active_chunks:
                return None

        max_len = max(len(samples) for samples in active_chunks)
        mixed = [0] * max_len
        for samples in active_chunks:
            for idx, sample in enumerate(samples):
                mixed[idx] += sample

        max_val = max(abs(value) for value in mixed) or 1
        if max_val > 32767:
            scale = 32767.0 / max_val
            mixed = [int(round(value * scale)) for value in mixed]

        try:
            return struct.pack(f"<{len(mixed)}h", *mixed)
        except Exception:
            return None
