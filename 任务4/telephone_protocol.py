"""
任务4：IP电话系统应用层协议

消息统一使用 JSON 文本，以换行符('\n')作为分隔。

消息类型：
- login: 用户登录（输入用户名即可）
- logout: 用户登出
- contact_add: 添加联系人
- contact_delete: 删除联系人
- contact_update: 更新联系人
- contact_list: 获取联系人列表
- contact_search: 搜索联系人
- call_invite: 呼叫对方
- call_accept: 接听来电
- call_reject: 拒绝来电
- call_hangup: 挂断通话
- call_busy: 对方占线
- call_not_found: 用户不存在
- text: 文本消息
- audio_chunk: 音频数据块
"""

import base64
import json
from dataclasses import dataclass
from typing import Literal, Optional


# 消息类型定义
MessageType = Literal[
    "login", "logout",
    "contact_add", "contact_delete", "contact_update", "contact_list", "contact_search",
    "call_invite", "call_accept", "call_reject", "call_hangup", "call_busy", "call_not_found",
    "text", "audio_chunk"
]


# 登录请求
@dataclass
class LoginRequest:
    username: str


# 联系人操作
@dataclass
class ContactAddRequest:
    contact_name: str


@dataclass
class ContactDeleteRequest:
    contact_name: str


@dataclass
class ContactUpdateRequest:
    old_name: str
    new_name: str


@dataclass
class ContactSearchRequest:
    keyword: str


# 呼叫操作
@dataclass
class CallInviteRequest:
    target: str  # 被呼叫的用户名


# ========== 编码函数 ==========

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
    msg = {"type": "contact_update", "username": username, "old_name": old_name, "new_name": new_name}
    return json.dumps(msg, ensure_ascii=False)


def encode_contact_list(username: str) -> str:
    msg = {"type": "contact_list", "username": username}
    return json.dumps(msg, ensure_ascii=False)


def encode_contact_search(username: str, keyword: str) -> str:
    msg = {"type": "contact_search", "username": username, "keyword": keyword}
    return json.dumps(msg, ensure_ascii=False)


def encode_call_invite(caller: str, target: str) -> str:
    msg = {"type": "call_invite", "caller": caller, "target": target}
    return json.dumps(msg, ensure_ascii=False)


def encode_call_accept(caller: str, target: str) -> str:
    msg = {"type": "call_accept", "caller": caller, "target": target}
    return json.dumps(msg, ensure_ascii=False)


def encode_call_reject(caller: str, target: str, reason: str = "") -> str:
    msg = {"type": "call_reject", "caller": caller, "target": target, "reason": reason}
    return json.dumps(msg, ensure_ascii=False)


def encode_call_hangup(username: str, target: str) -> str:
    msg = {"type": "call_hangup", "username": username, "target": target}
    return json.dumps(msg, ensure_ascii=False)


def encode_call_busy(target: str, caller: str) -> str:
    msg = {"type": "call_busy", "target": target, "caller": caller}
    return json.dumps(msg, ensure_ascii=False)


def encode_call_not_found(target: str, caller: str) -> str:
    msg = {"type": "call_not_found", "target": target, "caller": caller}
    return json.dumps(msg, ensure_ascii=False)


def encode_text(content: str) -> str:
    msg = {"type": "text", "content": content}
    return json.dumps(msg)


def encode_audio_chunk(stream_id: str, raw: bytes) -> str:
    if not raw:
        raise ValueError("audio chunk is empty")
    msg = {
        "type": "audio_chunk",
        "stream_id": stream_id,
        "data": base64.b64encode(raw).decode("utf-8"),
    }
    return json.dumps(msg)


# 响应消息编码
def encode_response(success: bool, message: str, data: Optional[dict] = None) -> str:
    msg = {"success": success, "message": message}
    if data:
        msg["data"] = data
    return json.dumps(msg, ensure_ascii=False)


# ========== 解码函数 ==========

def decode_message(raw: str) -> tuple[MessageType, dict]:
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
    
    valid_types = (
        "login", "logout",
        "contact_add", "contact_delete", "contact_update", "contact_list", "contact_search",
        "call_invite", "call_accept", "call_reject", "call_hangup", "call_busy", "call_not_found",
        "text", "audio_chunk", "response"
    )
    if t not in valid_types:
        raise ValueError(f"unknown type: {t}")
    return t, obj


def decode_response(raw: str) -> tuple[bool, str, dict]:
    """解析响应消息"""
    obj = json.loads(raw)
    success = obj.get("success", False)
    message = obj.get("message", "")
    data = obj.get("data", {})
    return success, message, data
