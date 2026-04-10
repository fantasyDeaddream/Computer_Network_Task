"""
Task 3 server:
- TCP for signaling and roster updates
- UDP for relay fallback while direct P2P is negotiated
"""

from __future__ import annotations

import json
import socket
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple
import sys

from stream_protocol import (
    decode_media_packet,
    decode_message,
    encode_call_busy,
    encode_call_not_found,
    encode_call_ready,
    encode_response,
    encode_text,
    encode_transport_update,
    encode_user_list,
)


NEGOTIATION_TIMEOUT_SEC = 1.6


def _ensure_task2_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task2_dir = base / "任务2"
    if str(task2_dir) not in sys.path:
        sys.path.insert(0, str(task2_dir))


_ensure_task2_on_path()

from audio_config import DEFAULT_PORT, MESSAGE_DELIMITER  # type: ignore  # noqa: E402


@dataclass
class ClientInfo:
    conn: socket.socket
    addr: Tuple[str, int]
    nickname: str = ""
    media_port: int = 0
    local_ip: str = ""
    pending_peer: str = ""
    in_call_with: str = ""
    call_mode: str = ""
    call_id: str = ""
    udp_observed_addr: Optional[Tuple[str, int]] = None


@dataclass
class CallSession:
    call_id: str
    caller: str
    callee: str
    direct_seen: set[str] = field(default_factory=set)
    final_mode: str = ""
    timer: Optional[threading.Timer] = None


