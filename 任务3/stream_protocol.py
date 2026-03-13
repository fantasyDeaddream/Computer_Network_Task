"""
任务3：实时音频流传输协议

和任务2一次性“文件式”传输不同，这里使用小音频块（chunk）进行连续发送，
实现近实时语音对讲。

消息统一使用 JSON 文本，以换行符('\n')作为分隔。
"""

import base64
import json
from dataclasses import dataclass
from typing import Literal


MessageType = Literal["login", "text", "audio_chunk"]


@dataclass
class LoginMessage:
    nickname: str


@dataclass
class TextMessage:
    content: str


@dataclass
class AudioChunkMessage:
    stream_id: str
    data: bytes


def encode_login(nickname: str) -> str:
    msg = {"type": "login", "nickname": nickname}
    return json.dumps(msg)


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

    t = obj.get("type")
    if t not in ("login", "text", "audio_chunk"):
        raise ValueError(f"unknown type: {t}")
    return t, obj

