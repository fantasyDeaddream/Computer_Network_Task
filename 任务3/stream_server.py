"""
Task 3 server:
- TCP for signaling and text
- UDP for media relay when peers are not in the same subnet
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
from dataclasses import dataclass
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
)


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
    subnet_prefix: int = 24
    pending_peer: str = ""
    in_call_with: str = ""
    call_mode: str = ""
    udp_observed_addr: Optional[Tuple[str, int]] = None


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
        elif msg_type == "media_stop":
            self._handle_media_stop(cid, payload)

    def _handle_login(self, cid: int, payload: dict) -> None:
        nickname = str(payload.get("nickname", "")).strip()
        media_port = int(payload.get("media_port", 0) or 0)
        local_ip = str(payload.get("local_ip", "")).strip()
        subnet_prefix = int(payload.get("subnet_prefix", 24) or 24)

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
            info.subnet_prefix = subnet_prefix
            info.udp_observed_addr = (info.addr[0], media_port)
            self._nickname_map[nickname] = cid

        print(
            "[Server] Login:",
            json.dumps(
                {
                    "nickname": nickname,
                    "tcp_addr": self._clients[cid].addr,
                    "local_ip": local_ip or self._clients[cid].addr[0],
                    "subnet_prefix": subnet_prefix,
                    "media_port": media_port,
                },
                ensure_ascii=False,
            ),
        )
        self._send_by_cid(cid, encode_response(True, "Login success"))

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

        invite = json.dumps(
            {"type": "call_invite", "caller": caller},
            ensure_ascii=False,
        )
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

            mode, detail = self._decide_call_mode(caller_info, callee_info)
            caller_info.pending_peer = ""
            callee_info.pending_peer = ""
            caller_info.in_call_with = callee
            callee_info.in_call_with = caller
            caller_info.call_mode = mode
            callee_info.call_mode = mode

            if mode == "p2p":
                caller_ready = encode_call_ready(
                    peer=callee,
                    mode=mode,
                    peer_ip=callee_info.local_ip,
                    peer_port=callee_info.media_port,
                    relay_port=self._port,
                    detail=detail,
                )
                callee_ready = encode_call_ready(
                    peer=caller,
                    mode=mode,
                    peer_ip=caller_info.local_ip,
                    peer_port=caller_info.media_port,
                    relay_port=self._port,
                    detail=detail,
                )
            else:
                caller_ready = encode_call_ready(
                    peer=callee,
                    mode=mode,
                    relay_port=self._port,
                    detail=detail,
                )
                callee_ready = encode_call_ready(
                    peer=caller,
                    mode=mode,
                    relay_port=self._port,
                    detail=detail,
                )

        self._send_by_cid(caller_cid, caller_ready)
        self._send_by_cid(cid, callee_ready)
        print(f"[Server] Call established: {caller} <-> {callee} via {mode.upper()} ({detail})")

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
                {
                    "type": "call_reject",
                    "caller": callee,
                    "reason": reason,
                },
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

        msg = json.dumps(
            {
                "type": "media_stop",
                "peer": source,
            },
            ensure_ascii=False,
        )
        self._send_by_cid(target_cid, msg)
        print(f"[Server] Media stop forwarded: {source} -> {target}")

    def _handle_disconnect(self, cid: int) -> None:
        username = ""
        target = ""

        with self._lock:
            info = self._clients.pop(cid, None)
            if not info:
                return
            username = info.nickname
            target = info.in_call_with or info.pending_peer
            if username:
                self._nickname_map.pop(username, None)

            if info.pending_peer:
                peer_cid = self._nickname_map.get(info.pending_peer)
                peer_info = self._clients.get(peer_cid) if peer_cid else None
                if peer_info and peer_info.pending_peer == username:
                    peer_info.pending_peer = ""

        if username and target:
            self._end_call(username, target, notify=True)

        try:
            conn = info.conn  # type: ignore[name-defined]
            conn.close()
        except Exception:
            pass

    def _end_call(self, username: str, target: str, notify: bool = True) -> None:
        if not username or not target:
            return

        target_cid: Optional[int]
        target_info: Optional[ClientInfo]

        with self._lock:
            user_cid = self._nickname_map.get(username)
            user_info = self._clients.get(user_cid) if user_cid else None
            if user_info:
                user_info.pending_peer = ""
                user_info.in_call_with = ""
                user_info.call_mode = ""

            target_cid = self._nickname_map.get(target)
            target_info = self._clients.get(target_cid) if target_cid else None
            if target_info:
                if target_info.pending_peer == username:
                    target_info.pending_peer = ""
                if target_info.in_call_with == username:
                    target_info.in_call_with = ""
                    target_info.call_mode = ""

        if notify and target_cid and target_info:
            hangup = json.dumps(
                {
                    "type": "call_hangup",
                    "peer": username,
                },
                ensure_ascii=False,
            )
            self._send_by_cid(target_cid, hangup)
        print(f"[Server] Call ended: {username} x {target}")

    def _decide_call_mode(self, caller: ClientInfo, callee: ClientInfo) -> tuple[str, str]:
        if self._same_subnet(
            caller.local_ip,
            caller.subnet_prefix,
            callee.local_ip,
            callee.subnet_prefix,
        ):
            return "p2p", f"same subnet {caller.local_ip}/{caller.subnet_prefix} <-> {callee.local_ip}/{callee.subnet_prefix}"
        return "relay", f"different subnet {caller.local_ip}/{caller.subnet_prefix} -> {callee.local_ip}/{callee.subnet_prefix}"

    def _same_subnet(
        self,
        ip_a: str,
        prefix_a: int,
        ip_b: str,
        prefix_b: int,
    ) -> bool:
        try:
            addr_a = ipaddress.ip_address(ip_a)
            addr_b = ipaddress.ip_address(ip_b)
            net_a = ipaddress.ip_network(f"{ip_a}/{prefix_a}", strict=False)
            net_b = ipaddress.ip_network(f"{ip_b}/{prefix_b}", strict=False)
        except ValueError:
            return False
        return addr_b in net_a and addr_a in net_b

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

            if packet_type == "media_probe":
                print(f"[Server] UDP probe from {sender} at {addr}")
                continue

            target = str(packet.get("target", "")).strip()
            if not target or not sender_info:
                continue
            if sender_info.call_mode != "relay" or sender_info.in_call_with != target:
                continue

            with self._lock:
                target_cid = self._nickname_map.get(target)
                target_info = self._clients.get(target_cid) if target_cid else None
                if not target_info:
                    continue
                forward_addr = target_info.udp_observed_addr or (target_info.addr[0], target_info.media_port)

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
