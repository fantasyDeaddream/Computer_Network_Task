"""
Task 5 conference server with per-client adaptive downstream audio delivery.
"""

from __future__ import annotations

import base64
import json
import os
import random
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import sys

from audio_adaptive import (
    AudioFormat,
    CANONICAL_AUDIO_FORMAT,
    DEFAULT_ADAPTIVE_PROFILE,
    ReframingAudioTranscoder,
    choose_adaptive_profile,
    get_profile_by_name,
)
from conference_protocol import (
    MAX_ROOM_SIZE,
    MESSAGE_DELIMITER,
    decode_media_packet,
    decode_message,
    decode_udp_audio_packet,
    encode_call_busy,
    encode_call_not_found,
    encode_call_ready,
    encode_room_audio_chunk,
    encode_room_dismissed_notify,
    encode_room_invite_notify,
    encode_room_member_update,
    encode_transport_update,
    encode_response,
    encode_udp_audio_packet,
)


def _ensure_task4_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task4_dir = base / "任务4"
    if str(task4_dir) not in sys.path:
        sys.path.insert(0, str(task4_dir))


_ensure_task4_on_path()

from data_store import get_data_store


DEFAULT_PORT = 8882
NEGOTIATION_TIMEOUT_SEC = 1.6


@dataclass(frozen=True)
class NetworkImpairment:
    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    loss_rate: float = 0.0

    @classmethod
    def from_env(cls) -> "NetworkImpairment":
        def read_float(name: str, default: float = 0.0) -> float:
            try:
                return float(os.getenv(name, default))
            except (TypeError, ValueError):
                return default

        delay_ms = max(0.0, read_float("TASK5_DELAY_MS"))
        jitter_ms = max(0.0, read_float("TASK5_JITTER_MS"))
        loss_rate = max(0.0, read_float("TASK5_LOSS_RATE"))
        if loss_rate > 1.0:
            loss_rate /= 100.0
        return cls(
            delay_ms=delay_ms,
            jitter_ms=jitter_ms,
            loss_rate=min(loss_rate, 1.0),
        )

    @property
    def enabled(self) -> bool:
        return self.delay_ms > 0 or self.jitter_ms > 0 or self.loss_rate > 0

    def sampled_delay_seconds(self) -> float:
        if self.delay_ms <= 0 and self.jitter_ms <= 0:
            return 0.0
        jitter = random.uniform(-self.jitter_ms, self.jitter_ms)
        return max(0.0, self.delay_ms + jitter) / 1000.0


@dataclass
class ClientAudioState:
    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    packet_loss_percent: float = 0.0
    last_reported_at: float = 0.0
    profile_name: str = DEFAULT_ADAPTIVE_PROFILE.name
    outgoing_seq: Dict[str, int] = field(default_factory=dict)
    transcoders: Dict[str, ReframingAudioTranscoder] = field(default_factory=dict)


@dataclass
class ClientInfo:
    conn: socket.socket
    addr: Tuple[str, int]
    username: str = ""
    room_id: str = ""
    udp_addr: Optional[Tuple[str, int]] = None
    media_port: int = 0
    local_ip: str = ""
    pending_peer: str = ""
    in_call_with: str = ""
    call_mode: str = ""
    call_id: str = ""
    call_udp_observed_addr: Optional[Tuple[str, int]] = None
    send_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    audio_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    audio_state: ClientAudioState = field(default_factory=ClientAudioState, repr=False)


@dataclass
class CallSession:
    call_id: str
    caller: str
    callee: str
    direct_seen: Set[str] = field(default_factory=set)
    final_mode: str = ""
    timer: Optional[threading.Timer] = None


@dataclass
class ChatRoom:
    room_id: str
    creator: str
    audio_protocol: str = "tcp"
    members: Dict[str, int] = field(default_factory=dict)
    invited: Set[str] = field(default_factory=set)
    udp_port: int = 0
    _udp_sock: Optional[socket.socket] = field(default=None, repr=False)

    def get_available_positions(self) -> List[int]:
        used = set(self.members.values())
        return [i for i in range(MAX_ROOM_SIZE) if i not in used]

    def assign_position(self, username: str) -> int:
        available = self.get_available_positions()
        if not available:
            return -1
        pos = random.choice(available)
        self.members[username] = pos
        return pos

    def get_member_list(self) -> List[dict]:
        return [
            {"username": username, "position": position}
            for username, position in sorted(self.members.items(), key=lambda item: item[1])
        ]


