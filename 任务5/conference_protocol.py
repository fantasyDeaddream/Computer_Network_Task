"""
Application-layer protocol helpers for task 5.

Task 5 extends the task 4 JSON control protocol with multi-party room control,
audio transport metadata, and client quality feedback.
"""

from __future__ import annotations

import base64
import json
import struct
import time
from typing import List, Literal, Optional


MessageType = Literal[
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
    "call_ready",
    "transport_update",
    "direct_path_seen",
    "media_stop",
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
    "quality_report",
]

MediaPacketType = Literal["audio_frame", "media_probe"]


MESSAGE_DELIMITER = "\n"
MAX_ROOM_SIZE = 20

UDP_AUDIO_MAGIC = b"CN5A"
UDP_AUDIO_MAGIC_V2 = b"CN5B"
UDP_AUDIO_USERNAME_BYTES = 32
_UDP_AUDIO_HEADER = struct.Struct("!4s32sIQ")
_UDP_AUDIO_HEADER_V2 = struct.Struct("!4s32sIQIBBH")
UDP_AUDIO_HEADER_SIZE = _UDP_AUDIO_HEADER.size
UDP_AUDIO_HEADER_V2_SIZE = _UDP_AUDIO_HEADER_V2.size


def encode_room_create(creator: str, audio_protocol: str = "tcp") -> str:
    msg = {"type": "room_create", "creator": creator, "audio_protocol": audio_protocol}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_invite(room_id: str, inviter: str, target: str) -> str:
    msg = {
        "type": "room_invite",
        "room_id": room_id,
        "inviter": inviter,
        "target": target,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_room_invite_notify(room_id: str, inviter: str, target: str) -> str:
    msg = {
        "type": "room_invite_notify",
        "room_id": room_id,
        "inviter": inviter,
        "target": target,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_room_join(room_id: str, username: str) -> str:
    msg = {"type": "room_join", "room_id": room_id, "username": username}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_leave(room_id: str, username: str) -> str:
    msg = {"type": "room_leave", "room_id": room_id, "username": username}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_dismiss(room_id: str, creator: str) -> str:
    msg = {"type": "room_dismiss", "room_id": room_id, "creator": creator}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_dismissed_notify(room_id: str) -> str:
    msg = {"type": "room_dismissed_notify", "room_id": room_id}
    return json.dumps(msg, ensure_ascii=False)


def encode_room_member_update(
    room_id: str, members: List[dict], positions: dict
) -> str:
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
    audio_format: Optional[dict] = None,
    profile: Optional[str] = None,
) -> str:
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
    if audio_format:
        msg["audio_format"] = dict(audio_format)
    if profile:
        msg["profile"] = profile
    return json.dumps(msg, ensure_ascii=False)


def encode_udp_audio_packet(
    sender: str,
    raw: bytes,
    seq: int = 0,
    timestamp_ms: Optional[int] = None,
    audio_format: Optional[dict] = None,
) -> bytes:
    if not raw:
        raise ValueError("audio chunk is empty")
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)

    username_bytes = sender.encode("utf-8")[:UDP_AUDIO_USERNAME_BYTES].ljust(
        UDP_AUDIO_USERNAME_BYTES, b"\x00"
    )
    if audio_format:
        sample_rate = int(audio_format.get("sample_rate", 16000))
        channels = int(audio_format.get("channels", 1))
        sample_width = int(audio_format.get("sample_width", 2))
        chunk_size = int(audio_format.get("chunk_size", 1024))
        header = _UDP_AUDIO_HEADER_V2.pack(
            UDP_AUDIO_MAGIC_V2,
            username_bytes,
            int(seq) & 0xFFFFFFFF,
            int(timestamp_ms),
            sample_rate,
            channels,
            sample_width,
            chunk_size,
        )
        return header + raw

    header = _UDP_AUDIO_HEADER.pack(
        UDP_AUDIO_MAGIC, username_bytes, int(seq) & 0xFFFFFFFF, int(timestamp_ms)
    )
    return header + raw


def decode_udp_audio_packet(packet: bytes) -> tuple:
    if len(packet) >= UDP_AUDIO_HEADER_V2_SIZE and packet[:4] == UDP_AUDIO_MAGIC_V2:
        (
            _,
            username_bytes,
            seq,
            timestamp_ms,
            sample_rate,
            channels,
            sample_width,
            chunk_size,
        ) = _UDP_AUDIO_HEADER_V2.unpack(packet[:UDP_AUDIO_HEADER_V2_SIZE])
        sender = username_bytes.rstrip(b"\x00").decode("utf-8", errors="ignore")
        audio_format = {
            "sample_rate": sample_rate,
            "channels": channels,
            "sample_width": sample_width,
            "chunk_size": chunk_size,
        }
        return sender, seq, timestamp_ms, packet[UDP_AUDIO_HEADER_V2_SIZE:], audio_format

    if len(packet) >= UDP_AUDIO_HEADER_SIZE and packet[:4] == UDP_AUDIO_MAGIC:
        _, username_bytes, seq, timestamp_ms = _UDP_AUDIO_HEADER.unpack(
            packet[:UDP_AUDIO_HEADER_SIZE]
        )
        sender = username_bytes.rstrip(b"\x00").decode("utf-8", errors="ignore")
        return sender, seq, timestamp_ms, packet[UDP_AUDIO_HEADER_SIZE:], None

    if len(packet) >= UDP_AUDIO_USERNAME_BYTES:
        sender = (
            packet[:UDP_AUDIO_USERNAME_BYTES]
            .rstrip(b"\x00")
            .decode("utf-8", errors="ignore")
        )
        return sender, None, None, packet[UDP_AUDIO_USERNAME_BYTES:], None

    raise ValueError("invalid udp audio packet")


