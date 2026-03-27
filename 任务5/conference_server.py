"""
任务5 - 多方语音会议系统服务器

在任务4服务器基础上扩展，增加聊天室管理和组播音频转发功能。
"""

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
    decode_message,
    encode_response,
    encode_room_invite_notify,
    encode_room_dismissed_notify,
    encode_room_member_update,
    MAX_ROOM_SIZE,
    MESSAGE_DELIMITER,
)


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
    members: Dict[str, int] = field(default_factory=dict)
    invited: Set[str] = field(default_factory=set)

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
            {"username": u, "position": p}
            for u, p in sorted(self.members.items(), key=lambda x: x[1])
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

    def _cleanup(self) -> None:
        with self._lock:
            for cid, info in list(self._clients.items()):
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
                        mtype, payload = decode_message(line)
                    except ValueError:
                        continue
                    self._dispatch(cid, mtype, payload, conn, line)
        except ConnectionResetError:
            pass
        finally:
            self._handle_disconnect(cid)

    def _dispatch(self, cid, mtype, payload, conn, raw):
        handlers = {
            "login": lambda: self._handle_login(cid, payload, conn),
            "logout": lambda: self._handle_logout(cid),
            "contact_add": lambda: self._handle_contact_op(cid, payload, "add"),
            "contact_delete": lambda: self._handle_contact_op(cid, payload, "delete"),
            "contact_update": lambda: self._handle_contact_op(cid, payload, "update"),
            "contact_list": lambda: self._handle_contact_op(cid, payload, "list"),
            "contact_search": lambda: self._handle_contact_op(cid, payload, "search"),
            "online_query": lambda: self._handle_online_query(cid),
            "room_create": lambda: self._handle_room_create(cid),
            "room_invite": lambda: self._handle_room_invite(cid, payload),
            "room_join": lambda: self._handle_room_join(cid, payload),
            "room_leave": lambda: self._handle_room_leave(cid, payload),
            "room_dismiss": lambda: self._handle_room_dismiss(cid, payload),
            "room_audio_chunk": lambda: self._forward_room_audio(cid, raw),
        }
        h = handlers.get(mtype)
        if h:
            h()

    # ---- auth ----

    def _handle_login(self, cid, payload, conn):
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

    def _handle_logout(self, cid):
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            if info.room_id:
                self._remove_from_room(info.username, info.room_id)
            self._username_map.pop(info.username, None)
            print(f"[Server] {info.username} logged out")
            info.username = ""

    def _handle_disconnect(self, cid):
        with self._lock:
            info = self._clients.pop(cid, None)
            if info and info.username:
                if info.room_id:
                    self._remove_from_room(info.username, info.room_id)
                self._username_map.pop(info.username, None)

    # ---- contacts ----

    def _handle_contact_op(self, cid, payload, op):
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            uname = info.username

        if op == "add":
            cn = payload.get("contact_name", "")
            if not cn:
                self._send_by_cid(cid, encode_response(False, "联系人名称不能为空"))
                return
            ok, msg = self._data_store.add_contact(uname, cn)
            self._send_by_cid(cid, encode_response(ok, msg))
        elif op == "delete":
            cn = payload.get("contact_name", "")
            ok, msg = self._data_store.delete_contact(uname, cn)
            self._send_by_cid(cid, encode_response(ok, msg))
        elif op == "update":
            old = payload.get("old_name", "")
            new = payload.get("new_name", "")
            if not old or not new:
                self._send_by_cid(cid, encode_response(False, "联系人名称不能为空"))
                return
            ok, msg = self._data_store.update_contact(uname, old, new)
            self._send_by_cid(cid, encode_response(ok, msg))
        elif op == "list":
            contacts = self._data_store.get_contacts(uname)
            self._send_by_cid(
                cid, encode_response(True, "获取成功", {"contacts": contacts})
            )
        elif op == "search":
            kw = payload.get("keyword", "")
            contacts = self._data_store.search_contacts(uname, kw)
            self._send_by_cid(
                cid, encode_response(True, "搜索成功", {"contacts": contacts})
            )

    # ---- online query ----

    def _handle_online_query(self, cid):
        """返回当前所有在线用户列表"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            online_users = list(self._username_map.keys())
        self._send_by_cid(
            cid, encode_response(True, "查询成功", {"online_users": online_users})
        )

    # ---- room ----

    def _handle_room_create(self, cid):
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            if info.room_id:
                self._send_by_cid(cid, encode_response(False, "您已在聊天室中"))
                return
            creator = info.username
            rid = uuid.uuid4().hex[:8]
            room = ChatRoom(room_id=rid, creator=creator)
            pos = room.assign_position(creator)
            self._rooms[rid] = room
            info.room_id = rid
        print(f"[Server] Room {rid} created by {creator}")
        self._send_by_cid(
            cid,
            encode_response(True, "聊天室创建成功", {"room_id": rid, "position": pos}),
        )
        self._broadcast_member_update(rid)

    def _handle_room_invite(self, cid, payload):
        rid = payload.get("room_id", "")
        target = payload.get("target", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            inviter = info.username
            room = self._rooms.get(rid)
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
            tcid = self._username_map[target]
            tinfo = self._clients.get(tcid)
            if tinfo and tinfo.room_id:
                self._send_by_cid(
                    cid, encode_response(False, f"{target} 已在其他聊天室中")
                )
                return
            room.invited.add(target)
        notify = encode_room_invite_notify(rid, inviter, target)
        self._send_by_cid(tcid, notify)
        self._send_by_cid(cid, encode_response(True, f"已邀请 {target}"))
        print(f"[Server] {inviter} invited {target} to room {rid}")

    def _handle_room_join(self, cid, payload):
        rid = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                self._send_by_cid(cid, encode_response(False, "未登录"))
                return
            uname = info.username
            if info.room_id:
                self._send_by_cid(cid, encode_response(False, "您已在聊天室中"))
                return
            room = self._rooms.get(rid)
            if not room:
                self._send_by_cid(cid, encode_response(False, "聊天室不存在"))
                return
            if uname not in room.invited and uname != room.creator:
                self._send_by_cid(cid, encode_response(False, "您未被邀请"))
                return
            if len(room.members) >= MAX_ROOM_SIZE:
                self._send_by_cid(cid, encode_response(False, "聊天室已满"))
                return
            pos = room.assign_position(uname)
            room.invited.discard(uname)
            info.room_id = rid
        print(f"[Server] {uname} joined room {rid} at pos {pos}")
        self._send_by_cid(
            cid,
            encode_response(
                True,
                "加入聊天室成功",
                {
                    "room_id": rid,
                    "position": pos,
                    "creator": room.creator,
                },
            ),
        )
        self._broadcast_member_update(rid)

    def _handle_room_leave(self, cid, payload):
        rid = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            uname = info.username
        self._remove_from_room(uname, rid)
        self._send_by_cid(cid, encode_response(True, "已退出聊天室"))

    def _handle_room_dismiss(self, cid, payload):
        rid = payload.get("room_id", "")
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            uname = info.username
            room = self._rooms.get(rid)
            if not room:
                self._send_by_cid(cid, encode_response(False, "聊天室不存在"))
                return
            if room.creator != uname:
                self._send_by_cid(
                    cid, encode_response(False, "只有创建者可以解散聊天室")
                )
                return
            dismiss_msg = encode_room_dismissed_notify(rid)
            for m in list(room.members.keys()):
                if m in self._username_map:
                    mc = self._username_map[m]
                    mi = self._clients.get(mc)
                    if mi:
                        mi.room_id = ""
                    self._send_by_cid(mc, dismiss_msg)
            del self._rooms[rid]
        print(f"[Server] Room {rid} dismissed by {uname}")

    def _remove_from_room(self, username, room_id):
        with self._lock:
            room = self._rooms.get(room_id)
            if not room or username not in room.members:
                return
            if room.creator == username:
                dm = encode_room_dismissed_notify(room_id)
                for m in list(room.members.keys()):
                    if m in self._username_map:
                        mc = self._username_map[m]
                        mi = self._clients.get(mc)
                        if mi:
                            mi.room_id = ""
                        self._send_by_cid(mc, dm)
                del self._rooms[room_id]
                print(f"[Server] Room {room_id} dismissed (creator left)")
                return
            del room.members[username]
            if username in self._username_map:
                uc = self._username_map[username]
                ui = self._clients.get(uc)
                if ui:
                    ui.room_id = ""
        print(f"[Server] {username} left room {room_id}")
        self._broadcast_member_update(room_id)

    def _broadcast_member_update(self, rid):
        with self._lock:
            room = self._rooms.get(rid)
            if not room:
                return
            members = room.get_member_list()
            positions = dict(room.members)
            msg = encode_room_member_update(rid, members, positions)
            for m in list(room.members.keys()):
                if m in self._username_map:
                    self._send_by_cid(self._username_map[m], msg)

    def _forward_room_audio(self, cid, raw):
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.room_id:
                return
            room = self._rooms.get(info.room_id)
            if not room:
                return
            sender = info.username
            for m in list(room.members.keys()):
                if m == sender:
                    continue
                if m in self._username_map:
                    self._send_by_cid(self._username_map[m], raw)

    # ---- network ----

    def _send(self, conn, msg):
        try:
            conn.sendall((msg + MESSAGE_DELIMITER).encode("utf-8"))
        except Exception:
            pass

    def _send_by_cid(self, cid, msg):
        with self._lock:
            info = self._clients.get(cid)
            if info:
                self._send(info.conn, msg)


def main():
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
