"""
Audio chat server.
"""

from __future__ import annotations

import json
import socket
import threading
from datetime import datetime
from typing import Any

from audio_config import DEFAULT_PORT
from audio_protocol import AudioProtocol
from custom_exceptions import ProtocolError


class AudioServer:
    """TCP server that handles broadcast and private audio delivery."""

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.__host = host
        self.__port = port
        self.__connections: list[socket.socket | None] = []
        self.__nicknames: list[str | None] = []
        self.__lock = threading.Lock()

    def __log(self, message: str, level: str = "INFO") -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {level}: {message}")

    def __get_active_users(self) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        with self.__lock:
            for user_id in range(1, len(self.__connections)):
                if self.__connections[user_id] and self.__nicknames[user_id]:
                    users.append({"id": user_id, "nickname": self.__nicknames[user_id]})
        return users

    def __push_user_list_to_all(self) -> None:
        users = self.__get_active_users()
        payload = {"type": "user_list", "users": users}

        with self.__lock:
            active_ids = [
                user_id
                for user_id in range(1, len(self.__connections))
                if self.__connections[user_id]
            ]

        for user_id in active_ids:
            self.__send_json_to_user(user_id, payload)

    def __resolve_recipient(
        self,
        recipient_id: int | None = None,
        recipient_nickname: str | None = None,
    ) -> int | None:
        with self.__lock:
            if recipient_id is not None:
                if recipient_id < len(self.__connections) and self.__connections[recipient_id]:
                    return recipient_id
                return None

            if recipient_nickname:
                for user_id in range(1, len(self.__connections)):
                    if (
                        self.__connections[user_id]
                        and self.__nicknames[user_id] == recipient_nickname
                    ):
                        return user_id
        return None

    def __send_json_to_user(self, user_id: int, payload: dict[str, Any]) -> bool:
        with self.__lock:
            connection = (
                self.__connections[user_id]
                if 0 <= user_id < len(self.__connections)
                else None
            )

        if not connection:
            return False

        try:
            connection.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            return True
        except Exception as exc:
            self.__log(f"failed to send message to user {user_id}: {exc}", "ERROR")
            with self.__lock:
                if user_id < len(self.__connections):
                    self.__connections[user_id] = None
            return False

    def __send_system_message(self, user_id: int, message: str) -> None:
        self.__send_json_to_user(
            user_id,
            {
                "sender_id": 0,
                "sender_nickname": "System",
                "message": message,
                "is_private": False,
            },
        )

    def __deliver_text_message(
        self,
        sender_id: int,
        content: str,
        recipient_id: int | None = None,
        recipient_nickname: str | None = None,
    ) -> None:
        sender_nickname = self.__nicknames[sender_id]

        if recipient_id is not None or recipient_nickname:
            target_id = self.__resolve_recipient(recipient_id, recipient_nickname)
            if target_id is None or target_id == sender_id:
                self.__send_system_message(sender_id, "target recipient does not exist")
                return

            delivered = self.__send_json_to_user(
                target_id,
                {
                    "sender_id": sender_id,
                    "sender_nickname": sender_nickname,
                    "message": content,
                    "is_private": True,
                },
            )
            if delivered:
                self.__send_system_message(
                    sender_id,
                    f"private text sent to {self.__nicknames[target_id]}({target_id})",
                )
            else:
                self.__send_system_message(sender_id, "failed to deliver private text")
            return

        self.__broadcast(sender_id, content)

    def __user_thread(self, user_id: int) -> None:
        connection = self.__connections[user_id]
        nickname = self.__nicknames[user_id]

        self.__log(f"user {user_id} {nickname} joined")
        self.__broadcast(message=f"user {nickname}({user_id}) joined the chat room")
        self.__push_user_list_to_all()

        buffer = ""
        while True:
            try:
                data = connection.recv(4096).decode("utf-8")
                if not data:
                    self.__log(f"user {user_id} {nickname} disconnected")
                    break

                buffer += data
                while "\n" in buffer:
                    message, buffer = buffer.split("\n", 1)
                    if message.strip():
                        self.__handle_message(user_id, message.strip())
            except ConnectionResetError:
                self.__log(f"user {user_id} {nickname} reset the connection", "WARNING")
                break
            except Exception as exc:
                self.__log(
                    f"failed to process message from user {user_id}: {exc}",
                    "ERROR",
                )
                break

        self.__cleanup_connection(user_id, nickname or f"User{user_id}")

    def __handle_message(self, user_id: int, message: str) -> None:
        try:
            obj = json.loads(message)
            message_type = obj.get("type", "")

            if message_type == "audio":
                self.__handle_audio_message(user_id, message)
            elif message_type in {"broadcast", "text"}:
                self.__deliver_text_message(
                    sender_id=user_id,
                    content=obj.get("message", ""),
                    recipient_id=obj.get("recipient_id"),
                    recipient_nickname=obj.get("recipient_nickname"),
                )
            elif message_type == "list_users_request":
                self.__send_json_to_user(
                    user_id,
                    {"type": "user_list", "users": self.__get_active_users()},
                )
            elif message_type == "logout":
                nickname = self.__nicknames[user_id]
                self.__log(f"user {user_id} {nickname} logged out")
                self.__broadcast(message=f"user {nickname}({user_id}) left the chat room")
                if self.__connections[user_id]:
                    self.__connections[user_id].close()
                with self.__lock:
                    self.__connections[user_id] = None
                    self.__nicknames[user_id] = None
                self.__push_user_list_to_all()
            else:
                self.__log(f"unknown message type: {message_type}", "WARNING")
        except json.JSONDecodeError as exc:
            self.__log(f"failed to parse JSON: {exc}", "ERROR")
        except Exception as exc:
            self.__log(f"failed to handle message: {exc}", "ERROR")

    def __handle_audio_message(self, user_id: int, message: str) -> None:
        try:
            payload = AudioProtocol.parse_message(message)
            filename, audio_data = AudioProtocol.decode_message(message)
            nickname = self.__nicknames[user_id]
            recipient_id = payload.get("recipient_id")
            recipient_nickname = payload.get("recipient_nickname")

            self.__log(
                f"received audio from user {user_id} {nickname}: "
                f"{filename} ({len(audio_data)} bytes)"
            )

            forwarded = dict(payload)
            forwarded["sender_id"] = user_id
            forwarded["sender_nickname"] = nickname

            if recipient_id is not None or recipient_nickname:
                target_id = self.__resolve_recipient(recipient_id, recipient_nickname)
                if target_id is None or target_id == user_id:
                    self.__send_system_message(user_id, "target recipient does not exist")
                    self.__log(
                        f"private audio target not found for user {user_id}: "
                        f"id={recipient_id}, nickname={recipient_nickname}",
                        "WARNING",
                    )
                    return

                if self.__send_json_to_user(target_id, forwarded):
                    target_name = self.__nicknames[target_id]
                    self.__send_system_message(
                        user_id,
                        f"private audio sent to {target_name}({target_id})",
                    )
                    self.__log(
                        f"private audio from {user_id} delivered to {target_id}",
                        "INFO",
                    )
                else:
                    self.__send_system_message(user_id, "failed to deliver private audio")
                return

            self.__broadcast_audio(user_id, forwarded)
        except ProtocolError as exc:
            self.__log(f"invalid audio message: {exc}", "ERROR")
        except Exception as exc:
            self.__log(f"failed to handle audio message: {exc}", "ERROR")

    def __broadcast(self, user_id: int = 0, message: str = "") -> None:
        with self.__lock:
            for i in range(1, len(self.__connections)):
                if user_id != i and self.__connections[i]:
                    try:
                        self.__connections[i].send(
                            json.dumps(
                                {
                                    "sender_id": user_id,
                                    "sender_nickname": self.__nicknames[user_id],
                                    "message": message,
                                    "is_private": False,
                                }
                            ).encode("utf-8")
                            + b"\n"
                        )
                    except Exception as exc:
                        self.__log(f"failed to send message to user {i}: {exc}", "ERROR")

    def __broadcast_audio(self, sender_id: int, audio_payload: dict[str, Any]) -> None:
        with self.__lock:
            for i in range(1, len(self.__connections)):
                if i != sender_id and self.__connections[i]:
                    try:
                        self.__connections[i].send(
                            (json.dumps(audio_payload) + "\n").encode("utf-8")
                        )
                    except Exception as exc:
                        self.__log(
                            f"failed to broadcast audio to user {i}: {exc}",
                            "ERROR",
                        )
                        self.__connections[i] = None

    def __cleanup_connection(self, user_id: int, nickname: str) -> None:
        with self.__lock:
            if self.__connections[user_id]:
                try:
                    self.__connections[user_id].close()
                except Exception:
                    pass
                self.__connections[user_id] = None
                self.__nicknames[user_id] = None

        self.__log(f"user {user_id} {nickname} cleaned up")
        self.__broadcast(message=f"user {nickname}({user_id}) left the chat room")
        self.__push_user_list_to_all()

    def __wait_for_login(self, connection: socket.socket) -> None:
        try:
            buffer = connection.recv(1024).decode("utf-8")
            obj = json.loads(buffer)

            if obj["type"] == "login":
                nickname = obj["nickname"]
                with self.__lock:
                    self.__connections.append(connection)
                    self.__nicknames.append(nickname)
                    user_id = len(self.__connections) - 1

                connection.send(json.dumps({"id": user_id}).encode("utf-8"))
                self.__log(f"new user login: {nickname} (ID: {user_id})")
                self.__send_json_to_user(
                    user_id,
                    {"type": "user_list", "users": self.__get_active_users()},
                )

                thread = threading.Thread(target=self.__user_thread, args=(user_id,))
                thread.daemon = True
                thread.start()
            else:
                self.__log(
                    f"unable to parse login packet from {connection.getpeername()}",
                    "WARNING",
                )
                connection.close()
        except json.JSONDecodeError as exc:
            self.__log(f"failed to parse login JSON: {exc}", "ERROR")
            connection.close()
        except Exception as exc:
            self.__log(f"failed to handle login request: {exc}", "ERROR")
            connection.close()

    def start(self) -> None:
        try:
            self.__socket.bind((self.__host, self.__port))
            self.__socket.listen(10)

            self.__log(f"server listening on {self.__host}:{self.__port}")

            self.__connections.clear()
            self.__nicknames.clear()
            self.__connections.append(None)
            self.__nicknames.append("System")

            while True:
                connection, address = self.__socket.accept()
                self.__log(f"received new connection from {address}")
                thread = threading.Thread(target=self.__wait_for_login, args=(connection,))
                thread.daemon = True
                thread.start()
        except KeyboardInterrupt:
            self.__log("server interrupted, shutting down", "INFO")
        except Exception as exc:
            self.__log(f"server failed to start: {exc}", "ERROR")
        finally:
            self.__socket.close()
            self.__log("server closed")


def main() -> None:
    server = AudioServer()
    server.start()


if __name__ == "__main__":
    main()