def encode_online_query(username: str) -> str:
    msg = {"type": "online_query", "username": username}
    return json.dumps(msg, ensure_ascii=False)


def encode_quality_report(
    room_id: str,
    username: str,
    delay_ms: float,
    jitter_ms: float,
    packet_loss_percent: float,
    sample_count: int = 0,
) -> str:
    msg = {
        "type": "quality_report",
        "room_id": room_id,
        "username": username,
        "delay_ms": float(delay_ms),
        "jitter_ms": float(jitter_ms),
        "packet_loss_percent": float(packet_loss_percent),
        "sample_count": int(sample_count),
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_login(
    username: str, media_port: int = 0, local_ip: str = ""
) -> str:
    msg = {"type": "login", "username": username}
    if media_port > 0:
        msg["media_port"] = int(media_port)
    if local_ip:
        msg["local_ip"] = local_ip
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
    call_id: str,
    peer: str,
    mode: str,
    peer_ip: str = "",
    peer_port: int = 0,
    relay_port: int = 0,
    detail: str = "",
) -> str:
    msg = {
        "type": "call_ready",
        "call_id": call_id,
        "peer": peer,
        "mode": mode,
        "peer_ip": peer_ip,
        "peer_port": int(peer_port),
        "relay_port": int(relay_port),
        "detail": detail,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_transport_update(
    call_id: str,
    peer: str,
    mode: str,
    detail: str = "",
) -> str:
    msg = {
        "type": "transport_update",
        "call_id": call_id,
        "peer": peer,
        "mode": mode,
        "detail": detail,
    }
    return json.dumps(msg, ensure_ascii=False)


def encode_direct_path_seen(call_id: str, target: str) -> str:
    msg = {"type": "direct_path_seen", "call_id": call_id, "target": target}
    return json.dumps(msg, ensure_ascii=False)


def encode_media_stop(target: str) -> str:
    return json.dumps({"type": "media_stop", "target": target}, ensure_ascii=False)


def encode_audio_frame(
    stream_id: str,
    sequence: int,
    timestamp_ms: int,
    sender: str,
    target: str,
    mode: str,
    raw: bytes,
) -> bytes:
    if not raw:
        raise ValueError("audio frame is empty")
    msg = {
        "kind": "audio_frame",
        "stream_id": stream_id,
        "sequence": int(sequence),
        "timestamp_ms": int(timestamp_ms),
        "sender": sender,
        "target": target,
        "mode": mode,
        "data": base64.b64encode(raw).decode("ascii"),
    }
    return json.dumps(msg, ensure_ascii=False).encode("utf-8")


def encode_media_probe(sender: str, target: str, call_id: str, mode: str) -> bytes:
    msg = {
        "kind": "media_probe",
        "sender": sender,
        "target": target,
        "call_id": call_id,
        "mode": mode,
    }
    return json.dumps(msg, ensure_ascii=False).encode("utf-8")


def decode_media_packet(raw: bytes) -> tuple[MediaPacketType, dict]:
    if not raw:
        raise ValueError("empty media packet")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid media json: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("media packet is not a dict")
    packet_type = obj.get("kind")
    if packet_type not in {"audio_frame", "media_probe"}:
        raise ValueError(f"unknown media kind: {packet_type}")
    return packet_type, obj


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
    "call_ready",
    "transport_update",
    "direct_path_seen",
    "media_stop",
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
    "quality_report",
)


def decode_message(raw: str) -> tuple:
    if not raw:
        raise ValueError("empty message")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid json: {exc}") from exc

    if not isinstance(obj, dict):
        raise ValueError("message is not a dict")

    message_type = obj.get("type")
    if message_type is None:
        raise ValueError("message has no type field")
    if message_type not in VALID_TYPES:
        raise ValueError(f"unknown type: {message_type}")
    return message_type, obj


def decode_response(raw: str) -> tuple:
    obj = json.loads(raw)
    success = obj.get("success", False)
    message = obj.get("message", "")
    data = obj.get("data", {})
    return success, message, data
