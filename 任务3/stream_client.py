"""
任务3 实时音频流客户端核心

使用 pyaudio 以小块（chunk）形式连续采集与播放，达到近实时效果。
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
import sys

import pyaudio

from stream_protocol import (
    decode_message,
    encode_audio_chunk,
    encode_login,
    encode_text,
)


def _ensure_task2_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task2_dir = base / "任务2"
    if str(task2_dir) not in sys.path:
        sys.path.insert(0, str(task2_dir))


_ensure_task2_on_path()

from audio_config import (  # type: ignore  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    MESSAGE_DELIMITER,
    SAMPLE_RATE,
    CHANNELS,
    AUDIO_FORMAT,
    CHUNK_SIZE,
)


@dataclass
class StreamClientConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    nickname: str = "User"


class StreamClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        nickname: str = "User",
        on_text: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cfg = StreamClientConfig(host=host, port=port, nickname=nickname)
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._sending = False

        self._recv_thread: Optional[threading.Thread] = None
        self._send_thread: Optional[threading.Thread] = None

        self._p = pyaudio.PyAudio()
        self._out_stream: Optional[pyaudio.Stream] = None
        self._in_stream: Optional[pyaudio.Stream] = None

        self._stream_id = uuid.uuid4().hex
        self._on_text = on_text

    # -------- 连接与关闭 --------
    def connect(self) -> bool:
        if self._sock:
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((self.cfg.host, self.cfg.port))
            print(f"[Client] 已连接 {self.cfg.host}:{self.cfg.port}")

            # 发送登录
            login = encode_login(self.cfg.nickname) + MESSAGE_DELIMITER
            self._sock.sendall(login.encode("utf-8"))

            # 打开播放流
            self._out_stream = self._p.open(
                format=AUDIO_FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                frames_per_buffer=CHUNK_SIZE,
            )

            self._running = True
            self._recv_thread = threading.Thread(
                target=self._recv_loop, daemon=True
            )
            self._recv_thread.start()
            return True
        except Exception as e:
            print(f"[Client] 连接失败: {e}")
            self._cleanup_socket()
            return False

    def disconnect(self) -> None:
        self._running = False
        self._sending = False
        try:
            if self._in_stream:
                self._in_stream.stop_stream()
                self._in_stream.close()
        except Exception:
            pass
        self._in_stream = None

        try:
            if self._out_stream:
                self._out_stream.stop_stream()
                self._out_stream.close()
        except Exception:
            pass
        self._out_stream = None

        self._cleanup_socket()

    def _cleanup_socket(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None

    # -------- 文本消息 --------
    def send_text(self, content: str) -> None:
        if not self._sock:
            raise RuntimeError("未连接服务器")
        msg = encode_text(content) + MESSAGE_DELIMITER
        self._sock.sendall(msg.encode("utf-8"))

    # -------- 实时音频流 --------
    def start_streaming(self) -> None:
        if not self._sock:
            raise RuntimeError("未连接服务器")
        if self._sending:
            return

        # 打开输入流
        try:
            self._in_stream = self._p.open(
                format=AUDIO_FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as e:
            print(f"[Client] 打开输入设备失败: {e}")
            raise

        self._sending = True
        self._send_thread = threading.Thread(
            target=self._send_loop, daemon=True
        )
        self._send_thread.start()
        print("[Client] 开始实时语音发送... 再次点击停止按钮结束。")

    def stop_streaming(self) -> None:
        self._sending = False
        print("[Client] 停止实时语音发送。")

    # -------- 后台线程 --------
    def _send_loop(self) -> None:
        assert self._in_stream is not None
        while self._sending and self._sock:
            try:
                data = self._in_stream.read(
                    CHUNK_SIZE, exception_on_overflow=False
                )
            except Exception:
                break
            if not data:
                continue
            try:
                msg = encode_audio_chunk(self._stream_id, data)
                wire = msg + MESSAGE_DELIMITER
                self._sock.sendall(wire.encode("utf-8"))
            except Exception:
                break
            # 小睡片刻，避免阻塞 GUI，CHUNK_SIZE 已经控制时长，这里只做轻微让步
            time.sleep(0.001)
        self._sending = False

    def _recv_loop(self) -> None:
        buf = ""
        while self._running and self._sock:
            try:
                data = self._sock.recv(4096)
            except Exception:
                break
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
                except ValueError as e:
                    print(f"[Client] 无效消息: {e}")
                    continue
                if mtype == "text":
                    text = payload.get("content", "")
                    if self._on_text:
                        self._on_text(text)
                    else:
                        print(text)
                elif mtype == "audio_chunk":
                    b64 = payload.get("data", "")
                    import base64

                    try:
                        raw = base64.b64decode(b64)
                    except Exception:
                        continue
                    if self._out_stream:
                        try:
                            self._out_stream.write(
                                raw, exception_on_underflow=False
                            )
                        except Exception:
                            pass

        self._running = False
        print("[Client] 已从服务器断开。")