class StreamServer:
    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port

        self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._udp_sock.settimeout(0.5)

        self._clients: Dict[int, ClientInfo] = {}
        self._nickname_map: Dict[str, int] = {}
        self._sessions: Dict[str, CallSession] = {}
        self._lock = threading.RLock()
        self._running = False
        self._udp_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._tcp_sock.bind((self._host, self._port))
        self._tcp_sock.listen(10)
        self._udp_sock.bind((self._host, self._port))
        self._running = True

        print(f"[Server] Signaling TCP listen on {self._host}:{self._port}")
        print(f"[Server] Media relay UDP bind on {self._host}:{self._port}")

        self._udp_thread = threading.Thread(target=self._udp_loop, daemon=True)
        self._udp_thread.start()

        try:
            while self._running:
                try:
                    conn, addr = self._tcp_sock.accept()
                except OSError:
                    break
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True,
                ).start()
        finally:
            self._cleanup()
            print("[Server] StreamServer stopped")

    def stop(self) -> None:
        self._running = False
        try:
            self._tcp_sock.close()
        except Exception:
            pass
        try:
            self._udp_sock.close()
        except Exception:
            pass

    def _cleanup(self) -> None:
        with self._lock:
            for session in list(self._sessions.values()):
                if session.timer:
                    session.timer.cancel()
            self._sessions.clear()

            for info in list(self._clients.values()):
                try:
                    info.conn.close()
                except Exception:
                    pass
            self._clients.clear()
            self._nickname_map.clear()

        try:
            self._tcp_sock.close()
        except Exception:
            pass
        try:
            self._udp_sock.close()
        except Exception:
            pass

    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        cid = id(conn)
        with self._lock:
            self._clients[cid] = ClientInfo(conn=conn, addr=addr)

        print(f"[Server] TCP connected from {addr}")
        buffer = ""

        try:
            while self._running:
                data = conn.recv(4096)
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
                        print(f"[Server] Invalid control message: {exc}")
                        continue
                    self._process_message(cid, msg_type, payload)
        except ConnectionResetError:
            pass
        finally:
            self._handle_disconnect(cid)
            print(f"[Server] TCP disconnected from {addr}")

    def _process_message(self, cid: int, msg_type: str, payload: dict) -> None:
        if msg_type == "login":
            self._handle_login(cid, payload)
        elif msg_type == "text":
            self._handle_text(cid, payload)
        elif msg_type == "call_invite":
            self._handle_call_invite(cid, payload)
        elif msg_type == "call_accept":
            self._handle_call_accept(cid, payload)
        elif msg_type == "call_reject":
            self._handle_call_reject(cid, payload)
        elif msg_type == "call_hangup":
            self._handle_call_hangup(cid, payload)
        elif msg_type == "direct_path_seen":
            self._handle_direct_path_seen(cid, payload)
        elif msg_type == "media_stop":
            self._handle_media_stop(cid, payload)

    def _handle_login(self, cid: int, payload: dict) -> None:
        nickname = str(payload.get("nickname", "")).strip()
        media_port = int(payload.get("media_port", 0) or 0)
        local_ip = str(payload.get("local_ip", "")).strip()

        if not nickname:
            self._send_by_cid(cid, encode_response(False, "Nickname is required"))
            return
        if media_port <= 0:
            self._send_by_cid(cid, encode_response(False, "Invalid UDP media port"))
            return

        with self._lock:
            if nickname in self._nickname_map:
                self._send_by_cid(cid, encode_response(False, "Nickname already online"))
                return

            info = self._clients.get(cid)
            if not info:
                return

            info.nickname = nickname
            info.media_port = media_port
            info.local_ip = local_ip or info.addr[0]
            info.udp_observed_addr = (info.addr[0], media_port)
            self._nickname_map[nickname] = cid
            users = sorted(self._nickname_map.keys())

        print(
            "[Server] Login:",
            json.dumps(
                {
                    "nickname": nickname,
                    "tcp_addr": self._clients[cid].addr,
                    "local_ip": local_ip or self._clients[cid].addr[0],
                    "media_port": media_port,
                },
                ensure_ascii=False,
            ),
        )
        self._send_by_cid(
            cid,
            encode_response(True, "Login success", {"users": users}),
        )
        self._broadcast_user_list()

    def _handle_text(self, cid: int, payload: dict) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.nickname:
                self._send_by_cid(cid, encode_response(False, "Please login first"))
                return
            source = info.nickname
            target = str(payload.get("target", "")).strip() or info.in_call_with

        if not target:
            self._send_by_cid(cid, encode_response(False, "No target user selected"))
            return

        content = str(payload.get("content", "")).strip()
        if not content:
            return

        with self._lock:
            target_cid = self._nickname_map.get(target)
        if not target_cid:
            self._send_by_cid(cid, encode_response(False, f"User {target} is offline"))
            return

        self._send_by_cid(target_cid, encode_text(f"{source}: {content}", source))
        self._send_by_cid(cid, encode_response(True, "Text delivered"))

    def _handle_call_invite(self, cid: int, payload: dict) -> None:
        target = str(payload.get("target", "")).strip()
        if not target:
            return

        with self._lock:
            caller_info = self._clients.get(cid)
            if not caller_info or not caller_info.nickname:
                return
            caller = caller_info.nickname

            if caller == target:
                self._send_by_cid(cid, encode_response(False, "Cannot call yourself"))
                return

            if caller_info.in_call_with or caller_info.pending_peer:
                self._send_by_cid(cid, encode_response(False, "Caller is busy"))
                return

            target_cid = self._nickname_map.get(target)
            target_info = self._clients.get(target_cid) if target_cid else None
            if not target_info:
                self._send_by_cid(cid, encode_call_not_found(target))
                return

            if target_info.in_call_with or target_info.pending_peer:
                self._send_by_cid(cid, encode_call_busy(target))
                return

            caller_info.pending_peer = target
            target_info.pending_peer = caller

        invite = json.dumps({"type": "call_invite", "caller": caller}, ensure_ascii=False)
        self._send_by_cid(target_cid, invite)
        print(f"[Server] Call invite: {caller} -> {target}")

    def _handle_call_accept(self, cid: int, payload: dict) -> None:
        caller = str(payload.get("caller", "")).strip()
        if not caller:
            return

        with self._lock:
            callee_info = self._clients.get(cid)
            if not callee_info or not callee_info.nickname:
                return
            callee = callee_info.nickname

            caller_cid = self._nickname_map.get(caller)
            caller_info = self._clients.get(caller_cid) if caller_cid else None
            if not caller_info:
                self._send_by_cid(cid, encode_call_not_found(caller))
                return

            if callee_info.pending_peer != caller or caller_info.pending_peer != callee:
                self._send_by_cid(cid, encode_response(False, "No matching pending call"))
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
                detail="Trying direct UDP first; will fall back to relay if negotiation fails.",
            )
            callee_ready = encode_call_ready(
                call_id=call_id,
                peer=caller,
                mode="negotiating",
                peer_ip=caller_info.local_ip,
                peer_port=caller_info.media_port,
                relay_port=self._port,
                detail="Trying direct UDP first; will fall back to relay if negotiation fails.",
            )

        self._send_by_cid(caller_cid, caller_ready)
        self._send_by_cid(cid, callee_ready)
        self._arm_negotiation_timer(call_id)
        print(f"[Server] Call established: {caller} <-> {callee}; starting P2P negotiation")

    def _handle_call_reject(self, cid: int, payload: dict) -> None:
        caller = str(payload.get("caller", "")).strip()
        reason = str(payload.get("reason", "")).strip() or "Call rejected"

        with self._lock:
            callee_info = self._clients.get(cid)
            if not callee_info or not callee_info.nickname:
                return
            callee = callee_info.nickname
            caller_cid = self._nickname_map.get(caller)
            caller_info = self._clients.get(caller_cid) if caller_cid else None

            callee_info.pending_peer = ""
            if caller_info:
                caller_info.pending_peer = ""

        if caller_cid:
            reject_msg = json.dumps(
                {"type": "call_reject", "caller": callee, "reason": reason},
                ensure_ascii=False,
            )
            self._send_by_cid(caller_cid, reject_msg)
        print(f"[Server] Call rejected: {callee} rejected {caller}")

    def _handle_call_hangup(self, cid: int, payload: dict) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.nickname:
                return
            username = info.nickname
            target = str(payload.get("target", "")).strip() or info.in_call_with

        self._end_call(username, target)

    def _handle_direct_path_seen(self, cid: int, payload: dict) -> None:
        call_id = str(payload.get("call_id", "")).strip()
        if not call_id:
            return

        should_finalize = False
        with self._lock:
            info = self._clients.get(cid)
            session = self._sessions.get(call_id)
            if not info or not info.nickname or not session or session.final_mode:
                return
            if info.nickname not in (session.caller, session.callee):
                return

            if info.nickname not in session.direct_seen:
                session.direct_seen.add(info.nickname)
                print(
                    f"[Server] Direct UDP path seen for call {call_id}: "
                    f"{sorted(session.direct_seen)}"
                )
            should_finalize = len(session.direct_seen) == 2

        if should_finalize:
            self._finalize_session(call_id, mode="p2p", reason="both peers confirmed direct UDP")

    def _handle_media_stop(self, cid: int, payload: dict) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.nickname:
                return
            source = info.nickname
            target = str(payload.get("target", "")).strip() or info.in_call_with
            target_cid = self._nickname_map.get(target) if target else None

        if not target_cid:
            return

        msg = json.dumps({"type": "media_stop", "peer": source}, ensure_ascii=False)
        self._send_by_cid(target_cid, msg)
        print(f"[Server] Media stop forwarded: {source} -> {target}")

    def _handle_disconnect(self, cid: int) -> None:
        username = ""
        target = ""
        call_id = ""

        with self._lock:
            info = self._clients.pop(cid, None)
            if not info:
                return
            username = info.nickname
            target = info.in_call_with or info.pending_peer
            call_id = info.call_id
            if username:
                self._nickname_map.pop(username, None)

            if info.pending_peer:
                peer_cid = self._nickname_map.get(info.pending_peer)
                peer_info = self._clients.get(peer_cid) if peer_cid else None
                if peer_info and peer_info.pending_peer == username:
                    peer_info.pending_peer = ""

        if call_id:
            self._discard_session(call_id)
        if username and target:
            self._end_call(username, target, notify=True)

        try:
            conn = info.conn  # type: ignore[name-defined]
            conn.close()
        except Exception:
            pass

        if username:
            self._broadcast_user_list()

    def _end_call(self, username: str, target: str, notify: bool = True) -> None:
        if not username or not target:
            return

        target_cid: Optional[int]
        target_info: Optional[ClientInfo]
        call_id = ""

        with self._lock:
            user_cid = self._nickname_map.get(username)
            user_info = self._clients.get(user_cid) if user_cid else None
            if user_info:
                call_id = user_info.call_id
                user_info.pending_peer = ""
                user_info.in_call_with = ""
                user_info.call_mode = ""
                user_info.call_id = ""

            target_cid = self._nickname_map.get(target)
            target_info = self._clients.get(target_cid) if target_cid else None
            if target_info:
                if target_info.pending_peer == username:
                    target_info.pending_peer = ""
                if target_info.in_call_with == username:
                    call_id = call_id or target_info.call_id
                    target_info.in_call_with = ""
                    target_info.call_mode = ""
                    target_info.call_id = ""

        if call_id:
            self._discard_session(call_id)

        if notify and target_cid and target_info:
            hangup = json.dumps({"type": "call_hangup", "peer": username}, ensure_ascii=False)
            self._send_by_cid(target_cid, hangup)
        print(f"[Server] Call ended: {username} x {target}")

    def _arm_negotiation_timer(self, call_id: str) -> None:
        timer = threading.Timer(
            NEGOTIATION_TIMEOUT_SEC,
            lambda: self._finalize_session(
                call_id,
                mode="relay",
                reason="direct UDP was not confirmed in time",
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

            caller_cid = self._nickname_map.get(session.caller)
            callee_cid = self._nickname_map.get(session.callee)
            caller_info = self._clients.get(caller_cid) if caller_cid else None
            callee_info = self._clients.get(callee_cid) if callee_cid else None

            if caller_info and caller_info.call_id == call_id:
                caller_info.call_mode = mode
            if callee_info and callee_info.call_id == call_id:
                callee_info.call_mode = mode

            caller_msg = encode_transport_update(
                call_id=call_id,
                peer=session.callee,
                mode=mode,
                detail=reason,
            )
            callee_msg = encode_transport_update(
                call_id=call_id,
                peer=session.caller,
                mode=mode,
                detail=reason,
            )

        self._send_by_cid(caller_cid, caller_msg)
        self._send_by_cid(callee_cid, callee_msg)
        print(
            f"[Server] Transport finalized for call {call_id}: "
            f"{session.caller} <-> {session.callee} via {mode.upper()} ({reason})"
        )

    def _discard_session(self, call_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(call_id, None)
        if session and session.timer:
            session.timer.cancel()

    def _broadcast_user_list(self) -> None:
        with self._lock:
            users = sorted(self._nickname_map.keys())
            cids = list(self._nickname_map.values())
        msg = encode_user_list(users)
        for cid in cids:
            self._send_by_cid(cid, msg)
        print(f"[Server] Online users: {users}")

    def _udp_loop(self) -> None:
        while self._running:
            try:
                data, addr = self._udp_sock.recvfrom(65535)
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
                sender_cid = self._nickname_map.get(sender)
                sender_info = self._clients.get(sender_cid) if sender_cid else None
                if sender_info:
                    sender_info.udp_observed_addr = addr

            if not sender_info:
                continue

            if packet_type == "media_probe":
                print(f"[Server] UDP relay probe from {sender} at {addr}")
                continue

            target = str(packet.get("target", "")).strip()
            if not target or sender_info.in_call_with != target:
                continue

            if sender_info.call_mode == "p2p":
                continue

            with self._lock:
                target_cid = self._nickname_map.get(target)
                target_info = self._clients.get(target_cid) if target_cid else None
                if not target_info:
                    continue
                forward_addr = target_info.udp_observed_addr or (
                    target_info.addr[0],
                    target_info.media_port,
                )

            try:
                self._udp_sock.sendto(data, forward_addr)
            except Exception:
                pass

    def _send(self, conn: socket.socket, msg: str) -> None:
        try:
            conn.sendall((msg + MESSAGE_DELIMITER).encode("utf-8"))
        except Exception:
            pass

    def _send_by_cid(self, cid: Optional[int], msg: str) -> None:
        if not cid:
            return
        with self._lock:
            info = self._clients.get(cid)
        if info:
            self._send(info.conn, msg)


def main() -> None:
    server = StreamServer()
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
