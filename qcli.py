"""
Audio chat client.

Supports text chat, broadcast audio, private audio and sending existing WAV
files.
"""

from __future__ import annotations

import json
import re
import socket
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from audio_config import DEFAULT_HOST, DEFAULT_PORT, MAX_AUDIO_SIZE, MESSAGE_DELIMITER
from audio_decoder import AudioDecoder
from audio_encoder import AudioEncoder
from audio_player import AudioPlayer
from audio_protocol import AudioProtocol
from audio_recorder import AudioRecorder
from custom_exceptions import (
    AudioDeviceError,
    DecodingError,
    EncodingError,
    PlaybackError,
    ProtocolError,
)

EventCallback = Callable[[dict[str, Any]], None]


class AudioClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        nickname: str | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.nickname = nickname or f"User_{id(self) % 10000}"
        self.event_callback = event_callback

        self.socket: socket.socket | None = None
        self.is_connected = False
        self.is_running = False
        self.user_id: int | None = None
        self.recording_count = 0
        self.online_users: list[dict[str, Any]] = []

        self.recorder = AudioRecorder()
        self.player = AudioPlayer()
        self.encoder = AudioEncoder()
        self.decoder = AudioDecoder()

    def set_event_callback(self, callback: EventCallback | None) -> None:
        self.event_callback = callback

    def snapshot(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "nickname": self.nickname,
            "user_id": self.user_id,
            "connected": self.is_connected,
            "running": self.is_running,
            "recording": self.recorder.is_recording(),
            "online_users": list(self.online_users),
        }

    def _emit(self, event_type: str, **payload: Any) -> None:
        if not self.event_callback:
            return

        event = {"type": event_type, **payload}
        try:
            self.event_callback(event)
        except Exception:
            pass

    def _log(self, message: str, level: str = "info", **payload: Any) -> None:
        print(message)
        self._emit("log", level=level, message=message, **payload)

    def _emit_status(self) -> None:
        self._emit("status", **self.snapshot())

    def _parse_recipient(
        self, recipient: str | int | None
    ) -> tuple[int | None, str | None]:
        if recipient is None:
            return None, None
        if isinstance(recipient, int):
            return recipient, None

        normalized = str(recipient).strip()
        if not normalized:
            return None, None
        if normalized.isdigit():
            return int(normalized), None
        id_match = re.search(r"\((\d+)\)\s*$", normalized)
        if id_match:
            return int(id_match.group(1)), None
        prefix_match = re.match(r"^\s*(\d+)\s*[:：|-]\s*(.+)$", normalized)
        if prefix_match:
            return int(prefix_match.group(1)), None
        return None, normalized

    def connect(self) -> bool:
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self._log(f"[Client] Connected to server {self.host}:{self.port}")

            login_message = json.dumps({"type": "login", "nickname": self.nickname})
            self.socket.send(login_message.encode("utf-8"))

            response = self.socket.recv(1024).decode("utf-8")
            response_data = json.loads(response)
            self.user_id = response_data.get("id")

            self.is_connected = True
            self._emit_status()
            self._log(
                f"[Client] Login successful, user_id={self.user_id}, nickname={self.nickname}"
            )
            return True
        except Exception as exc:
            self._log(f"[Client] Connection failed: {exc}", level="error")
            self.is_connected = False
            self._emit_status()
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
            self.socket = None
            return False

    def disconnect(self) -> None:
        self.is_running = False

        if self.is_connected and self.socket:
            try:
                logout_message = json.dumps({"type": "logout", "nickname": self.nickname})
                self.socket.send((logout_message + MESSAGE_DELIMITER).encode("utf-8"))
                self._log("[Client] Logout message sent")
            except Exception as exc:
                self._log(f"[Client] Failed to send logout message: {exc}", level="error")

        self.is_connected = False
        self.user_id = None
        self.online_users = []
        self._emit_status()

        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

        self._log("[Client] Disconnected")

    def start_background_receive(self) -> bool:
        if not self.connect():
            return False

        self.is_running = True
        self._emit_status()
        receive_thread = threading.Thread(target=self._receive_messages_thread, daemon=True)
        receive_thread.start()
        self._emit("connected", **self.snapshot())
        self.request_user_list()
        return True

    def _receive_messages_thread(self) -> None:
        buffer = ""

        while self.is_running and self.is_connected and self.socket:
            try:
                data = self.socket.recv(4096).decode("utf-8")
                if not data:
                    self._log("[Client] Server connection closed", level="warning")
                    self.is_connected = False
                    self._emit_status()
                    break

                buffer += data
                while MESSAGE_DELIMITER in buffer:
                    message, buffer = buffer.split(MESSAGE_DELIMITER, 1)
                    if message:
                        self._handle_received_message(message)
            except Exception as exc:
                if self.is_running:
                    self._log(f"[Client] Error receiving message: {exc}", level="error")
                self.is_connected = False
                self._emit_status()
                break

        self.is_connected = False
        self.is_running = False
        self._emit_status()

    def _handle_received_message(self, message: str) -> None:
        try:
            msg_dict = json.loads(message)
            msg_type = msg_dict.get("type", "")

            if msg_type == "audio":
                self._handle_audio_message(message)
                return

            if msg_type == "user_list":
                users = msg_dict.get("users", [])
                self.online_users = list(users)
                self._emit("user_list", users=list(self.online_users))
                self._emit_status()
                if users:
                    user_text = ", ".join(
                        f"{user['nickname']}({user['id']})" for user in users
                    )
                else:
                    user_text = "no online users"
                self._log(f"[Client] Online users: {user_text}")
                return

            if "sender_id" in msg_dict and "message" in msg_dict:
                sender_id = msg_dict.get("sender_id", 0)
                sender_nickname = msg_dict.get("sender_nickname", "Unknown")
                content = msg_dict.get("message", "")
                is_private = msg_dict.get("is_private", False)

                if sender_id == 0:
                    log_message = f"[System] {content}"
                else:
                    prefix = "[Private]" if is_private else "[Broadcast]"
                    log_message = f"{prefix} [{sender_nickname}] {content}"

                self._log(log_message)
                self._emit(
                    "text_message",
                    sender_id=sender_id,
                    sender_nickname=sender_nickname,
                    content=content,
                    is_private=is_private,
                )
                return

            self._log(
                f"[Client] Unknown message format: {message[:100]}",
                level="warning",
            )
        except json.JSONDecodeError:
            self._log("[Client] Invalid JSON message", level="error")
        except Exception as exc:
            self._log(f"[Client] Failed to handle message: {exc}", level="error")

    def _handle_audio_message(self, message: str) -> None:
        try:
            payload = AudioProtocol.parse_message(message)
            filename, wav_data = AudioProtocol.decode_message(message)
            sender_id = payload.get("sender_id")
            sender_nickname = payload.get("sender_nickname", "Unknown")
            source = payload.get("source", "recording")
            is_private = payload.get("recipient_id") is not None or payload.get(
                "recipient_nickname"
            ) is not None

            audio_frames, sample_rate, channels, sample_width = self.decoder.decode_wav(
                wav_data
            )

            privacy = "private " if is_private else ""
            self._log(
                f"[Audio] Received {privacy}{source} audio from "
                f"{sender_nickname}({sender_id}): {filename}"
            )
            self._emit(
                "audio_received",
                filename=filename,
                sample_rate=sample_rate,
                channels=channels,
                sample_width=sample_width,
                bytes=len(audio_frames),
                sender_id=sender_id,
                sender_nickname=sender_nickname,
                is_private=is_private,
                source=source,
            )

            self._log("[Audio] Playing audio...")
            self.player.play(audio_frames)
            self._log("[Audio] Playback finished")
        except ProtocolError as exc:
            self._log(f"[Audio] Protocol error: {exc}", level="error")
        except DecodingError as exc:
            self._log(f"[Audio] Decode error: {exc}", level="error")
        except AudioDeviceError as exc:
            self._log(f"[Audio] Audio device error: {exc}", level="error")
        except PlaybackError as exc:
            self._log(f"[Audio] Playback error: {exc}", level="error")
        except Exception as exc:
            self._log(f"[Audio] Failed to process audio message: {exc}", level="error")

    def start_recording(self) -> bool:
        if self.recorder.is_recording():
            self._log("[Audio] Recording already in progress", level="warning")
            return False

        try:
            self.recorder.start_recording()
            self._emit_status()
            self._emit("recording_started")
            self._log("[Audio] Recording started")
            return True
        except AudioDeviceError as exc:
            self._log(f"[Audio] Unable to start recording: {exc}", level="error")
            return False

    def stop_recording_and_send(self, recipient: str | int | None = None) -> bool:
        if not self.recorder.is_recording():
            self._log("[Audio] Recording is not active", level="warning")
            return False

        try:
            self._log("[Audio] Stopping recording...")
            audio_data = self.recorder.stop_recording()
            self._emit_status()

            if not audio_data:
                self._log("[Audio] Recording produced empty data", level="warning")
                return False

            self._log("[Audio] Encoding WAV data...")
            wav_data = self.encoder.encode_to_wav(audio_data)

            self.recording_count += 1
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"recording_{self.recording_count}_{timestamp}.wav"
            return self._send_wav_bytes(
                wav_data=wav_data,
                filename=filename,
                recipient=recipient,
                source="recording",
            )
        except AudioDeviceError as exc:
            self._log(f"[Audio] Recording device error: {exc}", level="error")
        except EncodingError as exc:
            self._log(f"[Audio] Encode error: {exc}", level="error")
        except ProtocolError as exc:
            self._log(f"[Audio] Protocol error: {exc}", level="error")
        except Exception as exc:
            self._log(f"[Audio] Failed to send audio: {exc}", level="error")
        finally:
            self._emit_status()

        return False

    def send_wav_file(self, file_path: str | Path, recipient: str | int | None = None) -> bool:
        path = Path(file_path)
        if not path.exists():
            self._log(f"[Audio] WAV file not found: {path}", level="error")
            return False
        if path.suffix.lower() != ".wav":
            self._log(f"[Audio] Only .wav files are supported: {path.name}", level="error")
            return False

        try:
            wav_data = path.read_bytes()
            audio_frames, sample_rate, channels, sample_width = self.decoder.normalize_wav(
                wav_data
            )
            normalized_wav = self.encoder.encode_to_wav(
                audio_frames,
                sample_rate=sample_rate,
                channels=channels,
                sample_width=sample_width,
            )
            return self._send_wav_bytes(
                wav_data=normalized_wav,
                filename=path.name,
                recipient=recipient,
                source="wav_file",
            )
        except DecodingError as exc:
            self._log(f"[Audio] Invalid WAV file: {exc}", level="error")
            return False
        except EncodingError as exc:
            self._log(f"[Audio] Failed to normalize WAV file: {exc}", level="error")
            return False
        except Exception as exc:
            self._log(f"[Audio] Failed to send WAV file: {exc}", level="error")
            return False

    def _send_wav_bytes(
        self,
        wav_data: bytes,
        filename: str,
        recipient: str | int | None = None,
        source: str = "recording",
    ) -> bool:
        try:
            if len(wav_data) > MAX_AUDIO_SIZE:
                self._log(
                    f"[Audio] Audio file too large: {len(wav_data)} bytes > {MAX_AUDIO_SIZE} bytes",
                    level="error",
                )
                return False

            recipient_id, recipient_nickname = self._parse_recipient(recipient)
            message = AudioProtocol.encode_message(
                filename=filename,
                audio_data=wav_data,
                recipient_id=recipient_id,
                recipient_nickname=recipient_nickname,
                source=source,
            )
            self._send_message(message)

            target_desc = "broadcast"
            if recipient_id is not None:
                target_desc = f"user_id={recipient_id}"
            elif recipient_nickname:
                target_desc = f"nickname={recipient_nickname}"

            self._emit(
                "audio_sent",
                filename=filename,
                bytes=len(wav_data),
                recipient_id=recipient_id,
                recipient_nickname=recipient_nickname,
                source=source,
            )
            self._log(f"[Audio] Sent {source} audio {filename} to {target_desc}")
            return True
        except ProtocolError as exc:
            self._log(f"[Audio] Protocol error: {exc}", level="error")
        except Exception as exc:
            self._log(f"[Audio] Failed to send audio payload: {exc}", level="error")
        return False

    def send_text_message(self, content: str, recipient: str | int | None = None) -> bool:
        content = content.strip()
        if not content:
            self._log("[Client] Text message is empty", level="warning")
            return False

        try:
            recipient_id, recipient_nickname = self._parse_recipient(recipient)
            message = {
                "type": "text",
                "message": content,
            }
            if recipient_id is not None:
                message["recipient_id"] = recipient_id
            if recipient_nickname:
                message["recipient_nickname"] = recipient_nickname

            self._send_message(json.dumps(message))

            if recipient_id is not None:
                target_desc = f"user_id={recipient_id}"
            elif recipient_nickname:
                target_desc = f"nickname={recipient_nickname}"
            else:
                target_desc = "broadcast"

            self._emit(
                "text_sent",
                content=content,
                recipient_id=recipient_id,
                recipient_nickname=recipient_nickname,
            )
            self._log(f"[You -> {target_desc}] {content}")
            return True
        except Exception as exc:
            self._log(f"[Client] Failed to send text message: {exc}", level="error")
            return False

    def request_user_list(self) -> bool:
        try:
            self._send_message(json.dumps({"type": "list_users_request"}))
            return True
        except Exception as exc:
            self._log(f"[Client] Failed to request user list: {exc}", level="error")
            return False

    def _send_message(self, message: str) -> None:
        if not self.is_connected or not self.socket:
            raise ConnectionError("Not connected to the server")

        try:
            data = (message + MESSAGE_DELIMITER).encode("utf-8")
            self.socket.sendall(data)
        except Exception as exc:
            self.is_connected = False
            self._emit_status()
            raise ConnectionError(f"Send failed: {exc}") from exc

    def run(self) -> None:
        if not self.start_background_receive():
            return

        print("\n" + "=" * 60)
        print("Audio Chat Client")
        print("=" * 60)
        print("Commands:")
        print("  /record or /r              - start recording")
        print("  /stop or /s [target]       - stop recording and send audio")
        print("  /sendwav <path> [target]   - send an existing wav file")
        print("  /msg <target> <content>    - send a private text message")
        print("  /users                     - refresh online user list")
        print("  /quit or /q                - quit")
        print("  target empty = broadcast; integer = user id; text = nickname")
        print("=" * 60 + "\n")

        try:
            while self.is_running and self.is_connected:
                try:
                    user_input = input().strip()
                    if not user_input:
                        continue

                    parts = user_input.split(maxsplit=2)
                    command = parts[0]

                    if command in {"/record", "/r"}:
                        self.start_recording()
                        continue

                    if command in {"/stop", "/s"}:
                        recipient = parts[1] if len(parts) > 1 else None
                        self.stop_recording_and_send(recipient)
                        continue

                    if command == "/sendwav":
                        if len(parts) < 2:
                            self._log("[Client] Usage: /sendwav <path> [target]", level="warning")
                        else:
                            path = parts[1]
                            recipient = parts[2] if len(parts) > 2 else None
                            self.send_wav_file(path, recipient)
                        continue

                    if command == "/msg":
                        if len(parts) < 3:
                            self._log("[Client] Usage: /msg <target> <content>", level="warning")
                        else:
                            self.send_text_message(parts[2], parts[1])
                        continue

                    if command == "/users":
                        self.request_user_list()
                        continue

                    if command in {"/quit", "/q"}:
                        self._log("[Client] Exiting...")
                        break

                    self.send_text_message(user_input)
                except KeyboardInterrupt:
                    self._log("[Client] Interrupt received, exiting...", level="warning")
                    break
        finally:
            if self.recorder.is_recording():
                try:
                    self.recorder.stop_recording()
                except Exception:
                    pass
            self.disconnect()


def main() -> None:
    import sys

    host = DEFAULT_HOST
    port = DEFAULT_PORT
    nickname = None

    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:
        try:
            port = int(sys.argv[2])
        except ValueError:
            print(f"Invalid port: {sys.argv[2]}")
            raise SystemExit(1)
    if len(sys.argv) > 3:
        nickname = sys.argv[3]

    if not nickname:
        try:
            nickname = input("Please enter your nickname: ").strip() or None
        except (KeyboardInterrupt, EOFError):
            print("\n[Client] Cancelled")
            raise SystemExit(0)

    client = AudioClient(host, port, nickname)
    client.run()


if __name__ == "__main__":
    main()
