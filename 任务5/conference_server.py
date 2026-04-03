"""
任务5 - 多方语音会议系统服务端

控制面继续使用 TCP + JSON，音频面改为由服务端分配 IP 组播地址，
客户端直接通过 UDP 组播收发语音数据，服务端不再做逐个成员转发。
"""

from __future__ import annotations

import json
import random
import socket
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import sys

from conference_protocol import (
    MESSAGE_DELIMITER,
    MAX_ROOM_SIZE,
    decode_message,
    encode_response,
    encode_room_dismissed_notify,
    encode_room_invite_notify,
    encode_room_member_update,
)
from multicast_audio import allocate_multicast_endpoint


def _ensure_task4_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task4_dir = base / "任务4"
    if str(task4_dir) not in sys.path:
        sys.path.insert(0, str(task4_dir))


_ensure_task4_on_path()
from data_store import get_data_store

DEFAULT_PORT = 8882


@dataclass
class ClientInfo:
    conn: socket.socket
    addr: Tuple[str, int]
    username: str = ""
    room_id: str = ""


@dataclass
class ChatRoom:
    room_id: str
    creator: str
    audio_protocol: str = "udp"
    members: Dict[str, int] = field(default_factory=dict)
    invited: Set[str] = field(default_factory=set)
    multicast_group: str = ""
    multicast_port: int = 0

    def get_available_positions(self) -> List[int]:
        used = set(self.members.values())
        return [index for index in range(MAX_ROOM_SIZE) if index not in used]

    def assign_position(self, username: str) -> int:
        available = self.get_available_positions()
        if not available:
            return -1
        position = random.choice(available)
        self.members[username] = position
        return position

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
        self._clients: Dict[int, ClientInfo] = {}
        self._username_map: Dict[str, int] = {}
        self._rooms: Dict[str, ChatRoom] = {}
        self._lock = threading.RLock()
        self._running = False
        self._data_store = get_data_store()

    def start(self) -> None:
        self._sock.bind((self._host, self._port))
        self._sock.listen(20)
        self._running = True
        print(f"[Server] Listening on {self._host}:{self._port}")
        try:
            while self._running:
                try:
                    conn, addr = self._sock.accept()
                except OSError:
                    break
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True,
                ).start()
        finally:
            self._cleanup()
            print("[Server] Stopped")

    def stop(self) -> None:
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass

    def _cleanup(self) -> None:
        with self._lock:
            for info in self._clients.values():
                try:
                    info.conn.close()
                except OSError:
                    pass
            self._clients.clear()
            self._username_map.clear()
            self._rooms.clear()
        try:
            self._sock.close()
        except OSError:
            pass

    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        cid = id(conn)
        with self._lock:
            self._clients[cid] = ClientInfo(conn=conn, addr=addr)
        print(f"[Server] New connection: {addr}")

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
                        message_type, payload = decode_message(line)
                    except ValueError:
                        continue
                    self._dispatch(cid, message_type, payload, conn, line)
        except ConnectionResetError:
            pass
        finally:
            self._handle_disconnect(cid)

    def _dispatch(
        self,
        cid: int,
        message_type: str,
        payload: dict,
        conn: socket.socket,
        raw: str,
    ) -> None:
        handlers = {
            "login": lambda: self._handle_login(cid, payload, conn),
            "logout": lambda: self._handle_logout(cid),
            "contact_add": lambda: self._handle_contact_op(cid, payload, "add"),
            "contact_delete": lambda: self._handle_contact_op(cid, payload, "delete"),
            "contact_update": lambda: self._handle_contact_op(cid, payload, "update"),
            "contact_list": lambda: self._handle_contact_op(cid, payload, "list"),
            "contact_search": lambda: self._handle_contact_op(cid, payload, "search"),
            "online_query": lambda: self._handle_online_query(cid),
            "room_create": lambda: self._handle_room_create(cid, payload),
            "room_invite": lambda: self._handle_room_invite(cid, payload),
            "room_join": lambda: self._handle_room_join(cid, payload),
            "room_leave": lambda: self._handle_room_leave(cid, payload),
            "room_dismiss": lambda: self._handle_room_dismiss(cid, payload),
            "room_audio_chunk": lambda: self._forward_room_audio(cid, raw),
        }
        handler = handlers.get(message_type)
        if handler:
            handler()

    def _handle_login(self, cid: int, payload: dict, conn: socket.socket) -> None:
        username = payload.get("username", "")
        if not username:
            self._send(conn, encode_response(False, "用户名不能为空"))
            return

        self._data_store.ensure_user(username)
        with self._lock:
            if username in self._username_map:
                self._send(conn, encode_response(False, "用户已在线"))
                return
            info = self._clients.get(cid)
            if info:
                info.username = username
            self._username_map[username] = cid

        self._send(conn, encode_response(True, "登录成功"))
        print(f"[Server] {username} logged in")

    def _handle_logout(self, cid: int) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            if info.room_id:
                self._remove_from_room(info.username, info.room_id)
            self._username_map.pop(info.username, None)
            print(f"[Server] {info.username} logged out")
            info.username = ""

    def _handle_disconnect(self, cid: int) -> None:
        with self._lock:
            info = self._clients.pop(cid, None)
            if info and info.username:
                if info.room_id:
                    self._remove_from_room(info.username, info.room_id)
                self._username_map.pop(info.username, None)

    def _handle_contact_op(self, cid: int, payload: dict, op: str) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            username = info.username

        if op == "add":
            contact_name = payload.get("contact_name", "")
            if not contact_name:
                self._send_by_cid(cid, encode_response(False, "联系人名称不能为空"))
                return
            ok, message = self._data_store.add_contact(username, contact_name)
            self._send_by_cid(cid, encode_response(ok, message))
        elif op == "delete":
            contact_name = payload.get("contact_name", "")
            ok, message = self._data_store.delete_contact(username, contact_name)
            self._send_by_cid(cid, encode_response(ok, message))
        elif op == "update":
            old_name = payload.get("old_name", "")
            new_name = payload.get("new_name", "")
            if not old_name or not new_name:
                self._send_by_cid(cid, encode_response(False, "联系人名称不能为空"))
                return
            ok, message = self._data_store.update_contact(username, old_name, new_name)
            self._send_by_cid(cid, encode_response(ok, message))
        elif op == "list":
            contacts = self._data_store.get_contacts(username)
            self._send_by_cid(
                cid,
                encode_response(True, "获取成功", {"contacts": contacts}),
            )
        elif op == "search":
            keyword = payload.get("keyword", "")
            contacts = self._data_store.search_contacts(username, keyword)
            self._send_by_cid(
                cid,
                encode_response(True, "搜索成功", {"contacts": contacts}),
            )

    def _handle_online_query(self, cid: int) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            online_users = list(self._username_map.keys())
        self._send_by_cid(
            cid,
            encode_response(True, "查询成功", {"online_users": online_users}),
        )

    def _handle_room_create(self, cid: int, payload: Optional[dict] = None) -> None:
        payload = payload or {}
        audio_protocol = payload.get("audio_protocol", "udp")
        if audio_protocol != "udp":
            audio_protocol = "udp"

        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            if info.room_id:
                self._send_by_cid(cid, encode_response(False, "您已在聊天室中"))
                return

            room_id = uuid.uuid4().hex[:8]
            creator = info.username
            room = ChatRoom(room_id=room_id, creator=creator, audio_protocol=audio_protocol)
            position = room.assign_position(creator)
            used_endpoints = {
                (item.multicast_group, item.multicast_port)
                for item in self._rooms.values()
                if item.multicast_group and item.multicast_port
            }
            room.multicast_group, room.multicast_port = allocate_multicast_endpoint(
                used_endpoints
            )
            self._rooms[room_id] = room
            info.room_id = room_id

        response_data = {
            "room_id": room_id,
            "position": position,
            "audio_protocol": audio_protocol,
            "multicast_group": room.multicast_group,
            "multicast_port": room.multicast_port,
        }
        self._send_by_cid(
            cid,
            encode_response(True, "聊天室创建成功", response_data),
        )
        print(
            f"[Server] Room {room_id} created by {creator} "
            f"(group={room.multicast_group}:{room.multicast_port})"
        )
        self._broadcast_member_update(room_id)

    def _handle_room_invite(self, cid: int, payload: dict) -> None:
        room_id = payload.get("room_id", "")
        target = payload.get("target", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            inviter = info.username
            room = self._rooms.get(room_id)
            if not room:
                self._send_by_cid(cid, encode_response(False, "聊天室不存在"))
                return
            if len(room.members) >= MAX_ROOM_SIZE:
                self._send_by_cid(cid, encode_response(False, "聊天室已满"))
                return
            if target in room.members:
                self._send_by_cid(cid, encode_response(False, f"{target} 已在聊天室中"))
                return
            if target not in self._username_map:
                self._send_by_cid(cid, encode_response(False, f"{target} 不在线"))
                return
            target_cid = self._username_map[target]
            target_info = self._clients.get(target_cid)
            if target_info and target_info.room_id:
                self._send_by_cid(cid, encode_response(False, f"{target} 已在其他聊天室中"))
                return
            room.invited.add(target)

        self._send_by_cid(target_cid, encode_room_invite_notify(room_id, inviter, target))
        self._send_by_cid(cid, encode_response(True, f"已邀请 {target}"))
        print(f"[Server] {inviter} invited {target} to room {room_id}")

    def _handle_room_join(self, cid: int, payload: dict) -> None:
        room_id = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            username = info.username
            if info.room_id:
                self._send_by_cid(cid, encode_response(False, "您已在聊天室中"))
                return
            room = self._rooms.get(room_id)
            if not room:
                self._send_by_cid(cid, encode_response(False, "聊天室不存在"))
                return
            if username not in room.invited and username != room.creator:
                self._send_by_cid(cid, encode_response(False, "您未被邀请"))
                return
            if len(room.members) >= MAX_ROOM_SIZE:
                self._send_by_cid(cid, encode_response(False, "聊天室已满"))
                return

            position = room.assign_position(username)
            room.invited.discard(username)
            info.room_id = room_id
            response_data = {
                "room_id": room_id,
                "position": position,
                "creator": room.creator,
                "audio_protocol": room.audio_protocol,
                "multicast_group": room.multicast_group,
                "multicast_port": room.multicast_port,
            }

        self._send_by_cid(
            cid,
            encode_response(True, "加入聊天室成功", response_data),
        )
        print(f"[Server] {username} joined room {room_id} at pos {position}")
        self._broadcast_member_update(room_id)

    def _handle_room_leave(self, cid: int, payload: dict) -> None:
        room_id = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            username = info.username
        self._remove_from_room(username, room_id)
        self._send_by_cid(cid, encode_response(True, "已退出聊天室"))

    def _handle_room_dismiss(self, cid: int, payload: dict) -> None:
        room_id = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            username = info.username
            room = self._rooms.get(room_id)
            if not room:
                self._send_by_cid(cid, encode_response(False, "聊天室不存在"))
                return
            if room.creator != username:
                self._send_by_cid(cid, encode_response(False, "只有创建者可以解散聊天室"))
                return

            dismissed_message = encode_room_dismissed_notify(room_id)
            for member in list(room.members.keys()):
                member_cid = self._username_map.get(member)
                if member_cid is None:
                    continue
                member_info = self._clients.get(member_cid)
                if member_info:
                    member_info.room_id = ""
                self._send_by_cid(member_cid, dismissed_message)
            del self._rooms[room_id]

        print(f"[Server] Room {room_id} dismissed by {username}")

    def _remove_from_room(self, username: str, room_id: str) -> None:
        with self._lock:
            room = self._rooms.get(room_id)
            if not room or username not in room.members:
                return

            if room.creator == username:
                dismissed_message = encode_room_dismissed_notify(room_id)
                for member in list(room.members.keys()):
                    member_cid = self._username_map.get(member)
                    if member_cid is None:
                        continue
                    member_info = self._clients.get(member_cid)
                    if member_info:
                        member_info.room_id = ""
                    self._send_by_cid(member_cid, dismissed_message)
                del self._rooms[room_id]
                print(f"[Server] Room {room_id} dismissed (creator left)")
                return

            del room.members[username]
            user_cid = self._username_map.get(username)
            if user_cid is not None:
                user_info = self._clients.get(user_cid)
                if user_info:
                    user_info.room_id = ""

        print(f"[Server] {username} left room {room_id}")
        self._broadcast_member_update(room_id)

    def _broadcast_member_update(self, room_id: str) -> None:
        with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return
            members = room.get_member_list()
            positions = dict(room.members)
            message = encode_room_member_update(room_id, members, positions)
            member_cids = [
                self._username_map[member]
                for member in room.members
                if member in self._username_map
            ]
        for member_cid in member_cids:
            self._send_by_cid(member_cid, message)

    def _forward_room_audio(self, cid: int, raw: str) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.room_id:
                return
            room = self._rooms.get(info.room_id)
            if not room or room.audio_protocol != "tcp":
                return
            sender = info.username
            member_cids = [
                self._username_map[member]
                for member in room.members
                if member != sender and member in self._username_map
            ]
        for member_cid in member_cids:
            self._send_by_cid(member_cid, raw)

    def _send(self, conn: socket.socket, message: str) -> None:
        try:
            conn.sendall((message + MESSAGE_DELIMITER).encode("utf-8"))
        except OSError:
            pass

    def _send_by_cid(self, cid: int, message: str) -> None:
        with self._lock:
            info = self._clients.get(cid)
            if info is None:
                return
            conn = info.conn
        self._send(conn, message)


def main() -> None:
    server = ConferenceServer()
    try:
        print("=" * 50)
        print("任务5 - 多方语音会议系统服务器")
        print("=" * 50)
        print(f"端口: {DEFAULT_PORT}")
        print("Ctrl+C 停止")
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