class ConferenceServer:
    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._media_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._media_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._media_sock.settimeout(0.5)
        self._clients: Dict[int, ClientInfo] = {}
        self._username_map: Dict[str, int] = {}
        self._rooms: Dict[str, ChatRoom] = {}
        self._sessions: Dict[str, CallSession] = {}
        self._lock = threading.RLock()
        self._running = False
        self._data_store = get_data_store()
        self._impairment = NetworkImpairment.from_env()
        self._media_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._sock.bind((self._host, self._port))
        self._sock.listen(20)
        self._media_sock.bind((self._host, self._port))
        self._running = True
        print(f"[Server] Listening on {self._host}:{self._port}")
        print(f"[Server] Private-call UDP relay on {self._host}:{self._port}")
        if self._impairment.enabled:
            print(
                "[Server] Network impairment: "
                f"delay={self._impairment.delay_ms:.1f}ms, "
                f"jitter={self._impairment.jitter_ms:.1f}ms, "
                f"loss={self._impairment.loss_rate * 100:.1f}%"
            )
        self._media_thread = threading.Thread(
            target=self._private_call_udp_loop, daemon=True
        )
        self._media_thread.start()
        try:
            while self._running:
                try:
                    conn, addr = self._sock.accept()
                except OSError:
                    break
                threading.Thread(
                    target=self._handle_client, args=(conn, addr), daemon=True
                ).start()
        finally:
            self._cleanup()
            print("[Server] Stopped")

    def stop(self) -> None:
        self._running = False
        try:
            self._sock.close()
        except Exception:
            pass
        try:
            self._media_sock.close()
        except Exception:
            pass

    def _cleanup(self) -> None:
        with self._lock:
            for session in list(self._sessions.values()):
                if session.timer:
                    session.timer.cancel()
            self._sessions.clear()
            for room in list(self._rooms.values()):
                self._close_room_udp(room)
            for info in list(self._clients.values()):
                try:
                    info.conn.close()
                except Exception:
                    pass
            self._clients.clear()
            self._username_map.clear()
            self._rooms.clear()
        try:
            self._sock.close()
        except Exception:
            pass
        try:
            self._media_sock.close()
        except Exception:
            pass

    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        cid = id(conn)
        with self._lock:
            self._clients[cid] = ClientInfo(conn=conn, addr=addr)
        print(f"[Server] New connection: {addr}")

        buf = ""
        try:
            while self._running:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data.decode("utf-8", errors="ignore")
                while MESSAGE_DELIMITER in buf:
                    line, buf = buf.split(MESSAGE_DELIMITER, 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message_type, payload = decode_message(line)
                    except ValueError:
                        continue
                    self._dispatch(cid, message_type, payload, conn)
        except ConnectionResetError:
            pass
        except OSError:
            pass
        finally:
            self._handle_disconnect(cid)

    def _dispatch(self, cid, message_type, payload, conn) -> None:
        handlers = {
            "login": lambda: self._handle_login(cid, payload, conn),
            "logout": lambda: self._handle_logout(cid),
            "contact_add": lambda: self._handle_contact_op(cid, payload, "add"),
            "contact_delete": lambda: self._handle_contact_op(cid, payload, "delete"),
            "contact_update": lambda: self._handle_contact_op(cid, payload, "update"),
            "contact_list": lambda: self._handle_contact_op(cid, payload, "list"),
            "contact_search": lambda: self._handle_contact_op(cid, payload, "search"),
            "online_query": lambda: self._handle_online_query(cid),
            "call_invite": lambda: self._handle_call_invite(cid, payload),
            "call_accept": lambda: self._handle_call_accept(cid, payload),
            "call_reject": lambda: self._handle_call_reject(cid, payload),
            "call_hangup": lambda: self._handle_call_hangup(cid, payload),
            "direct_path_seen": lambda: self._handle_direct_path_seen(cid, payload),
            "media_stop": lambda: self._handle_media_stop(cid, payload),
            "room_create": lambda: self._handle_room_create(cid, payload),
            "room_invite": lambda: self._handle_room_invite(cid, payload),
            "room_join": lambda: self._handle_room_join(cid, payload),
            "room_leave": lambda: self._handle_room_leave(cid, payload),
            "room_dismiss": lambda: self._handle_room_dismiss(cid, payload),
            "room_audio_chunk": lambda: self._forward_room_audio(cid, payload),
            "quality_report": lambda: self._handle_quality_report(cid, payload),
        }
        handler = handlers.get(message_type)
        if handler:
            handler()

    def _handle_login(self, cid, payload, conn) -> None:
        username = payload.get("username", "")
        if not username:
            self._send(conn, encode_response(False, "empty username"))
            return
        try:
            media_port = int(payload.get("media_port", 0) or 0)
        except (TypeError, ValueError):
            media_port = 0
        local_ip = str(payload.get("local_ip", "")).strip()
        self._data_store.ensure_user(username)
        with self._lock:
            if username in self._username_map:
                self._send(conn, encode_response(False, "user already online"))
                return
            info = self._clients.get(cid)
            if info:
                info.username = username
                info.media_port = max(media_port, 0)
                info.local_ip = local_ip or info.addr[0]
                if info.media_port > 0:
                    info.call_udp_observed_addr = (info.addr[0], info.media_port)
            self._username_map[username] = cid
        self._send(conn, encode_response(True, "login ok"))
        print(f"[Server] {username} logged in")

    def _handle_logout(self, cid) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            username = info.username
            room_id = info.room_id
            target = info.in_call_with or info.pending_peer
            call_id = info.call_id
        if call_id:
            self._discard_session(call_id)
        if target:
            self._end_call(username, target)
        if room_id:
            self._remove_from_room(username, room_id)
        with self._lock:
            info = self._clients.get(cid)
            if info:
                info.username = ""
                info.room_id = ""
                self._reset_client_audio_state(info)
                self._reset_client_call_state(info)
            self._username_map.pop(username, None)
        print(f"[Server] {username} logged out")

    def _handle_disconnect(self, cid) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info:
                return
            username = info.username
            room_id = info.room_id
            target = info.in_call_with or info.pending_peer
            call_id = info.call_id

        if call_id:
            self._discard_session(call_id)
        if username and target:
            self._end_call(username, target)
        if username and room_id:
            self._remove_from_room(username, room_id)

        with self._lock:
            info = self._clients.pop(cid, None)
            if info and info.username:
                self._username_map.pop(info.username, None)
            elif username:
                self._username_map.pop(username, None)
        if info:
            try:
                info.conn.close()
            except Exception:
                pass

    def _handle_contact_op(self, cid, payload, op) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "not logged in"))
                return
            username = info.username

        if op == "add":
            contact_name = payload.get("contact_name", "")
            if not contact_name:
                self._send_by_cid(cid, encode_response(False, "empty contact name"))
                return
            ok, msg = self._data_store.add_contact(username, contact_name)
            self._send_by_cid(cid, encode_response(ok, msg))
        elif op == "delete":
            contact_name = payload.get("contact_name", "")
            ok, msg = self._data_store.delete_contact(username, contact_name)
            self._send_by_cid(cid, encode_response(ok, msg))
        elif op == "update":
            old_name = payload.get("old_name", "")
            new_name = payload.get("new_name", "")
            if not old_name or not new_name:
                self._send_by_cid(cid, encode_response(False, "empty contact name"))
                return
            ok, msg = self._data_store.update_contact(username, old_name, new_name)
            self._send_by_cid(cid, encode_response(ok, msg))
        elif op == "list":
            contacts = self._data_store.get_contacts(username)
            self._send_by_cid(
                cid, encode_response(True, "ok", {"contacts": contacts})
            )
        elif op == "search":
            keyword = payload.get("keyword", "")
            contacts = self._data_store.search_contacts(username, keyword)
            self._send_by_cid(
                cid, encode_response(True, "ok", {"contacts": contacts})
            )

    def _handle_online_query(self, cid) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "not logged in"))
                return
            online_users = list(self._username_map.keys())
        self._send_by_cid(
            cid, encode_response(True, "ok", {"online_users": online_users})
        )

    def _reset_client_call_state(self, info: ClientInfo) -> None:
        info.pending_peer = ""
        info.in_call_with = ""
        info.call_mode = ""
        info.call_id = ""
        info.call_udp_observed_addr = (
            (info.addr[0], info.media_port) if info.media_port > 0 else None
        )

    def _is_private_call_busy(self, info: ClientInfo) -> bool:
        return bool(info.pending_peer or info.in_call_with)

    def _reset_client_audio_state(self, info: ClientInfo) -> None:
        info.udp_addr = None
        with info.audio_lock:
            info.audio_state = ClientAudioState()

    def _build_client_audio_payload(self, info: ClientInfo) -> dict:
        with info.audio_lock:
            profile = get_profile_by_name(info.audio_state.profile_name)
            return {
                "adaptive_profile": profile.name,
                "audio_format": profile.audio_format.to_payload(),
            }

    def _handle_quality_report(self, cid, payload) -> None:
        room_id = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username or info.room_id != room_id:
                return

        try:
            delay_ms = max(0.0, float(payload.get("delay_ms", 0.0)))
            jitter_ms = max(0.0, float(payload.get("jitter_ms", 0.0)))
            packet_loss_percent = max(
                0.0, float(payload.get("packet_loss_percent", 0.0))
            )
        except (TypeError, ValueError):
            return

        with info.audio_lock:
            state = info.audio_state
            previous_profile = state.profile_name
            profile = choose_adaptive_profile(
                delay_ms=delay_ms,
                jitter_ms=jitter_ms,
                packet_loss_percent=packet_loss_percent,
                current_profile_name=previous_profile,
            )
            state.delay_ms = delay_ms
            state.jitter_ms = jitter_ms
            state.packet_loss_percent = packet_loss_percent
            state.last_reported_at = time.time()
            if profile.name != previous_profile:
                state.profile_name = profile.name
                for transcoder in state.transcoders.values():
                    transcoder.update_output_format(profile.audio_format)

        if profile.name != previous_profile:
            print(
                f"[Server] Adaptive profile for {info.username}: "
                f"{previous_profile} -> {profile.name} "
                f"(delay={delay_ms:.0f}ms, jitter={jitter_ms:.0f}ms, "
                f"loss={packet_loss_percent:.1f}%)"
            )

    def _handle_call_invite(self, cid, payload) -> None:
        target = str(payload.get("target", "")).strip()
        if not target:
            return

        with self._lock:
            caller_info = self._clients.get(cid)
            if not caller_info or not caller_info.username:
                return
            caller = caller_info.username

            if caller == target:
                self._send_by_cid(cid, encode_response(False, "cannot call yourself"))
                return
            if caller_info.room_id:
                self._send_by_cid(cid, encode_response(False, "leave room before private call"))
                return
            if self._is_private_call_busy(caller_info):
                self._send_by_cid(cid, encode_response(False, "caller is busy"))
                return

            target_cid = self._username_map.get(target)
            target_info = self._clients.get(target_cid) if target_cid else None
            if not target_info:
                self._send_by_cid(cid, encode_call_not_found(target))
                return
            if target_info.room_id or self._is_private_call_busy(target_info):
                self._send_by_cid(cid, encode_call_busy(target))
                return

            caller_info.pending_peer = target
            target_info.pending_peer = caller

        invite = {"type": "call_invite", "caller": caller}
        self._send_by_cid(target_cid, json.dumps(invite, ensure_ascii=False))

    def _handle_call_accept(self, cid, payload) -> None:
        caller = str(payload.get("caller", "")).strip()
        if not caller:
            return

        with self._lock:
            callee_info = self._clients.get(cid)
            if not callee_info or not callee_info.username:
                return
            callee = callee_info.username
            caller_cid = self._username_map.get(caller)
            caller_info = self._clients.get(caller_cid) if caller_cid else None
            if not caller_info:
                self._send_by_cid(cid, encode_call_not_found(caller))
                return
            if callee_info.room_id or caller_info.room_id:
                self._send_by_cid(cid, encode_response(False, "room members cannot start private calls"))
                return
            if callee_info.pending_peer != caller or caller_info.pending_peer != callee:
                self._send_by_cid(cid, encode_response(False, "no matching pending call"))
                return

            call_id = uuid.uuid4().hex
            session = CallSession(call_id=call_id, caller=caller, callee=callee)
            self._sessions[call_id] = session

            caller_info.pending_peer = ""
            callee_info.pending_peer = ""
            caller_info.in_call_with = callee
            callee_info.in_call_with = caller
            caller_info.call_mode = "negotiating"
            callee_info.call_mode = "negotiating"
            caller_info.call_id = call_id
            callee_info.call_id = call_id

            caller_ready = encode_call_ready(
                call_id=call_id,
                peer=callee,
                mode="negotiating",
                peer_ip=callee_info.local_ip,
                peer_port=callee_info.media_port,
                relay_port=self._port,
                detail="Trying direct UDP first; will fall back to relay if needed.",
            )
            callee_ready = encode_call_ready(
                call_id=call_id,
                peer=caller,
                mode="negotiating",
                peer_ip=caller_info.local_ip,
                peer_port=caller_info.media_port,
                relay_port=self._port,
                detail="Trying direct UDP first; will fall back to relay if needed.",
            )

        self._send_by_cid(caller_cid, caller_ready)
        self._send_by_cid(cid, callee_ready)
        self._arm_negotiation_timer(call_id)

    def _handle_call_reject(self, cid, payload) -> None:
        caller = str(payload.get("caller", "")).strip()
        reason = str(payload.get("reason", "")).strip() or "call rejected"

        with self._lock:
            callee_info = self._clients.get(cid)
            if not callee_info or not callee_info.username:
                return
            callee = callee_info.username
            caller_cid = self._username_map.get(caller)
            caller_info = self._clients.get(caller_cid) if caller_cid else None

            callee_info.pending_peer = ""
            if caller_info and caller_info.pending_peer == callee:
                caller_info.pending_peer = ""

        if caller_cid:
            reject_msg = {
                "type": "call_reject",
                "caller": callee,
                "reason": reason,
            }
            self._send_by_cid(caller_cid, json.dumps(reject_msg, ensure_ascii=False))

    def _handle_call_hangup(self, cid, payload) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            username = info.username
            target = str(payload.get("target", "")).strip() or info.in_call_with or info.pending_peer

        self._end_call(username, target)

    def _handle_direct_path_seen(self, cid, payload) -> None:
        call_id = str(payload.get("call_id", "")).strip()
        if not call_id:
            return

        finalize = False
        with self._lock:
            info = self._clients.get(cid)
            session = self._sessions.get(call_id)
            if not info or not info.username or not session or session.final_mode:
                return
            if info.username not in (session.caller, session.callee):
                return
            session.direct_seen.add(info.username)
            finalize = len(session.direct_seen) == 2

        if finalize:
            self._finalize_session(
                call_id, "p2p", "both peers confirmed direct UDP path"
            )

    def _handle_media_stop(self, cid, payload) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            source = info.username
            target = str(payload.get("target", "")).strip() or info.in_call_with
            target_cid = self._username_map.get(target) if target else None
        if not target_cid:
            return
        msg = {"type": "media_stop", "peer": source}
        self._send_by_cid(target_cid, json.dumps(msg, ensure_ascii=False))

    def _end_call(self, username: str, target: str, notify: bool = True) -> None:
        if not username or not target:
            return

        with self._lock:
            user_cid = self._username_map.get(username)
            user_info = self._clients.get(user_cid) if user_cid else None
            target_cid = self._username_map.get(target)
            target_info = self._clients.get(target_cid) if target_cid else None
            call_id = ""
            if user_info:
                call_id = user_info.call_id
                self._reset_client_call_state(user_info)
            if target_info:
                if target_info.pending_peer == username or target_info.in_call_with == username:
                    call_id = call_id or target_info.call_id
                    self._reset_client_call_state(target_info)

        if call_id:
            self._discard_session(call_id)
        if notify and target_cid:
            hangup = {"type": "call_hangup", "peer": username}
            self._send_by_cid(target_cid, json.dumps(hangup, ensure_ascii=False))

    def _arm_negotiation_timer(self, call_id: str) -> None:
        timer = threading.Timer(
            NEGOTIATION_TIMEOUT_SEC,
            lambda: self._finalize_session(
                call_id, "relay", "direct UDP was not confirmed in time"
            ),
        )
        timer.daemon = True
        with self._lock:
            session = self._sessions.get(call_id)
            if not session:
                return
            session.timer = timer
        timer.start()

    def _finalize_session(self, call_id: str, mode: str, reason: str) -> None:
        with self._lock:
            session = self._sessions.get(call_id)
            if not session or session.final_mode:
                return
            session.final_mode = mode
            if session.timer:
                session.timer.cancel()
                session.timer = None

            caller_cid = self._username_map.get(session.caller)
            callee_cid = self._username_map.get(session.callee)
            caller_info = self._clients.get(caller_cid) if caller_cid else None
            callee_info = self._clients.get(callee_cid) if callee_cid else None
            if caller_info and caller_info.call_id == call_id:
                caller_info.call_mode = mode
            if callee_info and callee_info.call_id == call_id:
                callee_info.call_mode = mode

            caller_msg = encode_transport_update(call_id, session.callee, mode, reason)
            callee_msg = encode_transport_update(call_id, session.caller, mode, reason)

        self._send_by_cid(caller_cid, caller_msg)
        self._send_by_cid(callee_cid, callee_msg)

    def _discard_session(self, call_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(call_id, None)
        if session and session.timer:
            session.timer.cancel()

    def _adapt_audio_chunks_for_client(
        self,
        target_cid: int,
        sender: str,
        raw: bytes,
        source_format: AudioFormat,
    ) -> tuple[str, dict, List[tuple[int, int, bytes]]]:
        with self._lock:
            info = self._clients.get(target_cid)
        if not info:
            default_format = DEFAULT_ADAPTIVE_PROFILE.audio_format.to_payload()
            return DEFAULT_ADAPTIVE_PROFILE.name, default_format, []

        with info.audio_lock:
            state = info.audio_state
            profile = get_profile_by_name(state.profile_name)
            transcoder = state.transcoders.get(sender)
            if transcoder is None:
                transcoder = ReframingAudioTranscoder(profile.audio_format)
                state.transcoders[sender] = transcoder
            else:
                transcoder.update_output_format(profile.audio_format)

            chunks = transcoder.feed(raw, source_format)
            packet_chunks: List[tuple[int, int, bytes]] = []
            for chunk in chunks:
                seq = state.outgoing_seq.get(sender, 0)
                state.outgoing_seq[sender] = (seq + 1) & 0xFFFFFFFF
                packet_chunks.append((seq, int(time.time() * 1000), chunk))

        return profile.name, profile.audio_format.to_payload(), packet_chunks

    def _handle_room_create(self, cid, payload=None) -> None:
        payload = payload or {}
        audio_protocol = payload.get("audio_protocol", "tcp")
        if audio_protocol not in ("tcp", "udp"):
            audio_protocol = "tcp"

        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "not logged in"))
                return
            if info.room_id:
                self._send_by_cid(cid, encode_response(False, "already in room"))
                return
            if self._is_private_call_busy(info):
                self._send_by_cid(
                    cid, encode_response(False, "finish private call before creating room")
                )
                return

            creator = info.username
            room_id = uuid.uuid4().hex[:8]
            room = ChatRoom(room_id=room_id, creator=creator, audio_protocol=audio_protocol)
            position = room.assign_position(creator)
            info.room_id = room_id
            self._reset_client_audio_state(info)

            if audio_protocol == "udp":
                udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp_sock.bind((self._host, 0))
                room.udp_port = udp_sock.getsockname()[1]
                room._udp_sock = udp_sock
                threading.Thread(
                    target=self._udp_recv_loop, args=(room_id,), daemon=True
                ).start()
                print(f"[Server] Room {room_id} UDP port: {room.udp_port}")

            self._rooms[room_id] = room
            profile_data = self._build_client_audio_payload(info)

        response_data = {
            "room_id": room_id,
            "position": position,
            "audio_protocol": audio_protocol,
            **profile_data,
        }
        if audio_protocol == "udp":
            response_data["udp_port"] = room.udp_port
        self._send_by_cid(cid, encode_response(True, "room created", response_data))
        print(f"[Server] Room {room_id} created by {creator} (audio: {audio_protocol})")
        self._broadcast_member_update(room_id)

    def _handle_room_invite(self, cid, payload) -> None:
        room_id = payload.get("room_id", "")
        target = payload.get("target", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "not logged in"))
                return
            inviter = info.username
            room = self._rooms.get(room_id)
            if not room:
                self._send_by_cid(cid, encode_response(False, "room not found"))
                return
            if self._is_private_call_busy(info):
                self._send_by_cid(
                    cid, encode_response(False, "finish private call before inviting")
                )
                return
            if len(room.members) >= MAX_ROOM_SIZE:
                self._send_by_cid(cid, encode_response(False, "room full"))
                return
            if target in room.members:
                self._send_by_cid(cid, encode_response(False, "target already in room"))
                return
            if target not in self._username_map:
                self._send_by_cid(cid, encode_response(False, "target offline"))
                return
            target_cid = self._username_map[target]
            target_info = self._clients.get(target_cid)
            if target_info and (target_info.room_id or self._is_private_call_busy(target_info)):
                self._send_by_cid(cid, encode_response(False, "target busy"))
                return
            room.invited.add(target)

        self._send_by_cid(target_cid, encode_room_invite_notify(room_id, inviter, target))
        self._send_by_cid(cid, encode_response(True, "invite sent"))
        print(f"[Server] {inviter} invited {target} to room {room_id}")

    def _handle_room_join(self, cid, payload) -> None:
        room_id = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "not logged in"))
                return
            username = info.username
            if info.room_id:
                self._send_by_cid(cid, encode_response(False, "already in room"))
                return
            if self._is_private_call_busy(info):
                self._send_by_cid(
                    cid, encode_response(False, "finish private call before joining room")
                )
                return
            room = self._rooms.get(room_id)
            if not room:
                self._send_by_cid(cid, encode_response(False, "room not found"))
                return
            if username not in room.invited and username != room.creator:
                self._send_by_cid(cid, encode_response(False, "not invited"))
                return
            if len(room.members) >= MAX_ROOM_SIZE:
                self._send_by_cid(cid, encode_response(False, "room full"))
                return

            position = room.assign_position(username)
            room.invited.discard(username)
            info.room_id = room_id
            self._reset_client_audio_state(info)
            profile_data = self._build_client_audio_payload(info)
            response_data = {
                "room_id": room_id,
                "position": position,
                "creator": room.creator,
                "audio_protocol": room.audio_protocol,
                **profile_data,
            }
            if room.audio_protocol == "udp":
                response_data["udp_port"] = room.udp_port

        self._send_by_cid(cid, encode_response(True, "join ok", response_data))
        print(f"[Server] {username} joined room {room_id} at pos {position}")
        self._broadcast_member_update(room_id)

    def _handle_room_leave(self, cid, payload) -> None:
        room_id = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            username = info.username
        self._remove_from_room(username, room_id)
        self._send_by_cid(cid, encode_response(True, "left room"))

    def _handle_room_dismiss(self, cid, payload) -> None:
        room_id = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            username = info.username
            room = self._rooms.get(room_id)
            if not room:
                self._send_by_cid(cid, encode_response(False, "room not found"))
                return
            if room.creator != username:
                self._send_by_cid(cid, encode_response(False, "only creator can dismiss"))
                return

            dismiss_msg = encode_room_dismissed_notify(room_id)
            for member in list(room.members.keys()):
                if member not in self._username_map:
                    continue
                member_cid = self._username_map[member]
                member_info = self._clients.get(member_cid)
                if member_info:
                    member_info.room_id = ""
                    self._reset_client_audio_state(member_info)
                self._send_by_cid(member_cid, dismiss_msg)
            self._close_room_udp(room)
            del self._rooms[room_id]

        print(f"[Server] Room {room_id} dismissed by {username}")

    def _remove_from_room(self, username: str, room_id: str) -> None:
        with self._lock:
            room = self._rooms.get(room_id)
            if not room or username not in room.members:
                return

            if room.creator == username:
                dismiss_msg = encode_room_dismissed_notify(room_id)
                for member in list(room.members.keys()):
                    if member not in self._username_map:
                        continue
                    member_cid = self._username_map[member]
                    member_info = self._clients.get(member_cid)
                    if member_info:
                        member_info.room_id = ""
                        self._reset_client_audio_state(member_info)
                    self._send_by_cid(member_cid, dismiss_msg)
                self._close_room_udp(room)
                del self._rooms[room_id]
                print(f"[Server] Room {room_id} dismissed (creator left)")
                return

            del room.members[username]
            if username in self._username_map:
                user_cid = self._username_map[username]
                user_info = self._clients.get(user_cid)
                if user_info:
                    user_info.room_id = ""
                    self._reset_client_audio_state(user_info)

        print(f"[Server] {username} left room {room_id}")
        self._broadcast_member_update(room_id)

    def _broadcast_member_update(self, room_id: str) -> None:
        with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return
            members = room.get_member_list()
            positions = dict(room.members)
            msg = encode_room_member_update(room_id, members, positions)
            recipients = [
                self._username_map[member]
                for member in list(room.members.keys())
                if member in self._username_map
            ]
        for target_cid in recipients:
            self._send_by_cid(target_cid, msg)

    def _forward_room_audio(self, cid, payload) -> None:
        recipients: List[int] = []
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.room_id:
                return
            room = self._rooms.get(info.room_id)
            if not room:
                return
            room_id = info.room_id
            sender = info.username
            for member in list(room.members.keys()):
                if member == sender:
                    continue
                if member in self._username_map:
                    recipients.append(self._username_map[member])

        try:
            raw = base64.b64decode(payload.get("data", ""))
        except Exception:
            return
        if not raw:
            return

        source_format = AudioFormat.from_payload(
            payload.get("audio_format"), CANONICAL_AUDIO_FORMAT
        )
        for target_cid in recipients:
            profile_name, audio_format, packet_chunks = self._adapt_audio_chunks_for_client(
                target_cid, sender, raw, source_format
            )
            for seq, timestamp_ms, chunk in packet_chunks:
                msg = encode_room_audio_chunk(
                    room_id,
                    sender,
                    chunk,
                    seq=seq,
                    timestamp_ms=timestamp_ms,
                    audio_format=audio_format,
                    profile=profile_name,
                )
                self._send_audio_by_cid(target_cid, msg)

    def _udp_recv_loop(self, room_id: str) -> None:
        while self._running:
            with self._lock:
                room = self._rooms.get(room_id)
                if not room or not room._udp_sock:
                    break
                udp_sock = room._udp_sock

            try:
                udp_sock.settimeout(1.0)
                data, addr = udp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                sender, _, _, audio_data, audio_format = decode_udp_audio_packet(data)
            except ValueError:
                continue
            if not sender or not audio_data:
                continue

            recipients: List[Tuple[int, Tuple[str, int]]] = []
            with self._lock:
                room = self._rooms.get(room_id)
                if not room or not room._udp_sock:
                    break

                if sender in self._username_map:
                    sender_cid = self._username_map[sender]
                    sender_info = self._clients.get(sender_cid)
                    if sender_info:
                        sender_info.udp_addr = addr

                for member in list(room.members.keys()):
                    if member == sender or member not in self._username_map:
                        continue
                    member_cid = self._username_map[member]
                    member_info = self._clients.get(member_cid)
                    if member_info and member_info.udp_addr:
                        recipients.append((member_cid, member_info.udp_addr))

            source_format = AudioFormat.from_payload(audio_format, CANONICAL_AUDIO_FORMAT)
            for target_cid, target_addr in recipients:
                _, target_audio_format, packet_chunks = self._adapt_audio_chunks_for_client(
                    target_cid, sender, audio_data, source_format
                )
                for seq, timestamp_ms, chunk in packet_chunks:
                    packet = encode_udp_audio_packet(
                        sender,
                        chunk,
                        seq=seq,
                        timestamp_ms=timestamp_ms,
                        audio_format=target_audio_format,
                    )
                    self._send_udp_audio(udp_sock, packet, target_addr)

        print(f"[Server] UDP recv loop for room {room_id} stopped")

    def _private_call_udp_loop(self) -> None:
        while self._running:
            try:
                data, addr = self._media_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                packet_type, packet = decode_media_packet(data)
            except ValueError:
                continue

            sender = str(packet.get("sender", "")).strip()
            if not sender:
                continue

            with self._lock:
                sender_cid = self._username_map.get(sender)
                sender_info = self._clients.get(sender_cid) if sender_cid else None
                if sender_info:
                    sender_info.call_udp_observed_addr = addr

            if not sender_info:
                continue

            if packet_type == "media_probe":
                continue

            target = str(packet.get("target", "")).strip()
            if not target or sender_info.in_call_with != target:
                continue
            if sender_info.call_mode == "p2p":
                continue

            with self._lock:
                target_cid = self._username_map.get(target)
                target_info = self._clients.get(target_cid) if target_cid else None
                if not target_info:
                    continue
                forward_addr = target_info.call_udp_observed_addr
                if not forward_addr and target_info.media_port > 0:
                    forward_addr = (target_info.addr[0], target_info.media_port)

            if not forward_addr:
                continue
            self._send_udp_audio(self._media_sock, data, forward_addr)

    def _close_room_udp(self, room: ChatRoom) -> None:
        if room._udp_sock:
            try:
                room._udp_sock.close()
            except Exception:
                pass
            room._udp_sock = None

    def _should_drop_audio(self) -> bool:
        return (
            self._impairment.loss_rate > 0
            and random.random() < self._impairment.loss_rate
        )

    def _send_audio_by_cid(self, cid: int, msg: str) -> None:
        if self._should_drop_audio():
            return
        delay = self._impairment.sampled_delay_seconds()
        if delay <= 0:
            self._send_by_cid(cid, msg)
            return
        timer = threading.Timer(delay, self._send_by_cid, args=(cid, msg))
        timer.daemon = True
        timer.start()

    def _send_udp_audio(
        self, udp_sock: socket.socket, data: bytes, target_addr: Tuple[str, int]
    ) -> None:
        if self._should_drop_audio():
            return

        def send_now() -> None:
            try:
                udp_sock.sendto(data, target_addr)
            except Exception:
                pass

        delay = self._impairment.sampled_delay_seconds()
        if delay <= 0:
            send_now()
            return
        timer = threading.Timer(delay, send_now)
        timer.daemon = True
        timer.start()

    def _send(self, conn: socket.socket, msg: str) -> None:
        try:
            conn.sendall((msg + MESSAGE_DELIMITER).encode("utf-8"))
        except Exception:
            pass

    def _send_by_cid(self, cid, msg: str) -> None:
        with self._lock:
            info = self._clients.get(cid)
        if not info:
            return
        with info.send_lock:
            self._send(info.conn, msg)


def main() -> None:
    server = ConferenceServer()
    try:
        print("=" * 50)
        print("Task 5 - Multi-party Voice Conference Server")
        print("=" * 50)
        print(f"Port: {DEFAULT_PORT}")
        print("Ctrl+C to stop")
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
