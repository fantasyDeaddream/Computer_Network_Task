"""
任务5：多方语音会议系统应用层协议

在任务4协议基础上扩展，增加聊天室相关消息类型。
消息统一使用 JSON 文本，以换行符('\n')作为分隔。

新增消息类型：
- room_create: 创建聊天室
- room_invite: 邀请用户加入聊天室
- room_invite_notify: 通知被邀请用户
- room_join: 用户加入聊天室
- room_leave: 用户退出聊天室
- room_dismiss: 解散聊天室
- room_dismissed_notify: 通知聊天室已被解散
- room_member_update: 聊天室成员变更通知
- room_audio_chunk: 聊天室音频数据块
- online_query: 查询在线用户列表
"""

import base64
import json
import struct
import time
from typing import Literal, Optional, List

# 消息类型定义（包含任务4的所有类型 + 新增聊天室类型）
MessageType = Literal[
    # 任务4原有类型
    "login",
    "logout",
    "contact_add",
    "contact_delete",
    "contact_update",
    "contact_list",
    "contact_search",
    "call_invite",
    "call_accept",
    "call_reject",
    "call_hangup",
    "call_busy",
    "call_not_found",
    "text",
    "audio_chunk",
    # 任务5新增类型
    "room_create",
    "room_invite",
    "room_invite_notify",
    "room_join",
    "room_leave",
    "room_dismiss",
    "room_dismissed_notify",
    "room_member_update",
    "room_audio_chunk",
    "online_query",
]

MESSAGE_DELIMITER = "\n"
MAX_ROOM_SIZE = 20  # 每个聊天室最多容纳20个用户
UDP_AUDIO_MAGIC = b"CN5A"
UDP_AUDIO_USERNAME_BYTES = 32
_UDP_AUDIO_HEADER = struct.Struct("!4s32sIQ")
UDP_AUDIO_HEADER_SIZE = _UDP_AUDIO_HEADER.size


# ========== 聊天室消息编码函数 ==========


