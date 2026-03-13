"""
任务3 实时音频流服务器

独立于任务2的 qser.py，不修改任何任务2文件。
协议见 stream_protocol.py。
"""

import socket
import threading
from dataclasses import dataclass
from typing import Dict, Tuple

from stream_protocol import decode_message, encode_text
from pathlib import Path
import sys


def _ensure_task2_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task2_dir = base / "任务2"
    if str(task2_dir) not in sys.path:
        sys.path.insert(0, str(task2_dir))


_ensure_task2_on_path()

from audio_config import DEFAULT_PORT, MESSAGE_DELIMITER  # type: ignore  # noqa: E402


@dataclass
class ClientInfo:
    conn: socket.socket
    addr: Tuple[str, int]
    nickname: str


class StreamServer:
    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._clients: Dict[int, ClientInfo] = {}
        self._lock = threading.Lock()
        self._running = False

    # -------- 公共接口 --------
    def start(self) -> None:
        """阻塞运行服务器，直到 stop() 被调用或进程结束。"""
        self._sock.bind((self._host, self._port))
        self._sock.listen(10)
        self._running = True
        print(f"[Server] StreamServer 监听 {self._host}:{self._port}")

        try:
            while self._running:
                try:
                    conn, addr = self._sock.accept()
                except OSError:
                    # 关闭时 accept 会抛异常
                    break
                threading.Thread(
                    target=self._handle_client, args=(conn, addr), daemon=True
                ).start()
        finally:
            with self._lock:
                for cid, info in list(self._clients.items()):
                    try:
                        info.conn.close()
                    except Exception:
                        pass
                self._clients.clear()
            try:
                self._sock.close()
            except Exception:
                pass
            print("[Server] StreamServer 已关闭")

    def stop(self) -> None:
        """请求停止服务器（线程安全）。"""
        self._running = False
        try:
            self._sock.close()
        except Exception:
            pass

    # -------- 内部实现 --------
    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        cid = id(conn)
        nickname = f"User-{addr[0]}:{addr[1]}"

        with self._lock:
            self._clients[cid] = ClientInfo(conn=conn, addr=addr, nickname=nickname)
        print(f"[Server] 新连接: {addr}")

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
                        mtype, payload = decode_message(line)
                    except ValueError as e:
                        print(f"[Server] 无效消息: {e}")
                        continue
                    if mtype == "login":
                        nickname = payload.get("nickname") or nickname
                        with self._lock:
                            info = self._clients.get(cid)
                            if info:
                                info.nickname = nickname
                        self._broadcast_text(f"{nickname} 加入会话", exclude=cid)
                    elif mtype == "text":
                        content = payload.get("content", "")
                        self._broadcast_text(f"{nickname}: {content}", exclude=None)
                    elif mtype == "audio_chunk":
                        self._broadcast_raw(line + MESSAGE_DELIMITER, exclude=cid)
        except ConnectionResetError:
            pass
        finally:
            with self._lock:
                self._clients.pop(cid, None)
            try:
                conn.close()
            except Exception:
                pass
            print(f"[Server] 连接关闭: {addr}")
            self._broadcast_text(f"{nickname} 离开会话", exclude=None)

    def _broadcast_raw(self, raw: str, exclude: int | None) -> None:
        data = raw.encode("utf-8")
        with self._lock:
            for cid, info in list(self._clients.items()):
                if exclude is not None and cid == exclude:
                    continue
                try:
                    info.conn.sendall(data)
                except Exception:
                    try:
                        info.conn.close()
                    except Exception:
                        pass
                    self._clients.pop(cid, None)

    def _broadcast_text(self, text: str, exclude: int | None) -> None:
        msg = encode_text(text) + MESSAGE_DELIMITER
        self._broadcast_raw(msg, exclude=exclude)


def main() -> None:
    server = StreamServer()
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()

