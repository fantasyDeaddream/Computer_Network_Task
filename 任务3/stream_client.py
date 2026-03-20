"""
Task 3 client:
- TCP signaling
- UDP media with short playout delay
- packet ordering and light packet-loss concealment
"""

from __future__ import annotations

import base64
import ipaddress
import json
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    import pyaudio
except ImportError:
    pyaudio = None

from stream_protocol import (
    decode_media_packet,
    decode_message,
    encode_audio_frame,
    encode_call_accept,
    encode_call_hangup,
    encode_call_invite,
    encode_call_reject,
    encode_login,
    encode_media_probe,
    encode_text,
)


def _ensure_task2_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task2_dir = base / "任务2"
    if str(task2_dir) not in sys.path:
        sys.path.insert(0, str(task2_dir))


_ensure_task2_on_path()

from audio_config import (  # type: ignore  # noqa: E402
    AUDIO_FORMAT,
    CHANNELS,
    DEFAULT_HOST,
    DEFAULT_PORT,
    MESSAGE_DELIMITER,
    SAMPLE_RATE,
)


MEDIA_CHUNK_SIZE = 320
FRAME_DURATION_SEC = MEDIA_CHUNK_SIZE / float(SAMPLE_RATE)
PLAYBACK_DELAY_SEC = FRAME_DURATION_SEC * 3
MISSING_GRACE_SEC = FRAME_DURATION_SEC * 0.75
MAX_JITTER_BUFFER_FRAMES = 64


class CallState:
    IDLE = "idle"
    CALLING = "calling"
    RINGING = "ringing"
    CONNECTING = "connecting"
    IN_CALL = "in_call"
    ENDED = "ended"


@dataclass
class StreamClientConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    nickname: str = "User"


@dataclass
class MediaFrame:
    sequence: int
    timestamp_ms: int
    stream_id: str
    payload: bytes
    received_at: float


class StreamClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        nickname: str = "User",
        on_text: Optional[Callable[[str], None]] = None,
        on_call_state_change: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.cfg = StreamClientConfig(host=host, port=port, nickname=nickname)

        self._control_sock: Optional[socket.socket] = None
        self._media_sock: Optional[socket.socket] = None
        self._running = False
        self._sending = False

        self._control_thread: Optional[threading.Thread] = None
        self._media_thread: Optional[threading.Thread] = None
        self._playback_thread: Optional[threading.Thread] = None
        self._send_thread: Optional[threading.Thread] = None

        self._response_queue: "queue.Queue[tuple[bool, str, dict]]" = queue.Queue()

        self._p = pyaudio.PyAudio() if pyaudio else None
        self._out_stream: Optional["pyaudio.Stream"] = None
        self._in_stream: Optional["pyaudio.Stream"] = None

        self._stream_id = uuid.uuid4().hex
        self._on_text = on_text
        self._on_call_state_change = on_call_state_change

        self._call_state = CallState.IDLE
        self._in_call_with = ""
        self._session_mode = ""
        self._peer_media_addr: Optional[tuple[str, int]] = None
        self._relay_media_addr: Optional[tuple[str, int]] = None

        self._local_ip = ""
        self._subnet_prefix = 24
        self._media_port = 0
        self._first_packet_path_logged = False

        self._jitter_lock = threading.Lock()
        self._jitter_buffer: dict[int, MediaFrame] = {}
        self._expected_sequence: Optional[int] = None
        self._next_play_time = 0.0
        self._current_remote_stream_id = ""
        self._last_played_frame: Optional[bytes] = None

    @property
    def is_connected(self) -> bool:
        return self._control_sock is not None

    @property
    def call_state(self) -> str:
        return self._call_state

    @property
    def in_call_with(self) -> str:
        return self._in_call_with

    @property
    def session_mode(self) -> str:
        return self._session_mode

    def connect(self) -> bool:
        if self._control_sock:
            return True

        try:
            self._control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._control_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._control_sock.connect((self.cfg.host, self.cfg.port))

            self._local_ip = self._control_sock.getsockname()[0]
            self._subnet_prefix = self._detect_subnet_prefix(self._local_ip)

            self._media_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._media_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._media_sock.settimeout(0.3)
            self._media_sock.bind((self._local_ip, 0))
            self._media_port = int(self._media_sock.getsockname()[1])

            print(
                f"[Client] Connected to signaling server {self.cfg.host}:{self.cfg.port}; "
                f"local_ip={self._local_ip}, subnet=/{self._subnet_prefix}, udp_port={self._media_port}"
            )

            login = encode_login(
                self.cfg.nickname,
                self._media_port,
                self._local_ip,
                self._subnet_prefix,
            )
            self._send_control(login)
            success, message = self._sync_wait_response()
            if not success:
                print(f"[Client] Login failed: {message}")
                self.disconnect()
                return False

            if self._p:
                self._out_stream = self._p.open(
                    format=AUDIO_FORMAT,
                    channels=CHANNELS,
                    rate=SAMPLE_RATE,
                    output=True,
                    frames_per_buffer=MEDIA_CHUNK_SIZE,
                )
            else:
                print("[Client] Warning: pyaudio is unavailable; audio playback will not work")

            self._running = True
            self._control_thread = threading.Thread(target=self._recv_control_loop, daemon=True)
            self._media_thread = threading.Thread(target=self._recv_media_loop, daemon=True)
            self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
            self._control_thread.start()
            self._media_thread.start()
            self._playback_thread.start()
            return True
        except Exception as exc:
            print(f"[Client] Connect failed: {exc}")
            self.disconnect()
            return False

    def disconnect(self) -> None:
        if self._control_sock and self._in_call_with:
            try:
                self._send_control(encode_call_hangup(self._in_call_with))
            except Exception:
                pass

        self._running = False
        self._sending = False
        self._clear_media_session(reset_state=True)

        try:
            if self._in_stream:
                self._in_stream.stop_stream()
                self._in_stream.close()
        except Exception:
            pass
        self._in_stream = None

        try:
            if self._out_stream:
                self._out_stream.stop_stream()
                self._out_stream.close()
        except Exception:
            pass
        self._out_stream = None

        try:
            if self._control_sock:
                self._control_sock.close()
        except Exception:
            pass
        self._control_sock = None

        try:
            if self._media_sock:
                self._media_sock.close()
        except Exception:
            pass
        self._media_sock = None

    def send_text(self, content: str, target: str = "") -> None:
        if not self._control_sock:
            raise RuntimeError("Not connected to the signaling server")
        resolved_target = target or self._in_call_with
        if not resolved_target:
            raise RuntimeError("No target user selected")
        self._send_control(encode_text(content, resolved_target))

    def call(self, target: str) -> bool:
        target = target.strip()
        if not target or not self._control_sock:
            return False
        if self._call_state not in (CallState.IDLE, CallState.ENDED):
            return False
        self._in_call_with = target
        self._set_call_state(CallState.CALLING, target)
        self._send_control(encode_call_invite(target))
        print(f"[Client] Calling {target}...")
        return True

    def accept_call(self, caller: str) -> bool:
        caller = caller.strip()
        if not caller or not self._control_sock:
            return False
        self._in_call_with = caller
        self._set_call_state(CallState.CONNECTING, caller)
        self._send_control(encode_call_accept(caller))
        print(f"[Client] Accepting call from {caller}; waiting for session setup...")
        return True

    def reject_call(self, caller: str) -> bool:
        caller = caller.strip()
        if not caller or not self._control_sock:
            return False
        self._send_control(encode_call_reject(caller, "Call rejected"))
        print(f"[Client] Rejected call from {caller}")
        self._clear_media_session(reset_state=True)
        self._set_call_state(CallState.IDLE, "")
        return True

    def hangup(self) -> None:
        if not self._control_sock or not self._in_call_with:
            self._clear_media_session(reset_state=True)
            self._set_call_state(CallState.IDLE, "")
            return
        peer = self._in_call_with
        try:
            self._send_control(encode_call_hangup(peer))
        except Exception:
            pass
        self._clear_media_session(reset_state=True)
        self._set_call_state(CallState.ENDED, "")
        print(f"[Client] Call with {peer} ended")
        threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()

    def start_streaming(self) -> None:
        if self._call_state != CallState.IN_CALL:
            raise RuntimeError("You need an active call before sending audio")
        if not self._media_sock:
            raise RuntimeError("UDP media socket is not ready")
        if self._sending:
            return
        if not self._p:
            raise RuntimeError("pyaudio is not available")

        try:
            self._in_stream = self._p.open(
                format=AUDIO_FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=MEDIA_CHUNK_SIZE,
            )
        except Exception as exc:
            print(f"[Client] Failed to open microphone: {exc}")
            raise

        self._sending = True
        self._stream_id = uuid.uuid4().hex
        self._send_media_probe()
        self._send_thread = threading.Thread(target=self._send_audio_loop, daemon=True)
        self._send_thread.start()
        print(
            f"[Client] Start UDP audio streaming to {self._in_call_with} "
            f"using {self._session_mode.upper()} mode"
        )

    def stop_streaming(self) -> None:
        self._sending = False
        try:
            if self._in_stream:
                self._in_stream.stop_stream()
                self._in_stream.close()
        except Exception:
            pass
        self._in_stream = None
        print("[Client] Stop audio capture")

    def _send_audio_loop(self) -> None:
        if not self._in_stream or not self._media_sock:
            self._sending = False
            return

        sequence = 0
        started_at = time.monotonic()

        while self._sending and self._media_sock:
            destination = self._resolve_media_destination()
            if not destination:
                time.sleep(0.02)
                continue

            try:
                payload = self._in_stream.read(
                    MEDIA_CHUNK_SIZE,
                    exception_on_overflow=False,
                )
            except Exception:
                break

            if not payload:
                continue

            try:
                timestamp_ms = int((time.monotonic() - started_at) * 1000)
                packet = encode_audio_frame(
                    stream_id=self._stream_id,
                    sequence=sequence,
                    timestamp_ms=timestamp_ms,
                    sender=self.cfg.nickname,
                    target=self._in_call_with,
                    mode=self._session_mode,
                    raw=payload,
                )
                self._media_sock.sendto(packet.encode("utf-8"), destination)
                sequence += 1
            except Exception:
                break

            time.sleep(0.0005)

        self._sending = False

    def _recv_control_loop(self) -> None:
        buffer = ""
        while self._running and self._control_sock:
            try:
                data = self._control_sock.recv(4096)
            except Exception:
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
                    msg_type, payload = decode_message(line)
                except ValueError as exc:
                    print(f"[Client] Invalid control message: {exc}")
                    continue

                if msg_type == "response":
                    success = bool(payload.get("success", False))
                    message = str(payload.get("message", ""))
                    data_field = payload.get("data", {}) or {}
                    self._response_queue.put((success, message, data_field))
                    if message:
                        print(f"[Client] Server response: {message}")
                    continue

                self._handle_control_message(msg_type, payload)

        self._running = False
        self._sending = False
        print("[Client] Disconnected from signaling server")

    def _recv_media_loop(self) -> None:
        while self._running and self._media_sock:
            try:
                data, addr = self._media_sock.recvfrom(65535)
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
                    print(f"[Client] UDP probe received from {sender} at {addr}")
                continue

            self._handle_audio_packet(payload, addr)

    def _playback_loop(self) -> None:
        while True:
            if not self._running:
                time.sleep(0.02)
                continue
            if not self._out_stream:
                time.sleep(0.05)
                continue

            with self._jitter_lock:
                expected = self._expected_sequence
                next_play_time = self._next_play_time
                has_frames = bool(self._jitter_buffer)

            if expected is None or not has_frames and self._call_state != CallState.IN_CALL:
                time.sleep(0.01)
                continue

            now = time.monotonic()
            if next_play_time and now < next_play_time:
                time.sleep(min(next_play_time - now, 0.01))
                continue

            raw = self._dequeue_next_audio_frame()
            if raw is None:
                time.sleep(0.005)
                continue

            try:
                self._out_stream.write(raw, exception_on_underflow=False)
            except Exception:
                pass

    def _dequeue_next_audio_frame(self) -> Optional[bytes]:
        with self._jitter_lock:
            if self._expected_sequence is None:
                return None

            frame = self._jitter_buffer.pop(self._expected_sequence, None)
            now = time.monotonic()

            if frame is None:
                min_seq = min(self._jitter_buffer) if self._jitter_buffer else None
                if min_seq is None and now < self._next_play_time + MISSING_GRACE_SEC:
                    return None
                if min_seq is not None and min_seq > self._expected_sequence:
                    raw = self._conceal_missing_frame_locked()
                else:
                    if now < self._next_play_time + MISSING_GRACE_SEC:
                        return None
                    raw = self._conceal_missing_frame_locked()
            else:
                raw = frame.payload

            self._last_played_frame = raw
            self._expected_sequence += 1
            if self._next_play_time == 0.0:
                self._next_play_time = now + FRAME_DURATION_SEC
            else:
                self._next_play_time = max(self._next_play_time + FRAME_DURATION_SEC, now)
            return raw

    def _handle_control_message(self, msg_type: str, payload: dict) -> None:
        if msg_type == "call_invite":
            caller = str(payload.get("caller", "")).strip()
            if not caller:
                return
            self._in_call_with = caller
            self._set_call_state(CallState.RINGING, caller)
            print(f"[Client] Incoming call from {caller}")
            return

        if msg_type == "call_ready":
            peer = str(payload.get("peer", "")).strip()
            mode = str(payload.get("mode", "")).strip()
            detail = str(payload.get("detail", "")).strip()
            peer_ip = str(payload.get("peer_ip", "")).strip()
            peer_port = int(payload.get("peer_port", 0) or 0)
            relay_port = int(payload.get("relay_port", self.cfg.port) or self.cfg.port)

            self._in_call_with = peer
            self._session_mode = mode
            self._peer_media_addr = (peer_ip, peer_port) if peer_ip and peer_port else None
            self._relay_media_addr = (self.cfg.host, relay_port)
            self._reset_jitter_buffer()
            self._send_media_probe()
            self._set_call_state(CallState.IN_CALL, peer)

            path_desc = (
                f"P2P peer={peer_ip}:{peer_port}"
                if mode == "p2p"
                else f"server relay={self.cfg.host}:{relay_port}"
            )
            print(f"[Client] Call ready with {peer}: mode={mode.upper()}, {path_desc}")
            if detail:
                print(f"[Client] Routing decision detail: {detail}")
            return

        if msg_type == "call_reject":
            caller = str(payload.get("caller", "")).strip()
            reason = str(payload.get("reason", "")).strip() or "Call rejected"
            print(f"[Client] {caller or 'Peer'} rejected the call: {reason}")
            self._clear_media_session(reset_state=True)
            self._set_call_state(CallState.ENDED, "")
            threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()
            return

        if msg_type == "call_busy":
            target = str(payload.get("target", "")).strip()
            print(f"[Client] {target or 'Target user'} is busy")
            self._clear_media_session(reset_state=True)
            self._set_call_state(CallState.ENDED, "")
            threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()
            return

        if msg_type == "call_not_found":
            target = str(payload.get("target", "")).strip()
            print(f"[Client] User {target or '?'} is not online")
            self._clear_media_session(reset_state=True)
            self._set_call_state(CallState.ENDED, "")
            threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()
            return

        if msg_type == "call_hangup":
            peer = str(payload.get("peer", "")).strip() or self._in_call_with
            print(f"[Client] {peer or 'Peer'} hung up")
            self._clear_media_session(reset_state=True)
            self._set_call_state(CallState.ENDED, "")
            threading.Timer(1.0, lambda: self._set_call_state(CallState.IDLE, "")).start()
            return

        if msg_type == "text":
            text = str(payload.get("content", ""))
            if self._on_text:
                self._on_text(text)
            else:
                print(text)

    def _handle_audio_packet(self, payload: dict, addr: tuple[str, int]) -> None:
        sender = str(payload.get("sender", "")).strip()
        if not sender or sender != self._in_call_with:
            return

        try:
            sequence = int(payload.get("sequence", 0))
            timestamp_ms = int(payload.get("timestamp_ms", 0))
            stream_id = str(payload.get("stream_id", "")).strip()
            raw = base64.b64decode(str(payload.get("data", "")))
        except Exception:
            return

        if not raw:
            return

        if not self._first_packet_path_logged:
            if self._session_mode == "p2p":
                print(f"[Client] First UDP audio packet from peer {addr} (direct P2P)")
            else:
                print(f"[Client] First UDP audio packet from relay {addr} (server relay)")
            self._first_packet_path_logged = True

        with self._jitter_lock:
            if stream_id != self._current_remote_stream_id:
                self._current_remote_stream_id = stream_id
                self._jitter_buffer.clear()
                self._expected_sequence = None
                self._last_played_frame = None
                self._next_play_time = 0.0

            if self._expected_sequence is not None and sequence < self._expected_sequence - 2:
                return
            if sequence in self._jitter_buffer:
                return

            self._jitter_buffer[sequence] = MediaFrame(
                sequence=sequence,
                timestamp_ms=timestamp_ms,
                stream_id=stream_id,
                payload=raw,
                received_at=time.monotonic(),
            )

            if self._expected_sequence is None:
                self._expected_sequence = sequence
                self._next_play_time = time.monotonic() + PLAYBACK_DELAY_SEC

            if len(self._jitter_buffer) > MAX_JITTER_BUFFER_FRAMES:
                for old_seq in sorted(self._jitter_buffer)[:-MAX_JITTER_BUFFER_FRAMES]:
                    self._jitter_buffer.pop(old_seq, None)

    def _conceal_missing_frame_locked(self) -> bytes:
        next_frame = None
        if self._jitter_buffer:
            next_sequence = min(self._jitter_buffer)
            next_frame = self._jitter_buffer.get(next_sequence)

        if self._last_played_frame and next_frame:
            print(f"[Client] Missing UDP frame #{self._expected_sequence}; using interpolation")
            return self._interpolate_frames(self._last_played_frame, next_frame.payload)
        if self._last_played_frame:
            print(f"[Client] Missing UDP frame #{self._expected_sequence}; repeating attenuated last frame")
            return self._attenuate_frame(self._last_played_frame, 0.92)
        if next_frame:
            print(f"[Client] Missing UDP frame #{self._expected_sequence}; borrowing next frame as fallback")
            return next_frame.payload

        return bytes(MEDIA_CHUNK_SIZE * 2)

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

    def _resolve_media_destination(self) -> Optional[tuple[str, int]]:
        if self._session_mode == "p2p":
            return self._peer_media_addr
        if self._session_mode == "relay":
            return self._relay_media_addr
        return None

    def _send_media_probe(self) -> None:
        destination = self._resolve_media_destination()
        if not destination or not self._media_sock:
            return
        try:
            probe = encode_media_probe(self.cfg.nickname, self._in_call_with, self._session_mode)
            self._media_sock.sendto(probe.encode("utf-8"), destination)
        except Exception:
            pass

    def _send_control(self, msg: str) -> None:
        if not self._control_sock:
            raise RuntimeError("Not connected to the signaling server")
        self._control_sock.sendall((msg + MESSAGE_DELIMITER).encode("utf-8"))

    def _sync_wait_response(self, timeout: float = 5.0) -> tuple[bool, str]:
        if not self._control_sock:
            return False, "No signaling connection"

        deadline = time.monotonic() + timeout
        previous_timeout = self._control_sock.gettimeout()
        self._control_sock.settimeout(0.5)
        buffer = ""
        try:
            while time.monotonic() < deadline:
                try:
                    data = self._control_sock.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    return False, "Server closed the connection"

                buffer += data.decode("utf-8", errors="ignore")
                while MESSAGE_DELIMITER in buffer:
                    line, buffer = buffer.split(MESSAGE_DELIMITER, 1)
                    line = line.strip()
                    if not line:
                        continue
                    msg_type, payload = decode_message(line)
                    if msg_type == "response":
                        return (
                            bool(payload.get("success", False)),
                            str(payload.get("message", "")),
                        )
            return False, "Timed out waiting for login response"
        finally:
            self._control_sock.settimeout(previous_timeout)

    def _set_call_state(self, state: str, target: str) -> None:
        self._call_state = state
        if self._on_call_state_change:
            self._on_call_state_change(state, target)

    def _clear_media_session(self, reset_state: bool) -> None:
        self.stop_streaming()
        self._session_mode = ""
        self._peer_media_addr = None
        self._relay_media_addr = None
        self._first_packet_path_logged = False
        self._reset_jitter_buffer()
        if reset_state:
            self._in_call_with = ""

    def _reset_jitter_buffer(self) -> None:
        with self._jitter_lock:
            self._jitter_buffer.clear()
            self._expected_sequence = None
            self._next_play_time = 0.0
            self._current_remote_stream_id = ""
            self._last_played_frame = None

    def _detect_subnet_prefix(self, ip: str) -> int:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return 24

        if addr.is_loopback:
            return 8

        prefix = self._detect_prefix_from_os(ip)
        if prefix is not None:
            return prefix

        if addr.is_private:
            return 24
        return 32

    def _detect_prefix_from_os(self, ip: str) -> Optional[int]:
        try:
            if sys.platform.startswith("win"):
                output = subprocess.check_output(
                    ["ipconfig"],
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                return self._parse_windows_prefix(output, ip)
            output = subprocess.check_output(
                ["ip", "-o", "-f", "inet", "addr", "show"],
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            return self._parse_unix_prefix(output, ip)
        except Exception:
            return None

    def _parse_windows_prefix(self, output: str, ip: str) -> Optional[int]:
        ipv4_match = re.compile(r"IPv4[^:]*:\s*([0-9.]+)")
        mask_match = re.compile(r"Subnet Mask[^:]*:\s*([0-9.]+)")
        current_ip = ""
        for line in output.splitlines():
            ip_match = ipv4_match.search(line)
            if ip_match:
                current_ip = ip_match.group(1)
                continue
            mask_found = mask_match.search(line)
            if mask_found and current_ip == ip:
                return self._mask_to_prefix(mask_found.group(1))
        return None

    def _parse_unix_prefix(self, output: str, ip: str) -> Optional[int]:
        pattern = re.compile(rf"\binet\s+{re.escape(ip)}/(\d+)\b")
        match = pattern.search(output)
        if match:
            return int(match.group(1))
        return None

    def _mask_to_prefix(self, mask: str) -> Optional[int]:
        try:
            return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
        except Exception:
            return None
