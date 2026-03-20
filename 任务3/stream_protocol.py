"""
Task 3 signaling and UDP media protocol helpers.

Control messages are newline-delimited JSON sent over TCP.
Media packets are JSON datagrams sent over UDP.
"""

from __future__ import annotations

import base64
import json
from typing import Literal


ControlMessageType = Literal[
    "login",
    "response",
    "text",
    "call_invite",
    "call_accept",
    "call_reject",
    "call_hangup",
    "call_busy",
    "call_not_found",
    "call_ready",
]

MediaPacketType = Literal["audio_frame", "media_probe"]


def encode_login(
    nickname: str,
    media_port: int,
    local_ip: str,
    subnet_prefix: int,
) -> str:
    msg = {
        "type": "login",
        "nickname": nickname,
        "media_port": media_port,
        "local_ip": local_ip,
        "subnet_prefix": subnet_prefix,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_response(success: bool, message: str, data: dict | None = None) -> str:
    msg = {
        "type": "response",
        "success": success,
        "message": message,
    }
    if data:
        msg["data"] = data
    return json.dumps(msg, ensure_ascii=False)


def encode_text(content: str, target: str = "") -> str:
    msg = {"type": "text", "content": content, "target": target}
    return json.dumps(msg, ensure_ascii=False)


def encode_call_invite(target: str) -> str:
    return json.dumps({"type": "call_invite", "target": target}, ensure_ascii=False)


def encode_call_accept(caller: str) -> str:
    return json.dumps({"type": "call_accept", "caller": caller}, ensure_ascii=False)


def encode_call_reject(caller: str, reason: str = "") -> str:
    msg = {"type": "call_reject", "caller": caller, "reason": reason}
    return json.dumps(msg, ensure_ascii=False)


def encode_call_hangup(target: str) -> str:
    return json.dumps({"type": "call_hangup", "target": target}, ensure_ascii=False)


def encode_call_busy(target: str) -> str:
    return json.dumps({"type": "call_busy", "target": target}, ensure_ascii=False)


def encode_call_not_found(target: str) -> str:
    return json.dumps({"type": "call_not_found", "target": target}, ensure_ascii=False)


def encode_call_ready(
    peer: str,
    mode: str,
    peer_ip: str = "",
    peer_port: int = 0,
    relay_port: int = 0,
    detail: str = "",
) -> str:
    msg = {
        "type": "call_ready",
        "peer": peer,
        "mode": mode,
        "peer_ip": peer_ip,
        "peer_port": peer_port,
        "relay_port": relay_port,
        "detail": detail,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_audio_frame(
    stream_id: str,
    sequence: int,
    timestamp_ms: int,
    sender: str,
    target: str,
    mode: str,
    raw: bytes,
) -> str:
    if not raw:
        raise ValueError("audio frame is empty")
    msg = {
        "kind": "audio_frame",
        "stream_id": stream_id,
        "sequence": sequence,
        "timestamp_ms": timestamp_ms,
        "sender": sender,
        "target": target,
        "mode": mode,
        "data": base64.b64encode(raw).decode("ascii"),
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_media_probe(sender: str, target: str, mode: str) -> str:
    msg = {
        "kind": "media_probe",
        "sender": sender,
        "target": target,
        "mode": mode,
    }
    return json.dumps(msg, ensure_ascii=False)


def decode_message(raw: str) -> tuple[str, dict]:
    if not raw:
        raise ValueError("empty control message")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid control json: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("control message is not an object")
    msg_type = obj.get("type")
    valid_types = {
        "login",
        "response",
        "text",
        "call_invite",
        "call_accept",
        "call_reject",
        "call_hangup",
        "call_busy",
        "call_not_found",
        "call_ready",
    }
    if msg_type not in valid_types:
        raise ValueError(f"unknown control type: {msg_type}")
    return msg_type, obj


def decode_media_packet(raw: bytes) -> tuple[str, dict]:
    if not raw:
        raise ValueError("empty media packet")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid media json: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("media packet is not an object")
    packet_type = obj.get("kind")
    if packet_type not in {"audio_frame", "media_probe"}:
        raise ValueError(f"unknown media kind: {packet_type}")
    return packet_type, obj
