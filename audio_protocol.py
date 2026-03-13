"""
Audio transport protocol helpers.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from audio_config import MAX_FILENAME_LENGTH
from custom_exceptions import ProtocolError


class AudioProtocol:
    """Encode and decode audio messages transported as JSON."""

    @staticmethod
    def encode_message(
        filename: str,
        audio_data: bytes,
        recipient_id: int | None = None,
        recipient_nickname: str | None = None,
        source: str = "recording",
    ) -> str:
        if len(filename) > MAX_FILENAME_LENGTH:
            raise ProtocolError(
                f"filename too long: {len(filename)} > {MAX_FILENAME_LENGTH}"
            )
        if not filename:
            raise ProtocolError("filename cannot be empty")
        if not audio_data:
            raise ProtocolError("audio data cannot be empty")
        if not isinstance(audio_data, bytes):
            raise ProtocolError("audio data must be bytes")
        if recipient_id is not None and recipient_id < 1:
            raise ProtocolError("recipient_id must be a positive integer")

        try:
            encoded_data = base64.b64encode(audio_data).decode("utf-8")
            message: dict[str, Any] = {
                "type": "audio",
                "filename": filename,
                "length": len(audio_data),
                "data": encoded_data,
                "source": source,
            }

            if recipient_id is not None:
                message["recipient_id"] = recipient_id
            if recipient_nickname:
                message["recipient_nickname"] = recipient_nickname

            return json.dumps(message)
        except Exception as exc:
            raise ProtocolError(f"failed to encode audio message: {exc}") from exc

    @staticmethod
    def parse_message(message: str) -> dict[str, Any]:
        if not message:
            raise ProtocolError("message cannot be empty")
        if not isinstance(message, str):
            raise ProtocolError("message must be a string")

        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSON format: {exc}") from exc

        if not AudioProtocol.validate_message(payload):
            raise ProtocolError("message validation failed")
        return payload

    @staticmethod
    def decode_message(message: str) -> tuple[str, bytes]:
        payload = AudioProtocol.parse_message(message)

        try:
            filename = payload["filename"]
            audio_data = base64.b64decode(payload["data"])
            return filename, audio_data
        except KeyError as exc:
            raise ProtocolError(f"missing required field: {exc}") from exc
        except Exception as exc:
            raise ProtocolError(f"failed to decode message: {exc}") from exc

    @staticmethod
    def validate_message(message: dict[str, Any]) -> bool:
        if not isinstance(message, dict):
            return False

        required_fields = ["type", "filename", "length", "data"]
        if any(field not in message for field in required_fields):
            return False

        if message["type"] != "audio":
            return False
        if not isinstance(message["filename"], str) or not message["filename"]:
            return False
        if not isinstance(message["length"], int) or message["length"] < 0:
            return False
        if not isinstance(message["data"], str) or not message["data"]:
            return False

        recipient_id = message.get("recipient_id")
        if recipient_id is not None and (
            not isinstance(recipient_id, int) or recipient_id < 1
        ):
            return False

        recipient_nickname = message.get("recipient_nickname")
        if recipient_nickname is not None and not isinstance(recipient_nickname, str):
            return False

        sender_id = message.get("sender_id")
        if sender_id is not None and (not isinstance(sender_id, int) or sender_id < 0):
            return False

        sender_nickname = message.get("sender_nickname")
        if sender_nickname is not None and not isinstance(sender_nickname, str):
            return False

        source = message.get("source")
        if source is not None and not isinstance(source, str):
            return False

        return True
