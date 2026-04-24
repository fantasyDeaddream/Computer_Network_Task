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
from array import array
from collections import deque
from dataclasses import dataclass
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
from audio_adaptive import (
    AudioFormat,
    CANONICAL_AUDIO_FORMAT,
    ReframingAudioTranscoder,
    get_profile_name_for_format,
)

from conference_protocol import (
    decode_media_packet,
    encode_audio_frame,
    encode_login,
    encode_logout,
    encode_contact_add,
    encode_contact_delete,
    encode_contact_update,
    encode_contact_list,
    encode_contact_search,
    encode_call_accept,
    encode_call_hangup,
    encode_call_invite,
    encode_call_reject,
    encode_direct_path_seen,
    encode_media_probe,
    encode_media_stop,
    encode_online_query,
    encode_room_create,
    encode_room_invite,
    encode_room_join,
    encode_room_leave,
    encode_room_dismiss,
    encode_room_audio_chunk,
    encode_quality_report,
    encode_udp_audio_packet,
    decode_udp_audio_packet,
    MESSAGE_DELIMITER,
)
from emodel import AudioQualityMonitor

DEFAULT_PORT = 8882
CALL_MEDIA_CHUNK_SIZE = 320
CALL_FRAME_DURATION_SEC = CALL_MEDIA_CHUNK_SIZE / float(SAMPLE_RATE)
CALL_PLAYBACK_DELAY_SEC = CALL_FRAME_DURATION_SEC * 3
CALL_MISSING_GRACE_SEC = CALL_FRAME_DURATION_SEC * 0.75
MAX_CALL_JITTER_BUFFER_FRAMES = 64
NEGOTIATION_PROBE_INTERVAL_SEC = 0.20


class RoomState:
    IDLE = "idle"
    IN_ROOM = "in_room"


class CallState:
    IDLE = "idle"
    CALLING = "calling"
    RINGING = "ringing"
    CONNECTING = "connecting"
    IN_CALL = "in_call"
    ENDED = "ended"


@dataclass
class MediaFrame:
    sequence: int
    timestamp_ms: int
    stream_id: str
    payload: bytes
    received_at: float


