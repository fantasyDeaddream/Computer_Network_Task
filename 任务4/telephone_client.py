"""
任务4：IP电话系统客户端

负责用户登录、联系人管理和实时语音通话。
"""

from __future__ import annotations

import base64
import json
import queue
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional
import sys

try:
    import pyaudio
except ImportError:
    pyaudio = None

# 添加任务2路径
def _ensure_task2_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task2_dir = base / "任务2"
    if str(task2_dir) not in sys.path:
        sys.path.insert(0, str(task2_dir))


_ensure_task2_on_path()

from audio_config import (  # type: ignore  # noqa: E402
    DEFAULT_HOST,
    SAMPLE_RATE,
    CHANNELS,
    AUDIO_FORMAT,
    CHUNK_SIZE,
)

from telephone_protocol import decode_message, decode_response
from telephone_protocol import (
    encode_login, encode_logout,
    encode_contact_add, encode_contact_delete, encode_contact_update,
    encode_contact_list, encode_contact_search,
    encode_call_invite, encode_call_accept, encode_call_reject, encode_call_hangup,
    encode_text, encode_audio_chunk
)


# 服务器端口
DEFAULT_PORT = 8881
MESSAGE_DELIMITER = '\n'


# 通话状态
class CallState:
    IDLE = "idle"           # 空闲
    CALLING = "calling"     # 呼叫中
    RINGING = "ringing"     # 响铃中（被呼叫）
    IN_CALL = "in_call"     # 通话中
    ENDED = "ended"         # 通话结束


@dataclass
class ClientConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    username: str = ""