def encode_room_create(creator: str, audio_protocol: str = "tcp") -> str:
    """编码创建聊天室请求

    Args:
        creator: 创建者用户名
        audio_protocol: 音频传输协议，"tcp" 或 "udp"
    """
    msg = {"type": "room_create", "creator": creator, "audio_protocol": audio_protocol}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_invite(room_id: str, inviter: str, target: str) -> str:
    """编码邀请用户加入聊天室"""
    msg = {
        "type": "room_invite",
        "room_id": room_id,
        "inviter": inviter,
        "target": target,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_room_invite_notify(room_id: str, inviter: str, target: str) -> str:
    """编码邀请通知（发送给被邀请者）"""
    msg = {
        "type": "room_invite_notify",
        "room_id": room_id,
        "inviter": inviter,
        "target": target,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_room_join(room_id: str, username: str) -> str:
    """编码加入聊天室"""
    msg = {"type": "room_join", "room_id": room_id, "username": username}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_leave(room_id: str, username: str) -> str:
    """编码退出聊天室"""
    msg = {"type": "room_leave", "room_id": room_id, "username": username}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_dismiss(room_id: str, creator: str) -> str:
    """编码解散聊天室"""
    msg = {"type": "room_dismiss", "room_id": room_id, "creator": creator}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_dismissed_notify(room_id: str) -> str:
    """编码聊天室已被解散通知"""
    msg = {"type": "room_dismissed_notify", "room_id": room_id}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_member_update(
    room_id: str, members: List[dict], positions: dict
) -> str:
    """
    编码聊天室成员变更通知
    members: [{"username": str, "position": int}, ...]
    positions: {username: position_index, ...}
    """
    msg = {
        "type": "room_member_update",
        "room_id": room_id,
        "members": members,
        "positions": positions,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_room_audio_chunk(
    room_id: str,
    sender: str,
    raw: bytes,
    seq: Optional[int] = None,
    timestamp_ms: Optional[int] = None,
) -> str:
    """编码聊天室音频数据块"""
    if not raw:
        raise ValueError("audio chunk is empty")
    msg = {
        "type": "room_audio_chunk",
        "room_id": room_id,
        "sender": sender,
        "data": base64.b64encode(raw).decode("utf-8"),
    }
    if seq is not None:
        msg["seq"] = int(seq)
    if timestamp_ms is not None:
        msg["timestamp_ms"] = int(timestamp_ms)
    return json.dumps(msg)


def encode_udp_audio_packet(
    sender: str,
    raw: bytes,
    seq: int = 0,
    timestamp_ms: Optional[int] = None,
) -> bytes:
    """编码UDP音频包，包含用户名、序号和发送时间。"""
    if not raw:
        raise ValueError("audio chunk is empty")
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    username_bytes = sender.encode("utf-8")[:UDP_AUDIO_USERNAME_BYTES].ljust(
        UDP_AUDIO_USERNAME_BYTES, b"\x00"
    )
    header = _UDP_AUDIO_HEADER.pack(
        UDP_AUDIO_MAGIC, username_bytes, int(seq) & 0xFFFFFFFF, int(timestamp_ms)
    )
    return header + raw


def decode_udp_audio_packet(packet: bytes) -> tuple:
    """解析UDP音频包，兼容旧的32字节用户名头格式。"""
    if len(packet) >= UDP_AUDIO_HEADER_SIZE and packet[:4] == UDP_AUDIO_MAGIC:
        _, username_bytes, seq, timestamp_ms = _UDP_AUDIO_HEADER.unpack(
            packet[:UDP_AUDIO_HEADER_SIZE]
        )
        sender = username_bytes.rstrip(b"\x00").decode("utf-8", errors="ignore")
        return sender, seq, timestamp_ms, packet[UDP_AUDIO_HEADER_SIZE:]

    if len(packet) >= UDP_AUDIO_USERNAME_BYTES:
        sender = (
            packet[:UDP_AUDIO_USERNAME_BYTES]
            .rstrip(b"\x00")
            .decode("utf-8", errors="ignore")
        )
        return sender, None, None, packet[UDP_AUDIO_USERNAME_BYTES:]

    raise ValueError("invalid udp audio packet")


# ========== 在线状态查询 ==========


def encode_online_query(username: str) -> str:
    """编码查询在线用户列表请求"""
    msg = {"type": "online_query", "username": username}
    return json.dumps(msg, ensure_ascii=False)


# ========== 复用任务4的编码函数 ==========


def encode_login(username: str) -> str:
    msg = {"type": "login", "username": username}
    return json.dumps(msg, ensure_ascii=False)


def encode_logout(username: str) -> str:
    msg = {"type": "logout", "username": username}
    return json.dumps(msg)


def encode_contact_add(username: str, contact_name: str) -> str:
    msg = {"type": "contact_add", "username": username, "contact_name": contact_name}
    return json.dumps(msg, ensure_ascii=False)


def encode_contact_delete(username: str, contact_name: str) -> str:
    msg = {"type": "contact_delete", "username": username, "contact_name": contact_name}
    return json.dumps(msg, ensure_ascii=False)


def encode_contact_update(username: str, old_name: str, new_name: str) -> str:
    msg = {
        "type": "contact_update",
        "username": username,
        "old_name": old_name,
        "new_name": new_name,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_contact_list(username: str) -> str:
    msg = {"type": "contact_list", "username": username}
    return json.dumps(msg, ensure_ascii=False)


def encode_contact_search(username: str, keyword: str) -> str:
    msg = {"type": "contact_search", "username": username, "keyword": keyword}
    return json.dumps(msg, ensure_ascii=False)


def encode_response(success: bool, message: str, data: Optional[dict] = None) -> str:
    msg = {"success": success, "message": message}
    if data:
        msg["data"] = data
    return json.dumps(msg, ensure_ascii=False)


# ========== 解码函数 ==========

VALID_TYPES = (
    "login",
    "logout",
    "contact_add",
    "contact_delete",
    "contact_update",
    "contact_list",
    "contact_search",
    "call_invite",
    "call_accept",
    "call_reject",
    "call_hangup",
    "call_busy",
    "call_not_found",
    "text",
    "audio_chunk",
    "response",
    "room_create",
    "room_invite",
    "room_invite_notify",
    "room_join",
    "room_leave",
    "room_dismiss",
    "room_dismissed_notify",
    "room_member_update",
    "room_audio_chunk",
    "online_query",
)


def decode_message(raw: str) -> tuple:
    """
    解析消息，返回(type, payload)。
    对不合法的消息抛出 ValueError。
    """
    if not raw:
        raise ValueError("empty message")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid json: {e}") from e

    if not isinstance(obj, dict):
        raise ValueError("message is not a dict")

    t = obj.get("type")
    if t is None:
        raise ValueError("message has no type field")

    if t not in VALID_TYPES:
        raise ValueError(f"unknown type: {t}")
    return t, obj


def decode_response(raw: str) -> tuple:
    """解析响应消息"""
    obj = json.loads(raw)
    success = obj.get("success", False)
    message = obj.get("message", "")
    data = obj.get("data", {})
    return success, message, data