class ConferenceClient:

    def __init__(
        self,
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        on_room_state_change=None,
        on_room_invite=None,
        on_room_member_update=None,
        on_room_dismissed=None,
        on_quality_update=None,
        on_call_state_change=None,
    ):
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._username = ""
        self._server_ip = ""
        self._local_ip = ""

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
        self._volume_levels: Dict[str, float] = {}
        self._on_volume_update: Optional[Callable[[Dict[str, float]], None]] = None
        self._receive_transcoders: Dict[str, ReframingAudioTranscoder] = {}

        # UDP音频相关
        self._udp_sock: Optional[socket.socket] = None
        self._udp_server_addr: Optional[tuple] = None  # (host, udp_port)
        self._audio_seq = 0
        self._quality_monitor = AudioQualityMonitor()
        self._last_quality_callback = 0.0
        self._last_quality_report_sent = 0.0
        self._active_receive_format = CANONICAL_AUDIO_FORMAT
        self._active_receive_profile = "wideband"

        self._response_queue: queue.Queue = queue.Queue()
        self._send_lock = threading.Lock()

        self._call_media_sock: Optional[socket.socket] = None
        self._call_media_port = 0
        self._call_media_thread: Optional[threading.Thread] = None
        self._call_playback_thread: Optional[threading.Thread] = None
        self._call_send_thread: Optional[threading.Thread] = None
        self._call_probe_thread: Optional[threading.Thread] = None
        self._call_probe_stop = threading.Event()
        self._call_sending = False
        self._call_in_stream = None
        self._call_state = CallState.IDLE
        self._in_call_with = ""
        self._call_id = ""
        self._session_mode = ""
        self._peer_media_addr: Optional[tuple[str, int]] = None
        self._relay_media_addr: Optional[tuple[str, int]] = None
        self._call_stream_id = uuid.uuid4().hex
        self._first_packet_path_logged = False
        self._direct_path_reported = False
        self._call_jitter_lock = threading.Lock()
        self._call_jitter_buffer: Dict[int, MediaFrame] = {}
        self._call_expected_sequence: Optional[int] = None
        self._call_next_play_time = 0.0
        self._call_remote_stream_id = ""
        self._call_last_played_frame: Optional[bytes] = None

        # callbacks
        self._on_room_state_change = on_room_state_change
        self._on_room_invite = on_room_invite
        self._on_room_member_update = on_room_member_update
        self._on_room_dismissed = on_room_dismissed
        self._on_quality_update = on_quality_update
        self._on_call_state_change = on_call_state_change

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

    @property
    def call_state(self) -> str:
        return self._call_state

    @property
    def in_call_with(self) -> str:
        return self._in_call_with

    @property
    def session_mode(self) -> str:
        return self._session_mode

    @property
    def is_call_sending(self) -> bool:
        return self._call_sending

    def get_quality_reports(self) -> Dict[str, dict]:
        return self._quality_monitor.get_reports()

    def get_volume_levels(self) -> Dict[str, float]:
        return self._volume_levels.copy()

    def get_active_receive_audio_description(self) -> str:
        return f"{self._active_receive_profile}: {self._active_receive_format.describe()}"

    def reset_quality_stats(self) -> None:
        self._quality_monitor.reset()
        self._last_quality_callback = 0.0
        self._last_quality_report_sent = 0.0

    def prune_room_member_state(self, active_members: List[str]) -> None:
        active = set(active_members)
        with self._incoming_audio_lock:
            stale = [
                sender for sender in list(self._incoming_audio.keys()) if sender not in active
            ]
            for sender in stale:
                self._incoming_audio.pop(sender, None)
                self._lowpass_state.pop(sender, None)
                self._volume_levels.pop(sender, None)
                self._receive_transcoders.pop(sender, None)
                self._sender_volumes.pop(sender, None)
        self._quality_monitor.prune_senders(active)

    def _set_call_state(self, state: str, peer: str) -> None:
        self._call_state = state
        if state in (CallState.IDLE, CallState.ENDED):
            self._call_sending = False
        if self._on_call_state_change:
            self._on_call_state_change(state, peer)

    def _emit_call_state(self) -> None:
        if self._on_call_state_change:
            self._on_call_state_change(self._call_state, self._in_call_with)

    def _reset_call_jitter_buffer(self) -> None:
        with self._call_jitter_lock:
            self._call_jitter_buffer.clear()
            self._call_expected_sequence = None
            self._call_next_play_time = 0.0
            self._call_remote_stream_id = ""
            self._call_last_played_frame = None

    def _setup_private_call_media(self) -> None:
        self._teardown_private_call_media()
        if not self._sock:
            return
        try:
            self._server_ip = self._sock.getpeername()[0]
            self._local_ip = self._sock.getsockname()[0]
            self._call_media_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._call_media_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._call_media_sock.settimeout(0.3)
            bind_host = self._local_ip if self._local_ip else ""
            self._call_media_sock.bind((bind_host, 0))
            self._call_media_port = int(self._call_media_sock.getsockname()[1])
        except Exception as exc:
            print(f"[Client] Private call UDP setup failed: {exc}")
            self._call_media_sock = None
            self._call_media_port = 0

    def _teardown_private_call_media(self) -> None:
        if self._call_media_sock:
            try:
                self._call_media_sock.close()
            except Exception:
                pass
        self._call_media_sock = None
        self._call_media_port = 0

    def _start_private_call_threads(self) -> None:
        if self._call_media_sock and (
            not self._call_media_thread or not self._call_media_thread.is_alive()
        ):
            self._call_media_thread = threading.Thread(
                target=self._recv_call_media_loop, daemon=True
            )
            self._call_media_thread.start()
        if not self._call_playback_thread or not self._call_playback_thread.is_alive():
            self._call_playback_thread = threading.Thread(
                target=self._private_call_playback_loop, daemon=True
            )
            self._call_playback_thread.start()

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
        self._setup_private_call_media()
        self._send_raw(
            encode_login(username, self._call_media_port, self._local_ip)
        )
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
            self._start_private_call_threads()
        else:
            self._teardown_private_call_media()
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
        if self._sock and self._in_call_with:
            try:
                self._send_raw(encode_call_hangup(self._in_call_with))
            except Exception:
                pass
        self._clear_private_call_session(reset_state=True)
        self._stop_audio()
        self._teardown_udp()
        self.reset_quality_stats()
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
        self._teardown_private_call_media()
        self._room_id = ""
        self._room_state = RoomState.IDLE
        self._audio_protocol = "tcp"
        self._audio_seq = 0
        self._active_receive_format = CANONICAL_AUDIO_FORMAT
        self._active_receive_profile = "wideband"
        self._set_call_state(CallState.IDLE, "")

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

    # ---- private call ----

    def call(self, target: str) -> tuple[bool, str]:
        target = target.strip()
        if not target:
            return False, "请输入要呼叫的用户名"
        if self._room_state == RoomState.IN_ROOM:
            return False, "请先退出聊天室"
        if not self._call_media_sock or self._call_media_port <= 0:
            return False, "当前客户端未准备好私聊语音 UDP 通道"
        if self._call_state not in (CallState.IDLE, CallState.ENDED):
            return False, "当前已有私聊语音会话"
        self._in_call_with = target
        self._set_call_state(CallState.CALLING, target)
        self._send_raw(encode_call_invite(target))
        return True, "呼叫已发送"

    def accept_call(self, caller: str = "") -> tuple[bool, str]:
        target = caller.strip() or self._in_call_with
        if not target:
            return False, "没有待接听的来电"
        if self._room_state == RoomState.IN_ROOM:
            return False, "请先退出聊天室"
        if self._call_state != CallState.RINGING:
            return False, "当前没有待接听的来电"
        self._in_call_with = target
        self._set_call_state(CallState.CONNECTING, target)
        self._send_raw(encode_call_accept(target))
        return True, "正在建立连接"

    def reject_call(self, caller: str = "") -> tuple[bool, str]:
        target = caller.strip() or self._in_call_with
        if not target:
            return False, "没有待拒绝的来电"
        self._send_raw(encode_call_reject(target, "Call rejected"))
        self._clear_private_call_session(reset_state=True)
        self._set_call_state(CallState.IDLE, "")
        return True, "已拒绝来电"

    def hangup_call(self) -> tuple[bool, str]:
        peer = self._in_call_with
        if self._sock and peer:
            try:
                self._send_raw(encode_call_hangup(peer))
            except Exception:
                pass
        self._clear_private_call_session(reset_state=True)
        self._set_call_state(CallState.ENDED, "")
        threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()
        return True, "通话已结束"

    # ---- room ----

    def create_room(self, audio_protocol: str = "tcp"):
        """创建聊天室

        Args:
            audio_protocol: 音频传输协议，"tcp" 或 "udp"
        """
        if self._call_state not in (CallState.IDLE, CallState.ENDED):
            return False, "请先结束当前私聊语音", {}
        ok, msg, data = self._request_response(
            encode_room_create(self._username, audio_protocol)
        )
        if ok:
            self._room_id = data.get("room_id", "")
            self._my_position = data.get("position", -1)
            self._is_creator = True
            self._room_state = RoomState.IN_ROOM
            self._audio_protocol = data.get("audio_protocol", "tcp")
            self._audio_seq = 0
            self.reset_quality_stats()
            self._active_receive_format = AudioFormat.from_payload(
                data.get("audio_format"), CANONICAL_AUDIO_FORMAT
            )
            self._active_receive_profile = data.get("adaptive_profile", "wideband")
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
        if self._call_state not in (CallState.IDLE, CallState.ENDED):
            return False, "请先结束当前私聊语音", {}
        ok, msg, data = self._request_response(
            encode_room_join(room_id, self._username)
        )
        if ok:
            self._room_id = data.get("room_id", room_id)
            self._my_position = data.get("position", -1)
            self._is_creator = data.get("creator", "") == self._username
            self._room_state = RoomState.IN_ROOM
            self._audio_protocol = data.get("audio_protocol", "tcp")
            self._audio_seq = 0
            self.reset_quality_stats()
            self._active_receive_format = AudioFormat.from_payload(
                data.get("audio_format"), CANONICAL_AUDIO_FORMAT
            )
            self._active_receive_profile = data.get("adaptive_profile", "wideband")
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
        self._audio_seq = 0
        self.reset_quality_stats()
        self._active_receive_format = CANONICAL_AUDIO_FORMAT
        self._active_receive_profile = "wideband"

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
        self._audio_seq = 0
        self.reset_quality_stats()
        self._active_receive_format = CANONICAL_AUDIO_FORMAT
        self._active_receive_profile = "wideband"

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
            self._volume_levels.clear()
            self._receive_transcoders.clear()

    def _next_audio_metadata(self) -> tuple[int, int]:
        seq = self._audio_seq
        self._audio_seq = (self._audio_seq + 1) & 0xFFFFFFFF
        timestamp_ms = int(time.time() * 1000)
        return seq, timestamp_ms

    def _summarize_network_quality(self) -> Optional[dict]:
        reports = [
            report
            for sender, report in self.get_quality_reports().items()
            if sender != self._username and report.get("last_seen_age_s", 999.0) <= 5.0
        ]
        if not reports:
            return None
        count = len(reports)
        return {
            "delay_ms": sum(report.get("delay_ms", 0.0) for report in reports) / count,
            "jitter_ms": max(report.get("jitter_ms", 0.0) for report in reports),
            "packet_loss_percent": max(
                report.get("packet_loss_percent", 0.0) for report in reports
            ),
            "sample_count": count,
        }

    def _maybe_send_quality_report(self, now: float) -> None:
        if not self._room_id or not self._username or not self._sock:
            return
        if now - self._last_quality_report_sent < 1.0:
            return
        summary = self._summarize_network_quality()
        if not summary:
            return
        self._last_quality_report_sent = now
        try:
            self._send_raw(
                encode_quality_report(
                    self._room_id,
                    self._username,
                    summary["delay_ms"],
                    summary["jitter_ms"],
                    summary["packet_loss_percent"],
                    summary["sample_count"],
                )
            )
        except Exception:
            pass

    def _update_receive_audio_profile(
        self, audio_format: Optional[dict], profile: Optional[str] = None
    ) -> AudioFormat:
        receive_format = AudioFormat.from_payload(audio_format, CANONICAL_AUDIO_FORMAT)
        self._active_receive_format = receive_format
        self._active_receive_profile = (
            profile if profile else get_profile_name_for_format(receive_format)
        )
        return receive_format

    def _record_audio_quality(
        self, sender: str, seq: Optional[int], timestamp_ms: Optional[int]
    ) -> None:
        if not sender:
            return
        self._quality_monitor.observe_packet(sender, seq, timestamp_ms)
        now = time.time()
        if self._on_quality_update and now - self._last_quality_callback >= 1.0:
            self._last_quality_callback = now
            self._on_quality_update(self.get_quality_reports())
        self._maybe_send_quality_report(now)

    def _send_audio_loop(self):
        while self._is_streaming and self._room_id:
            try:
                data = self._in_stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception:
                break
            if not data:
                continue
            seq, timestamp_ms = self._next_audio_metadata()

            if (
                self._audio_protocol == "udp"
                and self._udp_sock
                and self._udp_server_addr
            ):
                # UDP模式：通过UDP发送音频
                try:
                    packet = encode_udp_audio_packet(
                        self._username,
                        data,
                        seq,
                        timestamp_ms,
                        CANONICAL_AUDIO_FORMAT.to_payload(),
                    )
                    self._udp_sock.sendto(packet, self._udp_server_addr)
                except Exception:
                    break
            else:
                # TCP模式：通过TCP发送音频
                try:
                    if not self._sock:
                        break
                    msg = encode_room_audio_chunk(
                        self._room_id,
                        self._username,
                        data,
                        seq,
                        timestamp_ms,
                        audio_format=CANONICAL_AUDIO_FORMAT.to_payload(),
                    )
                    self._send_raw(msg)
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
                    sender, seq, timestamp_ms, audio_data, audio_format = decode_udp_audio_packet(data)
                    if audio_data:
                        receive_format = self._update_receive_audio_profile(audio_format)
                        self._record_audio_quality(sender, seq, timestamp_ms)
                        self._enqueue_received_audio(sender, audio_data, receive_format)
                except Exception:
                    pass

    # ---- private call audio ----

    def _start_call_streaming(self) -> None:
        if self._call_state != CallState.IN_CALL:
            return
        if self._call_sending or not self._p or not self._call_media_sock:
            return
        try:
            self._call_in_stream = self._p.open(
                format=AUDIO_FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CALL_MEDIA_CHUNK_SIZE,
            )
        except Exception as exc:
            print(f"[Client] Open private-call microphone failed: {exc}")
            return
        self._call_sending = True
        self._call_stream_id = uuid.uuid4().hex
        self._call_send_thread = threading.Thread(
            target=self._send_call_audio_loop, daemon=True
        )
        self._call_send_thread.start()
        self._emit_call_state()

    def _stop_call_streaming(self, notify_peer: bool = True) -> None:
        was_sending = self._call_sending
        self._call_sending = False
        try:
            if self._call_in_stream:
                self._call_in_stream.stop_stream()
                self._call_in_stream.close()
        except Exception:
            pass
        self._call_in_stream = None
        if notify_peer and was_sending and self._sock and self._in_call_with:
            try:
                self._send_raw(encode_media_stop(self._in_call_with))
            except Exception:
                pass
        self._emit_call_state()

    def _send_call_audio_loop(self) -> None:
        if not self._call_in_stream or not self._call_media_sock:
            self._call_sending = False
            return

        sequence = 0
        started_at = time.monotonic()
        while self._call_sending and self._call_media_sock:
            destinations = self._resolve_call_destinations()
            if not destinations:
                time.sleep(0.02)
                continue
            try:
                payload = self._call_in_stream.read(
                    CALL_MEDIA_CHUNK_SIZE, exception_on_overflow=False
                )
            except Exception:
                break
            if not payload:
                continue
            try:
                packet = encode_audio_frame(
                    stream_id=self._call_stream_id,
                    sequence=sequence,
                    timestamp_ms=int((time.monotonic() - started_at) * 1000),
                    sender=self._username,
                    target=self._in_call_with,
                    mode=self._session_mode or "negotiating",
                    raw=payload,
                )
                for destination in destinations:
                    self._call_media_sock.sendto(packet, destination)
                sequence += 1
            except Exception:
                break
            time.sleep(0.0005)

        self._call_sending = False
        self._emit_call_state()

    def _recv_call_media_loop(self) -> None:
        while self._running and self._call_media_sock:
            try:
                data, addr = self._call_media_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                packet_type, payload = decode_media_packet(data)
            except ValueError:
                continue

            if packet_type == "media_probe":
                sender = str(payload.get("sender", "")).strip()
                if sender and sender == self._in_call_with:
                    if self._classify_source_path(addr) == "direct":
                        self._report_direct_path_seen()
                continue

            self._handle_call_audio_packet(payload, addr)

    def _private_call_playback_loop(self) -> None:
        while True:
            if not self._running and not self._call_media_sock:
                break
            if not self._running or not self._out_stream:
                time.sleep(0.05)
                continue

            with self._call_jitter_lock:
                expected = self._call_expected_sequence
                next_play_time = self._call_next_play_time
                has_frames = bool(self._call_jitter_buffer)

            if expected is None or (not has_frames and self._call_state != CallState.IN_CALL):
                time.sleep(0.01)
                continue

            now = time.monotonic()
            if next_play_time and now < next_play_time:
                time.sleep(min(next_play_time - now, 0.01))
                continue

            raw = self._dequeue_call_audio_frame()
            if raw is None:
                time.sleep(0.005)
                continue
            try:
                self._out_stream.write(raw, exception_on_underflow=False)
            except Exception:
                pass

    def _dequeue_call_audio_frame(self) -> Optional[bytes]:
        with self._call_jitter_lock:
            if self._call_expected_sequence is None:
                return None

            frame = self._call_jitter_buffer.pop(self._call_expected_sequence, None)
            now = time.monotonic()
            if frame is None:
                min_seq = min(self._call_jitter_buffer) if self._call_jitter_buffer else None
                if min_seq is None and now < self._call_next_play_time + CALL_MISSING_GRACE_SEC:
                    return None
                if min_seq is not None and min_seq > self._call_expected_sequence:
                    raw = self._conceal_missing_call_frame_locked()
                else:
                    if now < self._call_next_play_time + CALL_MISSING_GRACE_SEC:
                        return None
                    raw = self._conceal_missing_call_frame_locked()
            else:
                raw = frame.payload

            self._call_last_played_frame = raw
            self._call_expected_sequence += 1
            if self._call_next_play_time == 0.0:
                self._call_next_play_time = now + CALL_FRAME_DURATION_SEC
            else:
                self._call_next_play_time = max(
                    self._call_next_play_time + CALL_FRAME_DURATION_SEC, now
                )
            return raw

    def _handle_call_audio_packet(self, payload: dict, addr: tuple[str, int]) -> None:
        sender = str(payload.get("sender", "")).strip()
        if not sender or sender != self._in_call_with:
            return

        source_path = self._classify_source_path(addr)
        if self._session_mode == "p2p" and source_path == "relay":
            return
        if self._session_mode == "relay" and source_path == "direct":
            return
        if source_path == "direct":
            self._report_direct_path_seen()

        try:
            sequence = int(payload.get("sequence", 0))
            timestamp_ms = int(payload.get("timestamp_ms", 0))
            stream_id = str(payload.get("stream_id", "")).strip()
            raw = base64.b64decode(str(payload.get("data", "")))
        except Exception:
            return
        if not raw:
            return

        with self._call_jitter_lock:
            if stream_id != self._call_remote_stream_id:
                self._call_remote_stream_id = stream_id
                self._call_jitter_buffer.clear()
                self._call_expected_sequence = None
                self._call_last_played_frame = None
                self._call_next_play_time = 0.0

            if (
                self._call_expected_sequence is not None
                and sequence < self._call_expected_sequence - 2
            ):
                return
            if sequence in self._call_jitter_buffer:
                return

            self._call_jitter_buffer[sequence] = MediaFrame(
                sequence=sequence,
                timestamp_ms=timestamp_ms,
                stream_id=stream_id,
                payload=raw,
                received_at=time.monotonic(),
            )
            if self._call_expected_sequence is None:
                self._call_expected_sequence = sequence
                self._call_next_play_time = time.monotonic() + CALL_PLAYBACK_DELAY_SEC

            if len(self._call_jitter_buffer) > MAX_CALL_JITTER_BUFFER_FRAMES:
                for old_seq in sorted(self._call_jitter_buffer)[
                    :-MAX_CALL_JITTER_BUFFER_FRAMES
                ]:
                    self._call_jitter_buffer.pop(old_seq, None)

    def _resolve_call_destinations(self) -> List[tuple[str, int]]:
        destinations: List[tuple[str, int]] = []
        if self._session_mode == "negotiating":
            if self._peer_media_addr:
                destinations.append(self._peer_media_addr)
            if self._relay_media_addr:
                destinations.append(self._relay_media_addr)
        elif self._session_mode == "p2p" and self._peer_media_addr:
            destinations.append(self._peer_media_addr)
        elif self._session_mode == "relay" and self._relay_media_addr:
            destinations.append(self._relay_media_addr)
        elif self._peer_media_addr:
            destinations.append(self._peer_media_addr)
        elif self._relay_media_addr:
            destinations.append(self._relay_media_addr)

        deduped: List[tuple[str, int]] = []
        seen = set()
        for item in destinations:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    def _classify_source_path(self, addr: tuple[str, int]) -> str:
        if self._peer_media_addr and addr == self._peer_media_addr:
            return "direct"
        if self._relay_media_addr and addr == self._relay_media_addr:
            return "relay"
        return "unknown"

    def _start_call_probe_thread(self) -> None:
        self._stop_call_probe_thread()
        self._call_probe_stop.clear()
        self._call_probe_thread = threading.Thread(
            target=self._call_probe_loop, daemon=True
        )
        self._call_probe_thread.start()

    def _stop_call_probe_thread(self) -> None:
        self._call_probe_stop.set()

    def _call_probe_loop(self) -> None:
        while (
            self._running
            and not self._call_probe_stop.is_set()
            and self._call_state == CallState.IN_CALL
            and self._session_mode == "negotiating"
        ):
            self._send_direct_probe()
            time.sleep(NEGOTIATION_PROBE_INTERVAL_SEC)

    def _send_direct_probe(self) -> None:
        if not self._peer_media_addr or not self._call_media_sock or not self._call_id:
            return
        try:
            self._call_media_sock.sendto(
                encode_media_probe(
                    self._username,
                    self._in_call_with,
                    self._call_id,
                    self._session_mode or "negotiating",
                ),
                self._peer_media_addr,
            )
        except Exception:
            pass

    def _report_direct_path_seen(self) -> None:
        if not self._call_id or not self._sock or not self._in_call_with:
            return
        if self._direct_path_reported:
            return
        self._direct_path_reported = True
        try:
            self._send_raw(encode_direct_path_seen(self._call_id, self._in_call_with))
        except Exception:
            self._direct_path_reported = False

    def _clear_private_call_session(self, reset_state: bool) -> None:
        self._stop_call_streaming(notify_peer=False)
        self._stop_call_probe_thread()
        self._call_id = ""
        self._session_mode = ""
        self._peer_media_addr = None
        self._relay_media_addr = None
        self._first_packet_path_logged = False
        self._direct_path_reported = False
        self._reset_call_jitter_buffer()
        if reset_state:
            self._in_call_with = ""

    def _conceal_missing_call_frame_locked(self) -> bytes:
        next_frame = None
        if self._call_jitter_buffer:
            next_frame = self._call_jitter_buffer.get(min(self._call_jitter_buffer))
        if self._call_last_played_frame and next_frame:
            return self._interpolate_frames(
                self._call_last_played_frame, next_frame.payload
            )
        if self._call_last_played_frame:
            return self._attenuate_frame(self._call_last_played_frame, 0.92)
        if next_frame:
            return next_frame.payload
        return bytes(CALL_MEDIA_CHUNK_SIZE * 2)

    def _interpolate_frames(self, left: bytes, right: bytes) -> bytes:
        left_samples = self._bytes_to_samples(left)
        right_samples = self._bytes_to_samples(right)
        size = min(len(left_samples), len(right_samples))
        mixed = array("h")
        for idx in range(size):
            mixed.append(int((left_samples[idx] + right_samples[idx]) / 2))
        return self._samples_to_bytes(mixed)

    def _attenuate_frame(self, raw: bytes, factor: float) -> bytes:
        samples = self._bytes_to_samples(raw)
        adjusted = array("h")
        for sample in samples:
            adjusted.append(self._clip_sample(int(sample * factor)))
        return self._samples_to_bytes(adjusted)

    def _bytes_to_samples(self, raw: bytes) -> array:
        samples = array("h")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        return samples

    def _samples_to_bytes(self, samples: array) -> bytes:
        converted = array("h", samples)
        if sys.byteorder != "little":
            converted.byteswap()
        return converted.tobytes()

    def _clip_sample(self, value: int) -> int:
        return max(-32768, min(32767, value))

    # ---- network ----

    def _send_raw(self, msg):
        if not self._sock:
            raise RuntimeError("未连接服务器")
        with self._send_lock:
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
        if mtype in {
            "call_invite",
            "call_ready",
            "transport_update",
            "call_reject",
            "call_busy",
            "call_not_found",
            "call_hangup",
            "media_stop",
        }:
            self._handle_call_message(mtype, payload)

        elif mtype == "room_invite_notify":
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
            with self._incoming_audio_lock:
                self._incoming_audio.clear()
                self._lowpass_state.clear()
                self._volume_levels.clear()
                self._receive_transcoders.clear()
            self._room_id = ""
            self._room_state = RoomState.IDLE
            self._is_creator = False
            self._my_position = -1
            self._audio_protocol = "tcp"
            self._audio_seq = 0
            self.reset_quality_stats()
            self._active_receive_format = CANONICAL_AUDIO_FORMAT
            self._active_receive_profile = "wideband"
            if self._on_room_dismissed:
                self._on_room_dismissed(room_id)

        elif mtype == "room_audio_chunk":
            sender = payload.get("sender", "")
            b64 = payload.get("data", "")
            seq = payload.get("seq")
            timestamp_ms = payload.get("timestamp_ms")
            try:
                raw = base64.b64decode(b64)
                if sender and raw:
                    receive_format = self._update_receive_audio_profile(
                        payload.get("audio_format"), payload.get("profile")
                    )
                    self._record_audio_quality(sender, seq, timestamp_ms)
                    self._enqueue_received_audio(sender, raw, receive_format)
            except Exception:
                pass

    def _handle_call_message(self, mtype: str, payload: dict) -> None:
        if mtype == "call_invite":
            caller = str(payload.get("caller", "")).strip()
            if not caller:
                return
            self._in_call_with = caller
            self._set_call_state(CallState.RINGING, caller)
            return

        if mtype == "call_ready":
            peer = str(payload.get("peer", "")).strip()
            relay_port = int(payload.get("relay_port", self._port) or self._port)
            peer_ip = str(payload.get("peer_ip", "")).strip()
            peer_port = int(payload.get("peer_port", 0) or 0)
            self._in_call_with = peer
            self._call_id = str(payload.get("call_id", "")).strip()
            self._session_mode = str(payload.get("mode", "")).strip() or "negotiating"
            self._peer_media_addr = (peer_ip, peer_port) if peer_ip and peer_port else None
            self._relay_media_addr = (self._server_ip or self._host, relay_port)
            self._direct_path_reported = False
            self._reset_call_jitter_buffer()
            self._start_call_probe_thread()
            self._set_call_state(CallState.IN_CALL, peer)
            self._start_call_streaming()
            return

        if mtype == "transport_update":
            call_id = str(payload.get("call_id", "")).strip()
            if call_id and self._call_id and call_id != self._call_id:
                return
            self._session_mode = str(payload.get("mode", "")).strip()
            self._stop_call_probe_thread()
            self._emit_call_state()
            return

        if mtype == "call_reject":
            self._clear_private_call_session(reset_state=True)
            self._set_call_state(CallState.ENDED, "")
            threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()
            return

        if mtype == "call_busy":
            self._clear_private_call_session(reset_state=True)
            self._set_call_state(CallState.ENDED, "")
            threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()
            return

        if mtype == "call_not_found":
            self._clear_private_call_session(reset_state=True)
            self._set_call_state(CallState.ENDED, "")
            threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()
            return

        if mtype == "call_hangup":
            self._clear_private_call_session(reset_state=True)
            self._set_call_state(CallState.ENDED, "")
            threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()
            return

        if mtype == "media_stop":
            self._reset_call_jitter_buffer()

    def set_sender_volume(self, sender: str, volume: float) -> None:
        with self._incoming_audio_lock:
            self._sender_volumes[sender] = max(0.0, min(volume, 2.0))

    def _enqueue_received_audio(
        self, sender: str, raw: bytes, audio_format: AudioFormat
    ) -> None:
        if not raw:
            return
        transcoder = self._receive_transcoders.get(sender)
        if transcoder is None:
            transcoder = ReframingAudioTranscoder(CANONICAL_AUDIO_FORMAT)
            self._receive_transcoders[sender] = transcoder
        canonical_chunks = transcoder.feed(raw, audio_format)
        if not canonical_chunks:
            return

        with self._incoming_audio_lock:
            queue = self._incoming_audio.setdefault(sender, deque())
            for chunk in canonical_chunks:
                filtered = self._apply_noise_lowpass(sender, chunk)
                self._update_volume_level(sender, filtered)
                if len(queue) > 50:
                    queue.popleft()
                queue.append(filtered)

    def _update_volume_level(self, sender: str, raw: bytes) -> None:
        if len(raw) < 2:
            return
        sample_count = len(raw) // 2
        try:
            samples = struct.unpack(f"<{sample_count}h", raw)
            rms = math.sqrt(sum(s * s for s in samples) / sample_count)
            normalized = min(rms / 10000.0, 1.0)
            self._volume_levels[sender] = normalized
        except Exception:
            pass

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