class TelephoneClient:
    """IP电话客户端"""
    
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        on_call_state_change: Optional[Callable[[str, str], None]] = None,
        on_text: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cfg = ClientConfig(host=host, port=port)
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._call_state = CallState.IDLE
        self._in_call_with: str = ""
        
        self._recv_thread: Optional[threading.Thread] = None
        self._send_thread: Optional[threading.Thread] = None
        
        self._p = pyaudio.PyAudio() if pyaudio else None
        self._out_stream: Optional[pyaudio.Stream] = None
        self._in_stream: Optional[pyaudio.Stream] = None
        self._recv_thread_started = False
        
        self._stream_id = uuid.uuid4().hex
        self._on_call_state_change = on_call_state_change
        self._on_text = on_text
        
        self._contacts: List[str] = []
        self._is_calling = False
        
        # 响应队列：用于同步请求-响应模式
        self._response_queue: queue.Queue = queue.Queue()
        
        if not self._p:
            print("[Client] 警告: pyaudio未安装，语音功能将不可用")
    
    def _start_recv_thread(self) -> None:
        """启动接收线程"""
        if self._recv_thread_started:
            return
        # 恢复阻塞模式（login中可能设置了超时）
        if self._sock:
            self._sock.settimeout(None)
        self._running = True
        self._recv_thread = threading.Thread(
            target=self._recv_loop, daemon=True
        )
        self._recv_thread.start()
        self._recv_thread_started = True
    
    # ========== 属性 ==========
    
    @property
    def is_connected(self) -> bool:
        return self._sock is not None
    
    @property
    def call_state(self) -> str:
        return self._call_state
    
    @property
    def contacts(self) -> List[str]:
        return self._contacts.copy()
    
    # ========== 连接与关闭 ==========
    
    def _ensure_connected(self) -> bool:
        """确保已连接到服务器"""
        if self._sock:
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((self.cfg.host, self.cfg.port))
            print(f"[Client] 已连接 {self.cfg.host}:{self.cfg.port}")
            return True
        except Exception as e:
            print(f"[Client] 连接失败: {e}")
            self._sock = None
            return False
    
    def login(self, username: str) -> tuple[bool, str]:
        """用户登录（输入用户名即可）"""
        if not username:
            return False, "用户名不能为空"
        
        if not self._ensure_connected():
            return False, "无法连接到服务器"
        
        msg = encode_login(username)
        self._send_raw(msg)
        
        # 同步等待响应（登录时还没有recv线程）
        success, message = self._sync_wait_response()
        if success:
            self.cfg.username = username
            # 登录成功后启动后台接收线程和音频播放流
            if self._p:
                try:
                    self._out_stream = self._p.open(
                        format=AUDIO_FORMAT,
                        channels=CHANNELS,
                        rate=SAMPLE_RATE,
                        output=True,
                        frames_per_buffer=CHUNK_SIZE,
                    )
                except Exception as e:
                    print(f"[Client] 打开音频播放设备失败: {e}")
            self._start_recv_thread()
        return success, message
    
    def _sync_wait_response(self, timeout: float = 5.0) -> tuple[bool, str]:
        """同步等待服务器响应（在recv线程启动前使用）"""
        try:
            self._sock.settimeout(timeout)
            buf = ""
            while True:
                data = self._sock.recv(4096)
                if not data:
                    return False, "连接断开"
                buf += data.decode("utf-8", errors="ignore")
                if MESSAGE_DELIMITER in buf:
                    line, _ = buf.split(MESSAGE_DELIMITER, 1)
                    line = line.strip()
                    if line:
                        obj = json.loads(line)
                        success = obj.get("success", False)
                        message = obj.get("message", "")
                        return success, message
        except socket.timeout:
            return False, "等待响应超时"
        except Exception as e:
            return False, f"网络错误: {e}"
    
    def logout(self) -> None:
        """用户登出"""
        if self._sock and self.cfg.username:
            try:
                msg = encode_logout(self.cfg.username)
                self._send_raw(msg)
            except Exception:
                pass
        self.disconnect()
        self.cfg.username = ""
    
    def disconnect(self) -> None:
        """断开连接"""
        self._running = False
        self._is_calling = False
        self._recv_thread_started = False
        
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
        
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._set_call_state(CallState.IDLE)
    
    # ========== 联系人管理 ==========
    
    def _request_response(self, msg: str, timeout: float = 5.0) -> tuple[bool, str, dict]:
        """
        发送请求并等待响应。
        recv线程启动后，响应会被放入_response_queue。
        """
        # 清空队列中的旧响应
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except queue.Empty:
                break
        
        self._send_raw(msg)
        
        try:
            response = self._response_queue.get(timeout=timeout)
            return response
        except queue.Empty:
            return False, "等待响应超时", {}
    
    def add_contact(self, contact_name: str) -> tuple[bool, str]:
        """添加联系人"""
        msg = encode_contact_add(self.cfg.username, contact_name)
        success, message, _ = self._request_response(msg)
        if success:
            self._refresh_contacts()
        return success, message
    
    def delete_contact(self, contact_name: str) -> tuple[bool, str]:
        """删除联系人"""
        msg = encode_contact_delete(self.cfg.username, contact_name)
        success, message, _ = self._request_response(msg)
        if success:
            self._refresh_contacts()
        return success, message
    
    def update_contact(self, old_name: str, new_name: str) -> tuple[bool, str]:
        """更新联系人"""
        msg = encode_contact_update(self.cfg.username, old_name, new_name)
        success, message, _ = self._request_response(msg)
        if success:
            self._refresh_contacts()
        return success, message
    
    def get_contacts(self) -> List[str]:
        """获取联系人列表"""
        msg = encode_contact_list(self.cfg.username)
        success, message, data = self._request_response(msg)
        if success:
            self._contacts = data.get("contacts", [])
            return self._contacts
        return []
    
    def search_contacts(self, keyword: str) -> List[str]:
        """搜索联系人"""
        msg = encode_contact_search(self.cfg.username, keyword)
        success, message, data = self._request_response(msg)
        if success:
            return data.get("contacts", [])
        return []
    
    def _refresh_contacts(self) -> None:
        """刷新联系人列表"""
        self.get_contacts()
    
    # ========== 呼叫管理 ==========
    
    def call(self, target: str) -> bool:
        """呼叫对方"""
        if not target or self._call_state != CallState.IDLE:
            return False
        
        msg = encode_call_invite(self.cfg.username, target)
        self._send_raw(msg)
        self._in_call_with = target
        self._set_call_state(CallState.CALLING)
        return True
    
    def accept_call(self, caller: str) -> bool:
        """接听来电"""
        msg = encode_call_accept(caller, self.cfg.username)
        self._send_raw(msg)
        self._start_audio()
        self._in_call_with = caller
        self._set_call_state(CallState.IN_CALL)
        return True
    
    def reject_call(self, caller: str) -> bool:
        """拒绝来电"""
        msg = encode_call_reject(caller, self.cfg.username, "拒绝接听")
        self._send_raw(msg)
        self._in_call_with = ""
        self._set_call_state(CallState.IDLE)
        return True
    
    def hangup(self) -> None:
        """挂断通话"""
        if self._call_state in (CallState.IDLE, CallState.ENDED):
            return
        
        target = self._in_call_with
        if target:
            msg = encode_call_hangup(self.cfg.username, target)
            try:
                self._send_raw(msg)
            except Exception:
                pass
        
        self._stop_audio()
        self._in_call_with = ""
        self._set_call_state(CallState.ENDED)
        
        # 延迟恢复空闲状态
        threading.Timer(1.5, lambda: self._set_call_state(CallState.IDLE)).start()
    
    def _set_call_state(self, state: str) -> None:
        """设置通话状态"""
        self._call_state = state
        if self._on_call_state_change:
            self._on_call_state_change(state, self._in_call_with)
    
    # ========== 音频通话 ==========
    
    def _start_audio(self) -> None:
        """开始音频通话"""
        if not self._p:
            print("[Client] pyaudio未安装，无法进行语音通话")
            return
        
        # 避免重复打开输入设备
        if self._in_stream is not None:
            return
        
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
            return
        
        self._is_calling = True
        self._send_thread = threading.Thread(
            target=self._send_audio_loop, daemon=True
        )
        self._send_thread.start()
    
    def _stop_audio(self) -> None:
        """停止音频通话"""
        self._is_calling = False
        time.sleep(0.1)
        
        try:
            if self._in_stream:
                self._in_stream.stop_stream()
                self._in_stream.close()
        except Exception:
            pass
        self._in_stream = None
    
    def _send_audio_loop(self) -> None:
        """发送音频数据"""
        if not self._in_stream:
            return
        
        while self._is_calling and self._sock:
            try:
                data = self._in_stream.read(CHUNK_SIZE, exception_on_overflow=False)
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
            time.sleep(0.001)
    
    # ========== 网络通信 ==========
    
    def _send_raw(self, msg: str) -> None:
        """发送消息"""
        if not self._sock:
            raise RuntimeError("未连接服务器")
        self._sock.sendall((msg + MESSAGE_DELIMITER).encode("utf-8"))
    
    def _recv_loop(self) -> None:
        """接收消息循环"""
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
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # 检查是否是响应消息（有success字段，无type字段）
                if "success" in obj and "type" not in obj:
                    success = obj.get("success", False)
                    message = obj.get("message", "")
                    data_field = obj.get("data", {})
                    self._response_queue.put((success, message, data_field))
                    continue
                
                # 否则是信令/数据消息
                t = obj.get("type")
                if t:
                    self._handle_message(t, obj)
        
        self._running = False
        self._recv_thread_started = False
        print("[Client] 已从服务器断开")
    
    def _handle_message(self, mtype: str, payload: dict) -> None:
        """处理接收到的消息"""
        if mtype == "call_invite":
            # 收到呼叫请求
            caller = payload.get("caller", "")
            print(f"[Client] 收到来自 {caller} 的呼叫")
            self._in_call_with = caller
            self._set_call_state(CallState.RINGING)
        
        elif mtype == "call_accept":
            # 呼叫被接受——仅在呼叫方（CALLING状态）时处理
            # 被呼叫方已在 accept_call() 中处理，跳过重复的 call_accept
            if self._call_state == CallState.IN_CALL:
                return
            print(f"[Client] 对方接听了电话")
            self._start_audio()
            self._set_call_state(CallState.IN_CALL)
        
        elif mtype == "call_reject":
            # 呼叫被拒绝
            reason = payload.get("reason", "对方拒绝接听")
            print(f"[Client] 呼叫被拒绝: {reason}")
            self._in_call_with = ""
            self._is_calling = False
            self._set_call_state(CallState.ENDED)
            threading.Timer(1.5, lambda: self._set_call_state(CallState.IDLE)).start()
        
        elif mtype == "call_hangup":
            # 对方挂断
            print(f"[Client] 对方挂断通话")
            self._stop_audio()
            self._in_call_with = ""
            self._set_call_state(CallState.ENDED)
            threading.Timer(1.5, lambda: self._set_call_state(CallState.IDLE)).start()
        
        elif mtype == "call_busy":
            # 对方占线
            print(f"[Client] 对方占线")
            self._in_call_with = ""
            self._is_calling = False
            self._set_call_state(CallState.ENDED)
            threading.Timer(1.5, lambda: self._set_call_state(CallState.IDLE)).start()
        
        elif mtype == "call_not_found":
            # 用户不在线
            target = payload.get("target", "")
            print(f"[Client] 用户 {target} 不在线")
            self._in_call_with = ""
            self._is_calling = False
            self._set_call_state(CallState.ENDED)
            threading.Timer(1.5, lambda: self._set_call_state(CallState.IDLE)).start()
        
        elif mtype == "text":
            text = payload.get("content", "")
            if self._on_text:
                self._on_text(text)
        
        elif mtype == "audio_chunk":
            # 播放音频
            b64 = payload.get("data", "")
            try:
                raw = base64.b64decode(b64)
                if self._out_stream:
                    self._out_stream.write(raw, exception_on_underflow=False)
            except Exception:
                pass
