#!/usr/bin/env python3
# gui_tcp_chat_client_popup_fixed.py

import os
import sys
import json
import time
import wave
import struct
import socket
import threading
import queue
import tempfile
import shutil
import html

try:
    import pyaudio
except Exception:
    pyaudio = None

from PyQt5.QtCore import Qt, QObject, pyqtSignal, QUrl, QTimer
from PyQt5.QtWidgets import (
    QApplication, QWidget, QMainWindow, QStackedWidget, QDialog,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QTextBrowser, QFileDialog, QMessageBox,
    QSpinBox, QCheckBox, QComboBox, QScrollArea, QFrame
)

SERVER = ("10.192.3.86", 65432)
VOICE_RATE = 16000
VOICE_CHANNELS = 1
VOICE_SAMPWIDTH = 2
VOICE_CHUNK = 1024

STATUS_LABELS = {
    "online_free": "online_free在线空闲",
    "calling": "calling通话中",
    "not_online": "not_online离线",
}


# ---------- 密码封装成二进制文件实现自动读取与登录 ----------
def password_pack(nickname: str, password: str):
    b_nickname = nickname.encode("utf-8")
    b_password = password.encode("utf-8")
    with open("password.bin", "wb") as f:
        f.write(struct.pack(">I", len(b_nickname)))
        f.write(b_nickname)
        f.write(struct.pack(">I", len(b_password)))
        f.write(b_password)


def password_unpack():
    if not os.path.exists("password.bin"):
        return None, None
    try:
        with open("password.bin", "rb") as f:
            len_nickname = struct.unpack(">I", f.read(4))[0]
            b_nickname = f.read(len_nickname)
            len_password = struct.unpack(">I", f.read(4))[0]
            b_password = f.read(len_password)
        return b_nickname.decode("utf-8"), b_password.decode("utf-8")
    except Exception:
        return None, None


# ---------- json封装的文本信息的发送与接收 ----------
def send_json(conn, obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    length = struct.pack(">I", len(data))
    conn.sendall(length + data)


def recvall(conn, n):
    buf = b""
    while len(buf) < n:
        try:
            chunk = conn.recv(n - len(buf))
        except Exception:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_json(conn):
    raw_len = recvall(conn, 4)
    if not raw_len:
        return None
    msg_len = struct.unpack(">I", raw_len)[0]
    data = recvall(conn, msg_len)
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {"type": "error", "text": "无法解析服务器返回的 JSON"}


# ---------- 语音模块 ----------
def play_wav_bytes(wav_bytes, channels, sampwidth, framerate):
    if not pyaudio:
        return

    fname = f"received_{int(time.time() * 1000)}.wav"
    wf = wave.open(fname, "wb")
    wf.setnchannels(channels)
    wf.setsampwidth(sampwidth)
    wf.setframerate(framerate)
    wf.writeframes(wav_bytes)
    wf.close()

    try:
        wf = wave.open(fname, "rb")
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pa.get_format_from_width(wf.getsampwidth()),
            channels=wf.getnchannels(),
            rate=wf.getframerate(),
            output=True,
        )
        data = wf.readframes(1024)
        while data:
            stream.write(data)
            data = wf.readframes(1024)
        stream.stop_stream()
        stream.close()
        pa.terminate()
        wf.close()
    finally:
        try:
            os.remove(fname)
        except Exception:
            pass


def play_wav_file(filepath):
    if not pyaudio:
        return
    if not os.path.exists(filepath):
        return
    wf = None
    pa = None
    stream = None
    try:
        wf = wave.open(filepath, "rb")
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pa.get_format_from_width(wf.getsampwidth()),
            channels=wf.getnchannels(),
            rate=wf.getframerate(),
            output=True,
        )
        data = wf.readframes(1024)
        while data:
            stream.write(data)
            data = wf.readframes(1024)
    finally:
        try:
            if stream:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        try:
            if pa:
                pa.terminate()
        except Exception:
            pass
        try:
            if wf:
                wf.close()
        except Exception:
            pass


def safe_name(text, fallback="user"):
    text = (text or "").strip()
    if not text:
        return fallback
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in text)[:48] or fallback


def friend_json_path(username):
    return f"{safe_name(username, 'user')}.json"


def load_friends_from_file(username):
    path = friend_json_path(username)
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        friends = obj.get("friends", []) if isinstance(obj, dict) else []
        if not isinstance(friends, list):
            return set()
        return {str(x).strip() for x in friends if str(x).strip()}
    except Exception:
        return set()


def save_friends_to_file(username, friends):
    path = friend_json_path(username)
    data = {
        "nickname": username,
        "friends": sorted({str(x).strip() for x in friends if str(x).strip()}),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_wav(path, seconds, channels=1, rate=16000, frames_per_buffer=1024):
    if not pyaudio:
        raise RuntimeError("pyaudio 未安装，无法录音")

    pa = pyaudio.PyAudio()
    sample_format = pyaudio.paInt16
    sampwidth = pa.get_sample_size(sample_format)

    stream = pa.open(
        format=sample_format,
        channels=channels,
        rate=rate,
        input=True,
        frames_per_buffer=frames_per_buffer,
    )

    frames = []
    try:
        loops = max(1, int(rate / frames_per_buffer * seconds))
        for _ in range(loops):
            data = stream.read(frames_per_buffer, exception_on_overflow=False)
            frames.append(data)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    wf = wave.open(path, "wb")
    wf.setnchannels(channels)
    wf.setsampwidth(sampwidth)
    wf.setframerate(rate)
    wf.writeframes(b"".join(frames))
    wf.close()
    return sampwidth


def send_wav_file_over_conn(conn, filepath, to_user):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"文件不存在: {filepath}")

    with wave.open(filepath, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    header = {
        "type": "audio",
        "to": to_user,
        "bytes_len": len(raw),
        "format": "wav",
        "channels": channels,
        "sampwidth": sampwidth,
        "framerate": framerate,
    }
    send_json(conn, header)
    conn.sendall(raw)


class PCMPlayer:
    def __init__(self, rate=16000, channels=1, sampwidth=2):
        if not pyaudio:
            raise RuntimeError("pyaudio 未安装，无法播放实时语音")
        self.rate = rate
        self.channels = channels
        self.sampwidth = sampwidth
        self.pa = pyaudio.PyAudio()
        self.stream = self.pa.open(
            format=self.pa.get_format_from_width(sampwidth),
            channels=channels,
            rate=rate,
            output=True,
        )
        self.q = queue.Queue(maxsize=256)
        self.stop_evt = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                data = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if data is None:
                break
            try:
                self.stream.write(data)
            except Exception:
                break

    def submit(self, pcm_bytes):
        if self.stop_evt.is_set():
            return
        try:
            self.q.put_nowait(pcm_bytes)
        except queue.Full:
            try:
                _ = self.q.get_nowait()
            except Exception:
                pass
            try:
                self.q.put_nowait(pcm_bytes)
            except Exception:
                pass

    def close(self):
        if self.stop_evt.is_set():
            return
        self.stop_evt.set()
        try:
            self.q.put_nowait(None)
        except Exception:
            pass
        try:
            self.thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass
        try:
            self.pa.terminate()
        except Exception:
            pass


# ---------- 网络模块 ----------
class NetworkClient(QObject):
    messageReceived = pyqtSignal(str, str)
    broadcastReceived = pyqtSignal(str, str)
    usersReceived = pyqtSignal(list)
    loginSucceeded = pyqtSignal(str)
    loginFailed = pyqtSignal(str)
    disconnected = pyqtSignal(str)
    statusReceived = pyqtSignal(str)
    audioMessageReceived = pyqtSignal(str, bytes, int, int, int)
    voiceFrameReceived = pyqtSignal(str, bytes, int, int, int)
    callInvited = pyqtSignal(str, int, int, int)
    callAccepted = pyqtSignal(str)
    callRejected = pyqtSignal(str)
    callEnded = pyqtSignal(str)
    friendRequestReceived = pyqtSignal(str)
    friendAdded = pyqtSignal(str, str)
    friendRejected = pyqtSignal(str)
    friendListReceived = pyqtSignal(list)
    friendStatusUpdated = pyqtSignal(str, str)

    def __init__(self, server):
        super().__init__()
        self.server = server
        self.conn = None
        self.listener = None
        self.running = False
        self.send_lock = threading.Lock()
        self.pending_auth = None
        self.friend_feature_supported = True

    def connect_server(self):
        self.conn = socket.create_connection(self.server)
        self.running = True
        self.listener = threading.Thread(target=self._listener_loop, daemon=True)
        self.listener.start()

    def close(self):
        self.running = False
        try:
            if self.conn:
                try:
                    send_json(self.conn, {"type": "quit"})
                except Exception:
                    pass
                try:
                    self.conn.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                self.conn.close()
        except Exception:
            pass
        self.conn = None

    def send(self, obj):
        if not self.conn:
            raise RuntimeError("尚未连接服务器")
        with self.send_lock:
            send_json(self.conn, obj)

    def login(self, username, password):
        self.pending_auth = {"type": "login", "username": username}
        self.send({"type": "login", "username": username, "password": password})

    def register(self, username, password):
        self.pending_auth = {"type": "register", "username": username}
        self.send({"type": "register", "username": username, "password": password})

    def send_broadcast(self, text):
        self.send({"type": "broadcast", "text": text})

    def send_private_text(self, to_user, text):
        self.send({"type": "message", "to": to_user, "text": text})

    def request_users(self):
        self.send({"type": "list_request"})

    def request_friend_list(self):
        if not self.friend_feature_supported:
            return
        self.send({"type": "friend_list_request"})

    def send_friend_request(self, to_user):
        if not self.friend_feature_supported:
            raise RuntimeError("当前服务端未开启好友功能")
        self.send({"type": "friend_request", "to": to_user})

    def respond_friend_request(self, to_user, accept):
        if not self.friend_feature_supported:
            raise RuntimeError("当前服务端未开启好友功能")
        self.send({"type": "friend_response", "to": to_user, "accept": bool(accept)})

    def send_audio_file(self, to_user, path):
        with self.send_lock:
            send_wav_file_over_conn(self.conn, path, to_user)

    def record_and_send_audio(self, to_user, seconds, on_done=None):
        tmpfile = f"tmp_record_{int(time.time() * 1000)}.wav"

        def worker():
            try:
                record_wav(tmpfile, seconds)
                self.send_audio_file(to_user, tmpfile)
                if on_done:
                    on_done(True, f"已录音并发送 {seconds} 秒语音")
            except Exception as e:
                if on_done:
                    on_done(False, str(e))
            finally:
                try:
                    os.remove(tmpfile)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def start_voice_call(self, to_user):
        # 先标记为“呼叫中”，避免极端情况下回执先到导致状态不同步
        self.outgoing_call_pending = True
        try:
            self.send({
                "type": "call_start",
                "to": to_user,
                "rate": VOICE_RATE,
                "channels": VOICE_CHANNELS,
                "sampwidth": VOICE_SAMPWIDTH,
            })
        except Exception:
            self.outgoing_call_pending = False
            raise

    def accept_voice_call(self, to_user):
        # 先进入通话态，再通知对方，避免非常快的回执和本地状态打架
        self.call_active = True
        self.incoming_call_pending = False
        self.outgoing_call_pending = False
        try:
            self.send({"type": "call_accept", "to": to_user})
        except Exception:
            self.call_active = False
            raise

    def reject_voice_call(self, to_user):
        self.incoming_call_pending = False
        try:
            self.send({"type": "call_reject", "to": to_user})
        except Exception:
            raise

    def end_voice_call(self, to_user):
        try:
            self.send({"type": "call_end", "to": to_user})
        except Exception:
            raise

    def send_voice_frame(self, to_user, raw_bytes):
        with self.send_lock:
            send_json(self.conn, {
                "type": "audio_frame",
                "to": to_user,
                "bytes_len": len(raw_bytes),
                "rate": VOICE_RATE,
                "channels": VOICE_CHANNELS,
                "sampwidth": VOICE_SAMPWIDTH,
            })
            self.conn.sendall(raw_bytes)

    def _listener_loop(self):
        try:
            while self.running:
                msg = recv_json(self.conn)
                if msg is None:
                    self.disconnected.emit("与服务器的连接已断开")
                    break

                mtype = msg.get("type")
                text = msg.get("text", "")

                if self.pending_auth:
                    if mtype == "error":
                        self.loginFailed.emit(text or "登录/注册失败")
                        self.pending_auth = None
                        continue
                    if mtype == "info" and ("成功" in text or "登录成功" in text or "注册成功" in text):
                        self.loginSucceeded.emit(text or "成功")
                        self.pending_auth = None
                        continue

                if mtype == "message":
                    text_out = msg.get("text", "")
                    if msg.get("offline"):
                        text_out = f"[离线消息] {text_out}"
                    self.messageReceived.emit(msg.get("from", "?"), text_out)
                elif mtype == "broadcast":
                    self.broadcastReceived.emit(msg.get("from", "?"), msg.get("text", ""))
                elif mtype == "list_response":
                    self.usersReceived.emit(msg.get("users", []))
                elif mtype == "friend_list_response":
                    self.friendListReceived.emit(msg.get("friends", []))
                elif mtype == "friend_request":
                    self.friendRequestReceived.emit(msg.get("from", "?"))
                elif mtype == "friend_added":
                    self.friendAdded.emit(msg.get("friend", "?"), msg.get("status", "not_online"))
                elif mtype == "friend_rejected":
                    self.friendRejected.emit(msg.get("from", "?"))
                elif mtype == "friend_status":
                    self.friendStatusUpdated.emit(msg.get("friend", "?"), msg.get("status", "not_online"))
                elif mtype == "info":
                    self.statusReceived.emit(f"[info] {text}")
                elif mtype == "error":
                    lowered = str(text).lower()
                    if "friend_list_request" in lowered or "friend_request" in lowered or "friend_response" in lowered:
                        self.friend_feature_supported = False
                        self.statusReceived.emit("[info] 当前连接的服务端不支持好友协议，已自动关闭好友功能请求")
                    self.statusReceived.emit(f"[error] {text}")
                elif mtype == "audio":
                    from_user = msg.get("from", "?")
                    bytes_len = msg.get("bytes_len", 0)
                    channels = msg.get("channels") or 1
                    sampwidth = msg.get("sampwidth") or 2
                    framerate = msg.get("framerate") or 16000
                    self.statusReceived.emit(f"[audio] 来自 {from_user} 的语音，{bytes_len} 字节")
                    if not isinstance(bytes_len, int) or bytes_len <= 0:
                        self.statusReceived.emit("[audio] 不合法的音频长度")
                        continue
                    audio_bytes = recvall(self.conn, bytes_len)
                    if audio_bytes is None:
                        self.disconnected.emit("接收音频时连接中断")
                        break
                    self.audioMessageReceived.emit(from_user, audio_bytes, channels, sampwidth, framerate)
                elif mtype == "audio_frame":
                    from_user = msg.get("from", "?")
                    bytes_len = msg.get("bytes_len", 0)
                    channels = msg.get("channels") or VOICE_CHANNELS
                    sampwidth = msg.get("sampwidth") or VOICE_SAMPWIDTH
                    framerate = msg.get("rate") or VOICE_RATE
                    if not isinstance(bytes_len, int) or bytes_len <= 0:
                        self.statusReceived.emit("[voice] 不合法的音频帧长度")
                        continue
                    audio_bytes = recvall(self.conn, bytes_len)
                    if audio_bytes is None:
                        self.disconnected.emit("接收实时语音时连接中断")
                        break
                    self.voiceFrameReceived.emit(from_user, audio_bytes, channels, sampwidth, framerate)
                elif mtype == "call_start":
                    self.callInvited.emit(
                        msg.get("from", "?"),
                        int(msg.get("rate", VOICE_RATE)),
                        int(msg.get("channels", VOICE_CHANNELS)),
                        int(msg.get("sampwidth", VOICE_SAMPWIDTH)),
                    )
                elif mtype == "call_accept":
                    self.callAccepted.emit(msg.get("from", "?"))
                elif mtype == "call_reject":
                    self.callRejected.emit(msg.get("from", "?"))
                elif mtype == "call_end":
                    self.callEnded.emit(msg.get("from", "?"))
                else:
                    self.statusReceived.emit(f"[recv] {msg}")
        except Exception as e:
            self.disconnected.emit(f"监听线程异常: {e}")
        finally:
            try:
                if self.conn:
                    self.conn.close()
            except Exception:
                pass
            self.running = False


# ---------- UI ----------
class LoginPage(QWidget):
    def __init__(self, net: NetworkClient):
        super().__init__()
        self.net = net

        self.title = QLabel("TCP 聊天客户端")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 22px; font-weight: bold;")

        self.username = QLineEdit()
        self.username.setPlaceholderText("用户名")

        self.password = QLineEdit()
        self.password.setPlaceholderText("密码")
        self.password.setEchoMode(QLineEdit.Password)

        self.autoSave = QCheckBox("保存到 password.bin")
        self.autoLogin = QCheckBox("启动后自动登录 password.bin 中的账号")

        self.btnLogin = QPushButton("登录")
        self.btnRegister = QPushButton("注册")
        self.btnAuto = QPushButton("尝试自动登录")
        self.btnConnect = QPushButton("连接服务器")

        form = QGridLayout()
        form.addWidget(QLabel("用户名"), 0, 0)
        form.addWidget(self.username, 0, 1)
        form.addWidget(QLabel("密码"), 1, 0)
        form.addWidget(self.password, 1, 1)

        btns = QHBoxLayout()
        btns.addWidget(self.btnConnect)
        btns.addWidget(self.btnLogin)
        btns.addWidget(self.btnRegister)
        btns.addWidget(self.btnAuto)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addLayout(form)
        layout.addWidget(self.autoSave)
        layout.addWidget(self.autoLogin)
        layout.addLayout(btns)

        self.btnConnect.clicked.connect(self.do_connect)
        self.btnLogin.clicked.connect(self.do_login)
        self.btnRegister.clicked.connect(self.do_register)
        self.btnAuto.clicked.connect(self.do_auto_login)

    def do_connect(self):
        try:
            self.net.connect_server()
            QMessageBox.information(self, "连接成功", f"已连接到 {self.net.server[0]}:{self.net.server[1]}")
        except Exception as e:
            QMessageBox.critical(self, "连接失败", str(e))

    def do_login(self):
        u = self.username.text().strip()
        p = self.password.text()
        if not u or not p:
            QMessageBox.warning(self, "提示", "请输入用户名和密码")
            return
        if self.autoSave.isChecked():
            password_pack(u, p)
        try:
            self.net.login(u, p)
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def do_register(self):
        u = self.username.text().strip()
        p = self.password.text()
        if not u or not p:
            QMessageBox.warning(self, "提示", "请输入用户名和密码")
            return
        if self.autoSave.isChecked():
            password_pack(u, p)
        try:
            self.net.register(u, p)
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def do_auto_login(self):
        nickname, password = password_unpack()
        if not nickname:
            QMessageBox.warning(self, "提示", "没有可用的 password.bin")
            return
        self.username.setText(nickname)
        self.password.setText(password)
        try:
            self.net.login(nickname, password)
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))



class PrivateChatDialog(QDialog):
    def __init__(self, net: NetworkClient, peer: str, current_user: str, parent=None):
        super().__init__(parent)
        self.net = net
        self.peer = peer
        self.current_user = current_user

        self.call_active = False
        self.outgoing_call_pending = False
        self.incoming_call_pending = False
        self.call_rate = VOICE_RATE
        self.call_channels = VOICE_CHANNELS
        self.call_sampwidth = VOICE_SAMPWIDTH
        self.mic_thread = None
        self.mic_stop = threading.Event()
        self.tx_lock = threading.Lock()
        self.voice_player = None

        self.cache_dir = tempfile.mkdtemp(prefix="tcp_voice_cache_")
        self.cached_voice_files = []

        self.setWindowTitle(f"私聊 - {peer}")
        self.resize(760, 620)

        self.title = QLabel(f"与 {peer} 的私聊窗口")
        self.title.setStyleSheet("font-size: 18px; font-weight: bold;")

        self.status = QLabel("未通话")
        self.status.setStyleSheet("font-weight: bold;")

        self.feed = QWidget()
        self.feed_layout = QVBoxLayout(self.feed)
        self.feed_layout.setContentsMargins(6, 6, 6, 6)
        self.feed_layout.setSpacing(8)
        self.feed_layout.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.feed)

        self.input = QLineEdit()
        self.input.setPlaceholderText("输入消息")

        self.btnSend = QPushButton("发送消息")
        self.btnFile = QPushButton("发送语音文件")
        self.btnRecord = QPushButton("录音并发送")
        self.btnCall = QPushButton("发起语音通话")
        self.btnHangup = QPushButton("挂断")
        self.btnAccept = QPushButton("接听")
        self.btnReject = QPushButton("拒绝")
        self.btnAccept.setVisible(False)
        self.btnReject.setVisible(False)

        self.seconds = QSpinBox()
        self.seconds.setRange(1, 300)
        self.seconds.setValue(5)
        self.seconds.setSuffix(" 秒")

        self.record_row = QHBoxLayout()
        self.record_row.addWidget(QLabel("录音时长"))
        self.record_row.addWidget(self.seconds)
        self.record_row.addStretch(1)

        self.btn_row1 = QHBoxLayout()
        self.btn_row1.addWidget(self.btnSend)
        self.btn_row1.addWidget(self.btnFile)
        self.btn_row1.addWidget(self.btnRecord)

        self.btn_row2 = QHBoxLayout()
        self.btn_row2.addWidget(self.btnCall)
        self.btn_row2.addWidget(self.btnHangup)
        self.btn_row2.addWidget(self.btnAccept)
        self.btn_row2.addWidget(self.btnReject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.status)
        layout.addWidget(self.scroll)
        layout.addWidget(self.input)
        layout.addLayout(self.record_row)
        layout.addLayout(self.btn_row1)
        layout.addLayout(self.btn_row2)

        self.btnSend.clicked.connect(self.send_private_text)
        self.btnFile.clicked.connect(self.send_audio_file)
        self.btnRecord.clicked.connect(self.record_and_send)
        self.btnCall.clicked.connect(self.start_voice_call)
        self.btnHangup.clicked.connect(self.end_voice_call)
        self.btnAccept.clicked.connect(self.accept_voice_call)
        self.btnReject.clicked.connect(self.reject_voice_call)
        self.input.returnPressed.connect(self.send_private_text)

        self._connect_signals()
        self.append_log("[系统] 私聊窗口已打开")

    def _connect_signals(self):
        self.net.messageReceived.connect(self.on_private_message)
        self.net.audioMessageReceived.connect(self.on_audio_message)
        self.net.voiceFrameReceived.connect(self.on_voice_frame)
        self.net.callAccepted.connect(self.on_call_accepted)
        self.net.callRejected.connect(self.on_call_rejected)
        self.net.callEnded.connect(self.on_call_ended)
        self.net.statusReceived.connect(self.on_status)

    def _disconnect_signals(self):
        for sig, fn in [
            (self.net.messageReceived, self.on_private_message),
            (self.net.audioMessageReceived, self.on_audio_message),
            (self.net.voiceFrameReceived, self.on_voice_frame),
            (self.net.callAccepted, self.on_call_accepted),
            (self.net.callRejected, self.on_call_rejected),
            (self.net.callEnded, self.on_call_ended),
            (self.net.statusReceived, self.on_status),
        ]:
            try:
                sig.disconnect(fn)
            except Exception:
                pass

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _bubble_frame(self, title: str, text: str, bg: str, border: str):
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: bold;")
        body_lbl = QLabel(text)
        body_lbl.setWordWrap(True)
        body_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lay.addWidget(title_lbl)
        lay.addWidget(body_lbl)
        return frame

    def _add_row(self, widget, align="left"):
        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(6)

        if align == "right":
            row_lay.addStretch(1)
            row_lay.addWidget(widget)
        else:
            row_lay.addWidget(widget)
            row_lay.addStretch(1)

        self.feed_layout.insertWidget(self.feed_layout.count() - 1, row)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def append_log(self, text):
        bubble = self._bubble_frame("[系统]", str(text), "#f5f7fb", "#d7def0")
        self._add_row(bubble, align="left")

    def append_html_log(self, html_text):
        # 兼容旧调用：现在全部按普通文本显示，避免富文本链接串扰。
        self.append_log(html_text)

    def _make_text_message(self, title, text, outgoing=False):
        bg = "#e8f3ff" if outgoing else "#f5f7fb"
        border = "#bcd7ff" if outgoing else "#d7def0"
        return self._bubble_frame(title, text, bg, border)

    def _cache_voice_message(self, from_user, audio_bytes, channels, sampwidth, rate):
        safe_from = safe_name(from_user, "peer")
        ts = int(time.time() * 1000)
        index = len(self.cached_voice_files) + 1
        filename = f"{safe_from}_{ts}_{index}.wav"
        path = os.path.join(self.cache_dir, filename)
        wf = wave.open(path, "wb")
        wf.setnchannels(int(channels) if channels else 1)
        wf.setsampwidth(int(sampwidth) if sampwidth else 2)
        wf.setframerate(int(rate) if rate else VOICE_RATE)
        wf.writeframes(audio_bytes)
        wf.close()
        self.cached_voice_files.append(path)
        return path

    def _add_voice_bubble(self, from_user, path):
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background: #fff7e6; border: 1px solid #f0d39d; border-radius: 10px; }"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        title = QLabel(f"[语音消息] 来自 {from_user}，已缓存：{os.path.basename(path)}")
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: bold;")

        btn = QPushButton("点击播放")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _=False, p=path: self._play_cached_voice(p))

        lay.addWidget(title)
        lay.addWidget(btn, alignment=Qt.AlignLeft)

        self._add_row(frame, align="left")

    def _play_cached_voice(self, path):
        if not path:
            QMessageBox.warning(self, "提示", "语音缓存路径无效")
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "提示", "语音缓存文件不存在，可能已被清理")
            return
        self.append_log(f"[系统] 正在播放缓存语音：{os.path.basename(path)}")
        threading.Thread(target=play_wav_file, args=(path,), daemon=True).start()

    def _cleanup_voice_cache(self):
        try:
            shutil.rmtree(self.cache_dir, ignore_errors=True)
        except Exception:
            pass
        self.cached_voice_files = []

    def on_status(self, text):
        self.append_log(text)

    def on_private_message(self, from_user, text):
        if from_user != self.peer:
            return
        bubble = self._make_text_message(f"[{from_user}]", text, outgoing=False)
        self._add_row(bubble, align="left")

    def on_audio_message(self, from_user, audio_bytes, channels, sampwidth, rate):
        if from_user != self.peer:
            return
        try:
            cache_path = self._cache_voice_message(from_user, audio_bytes, channels, sampwidth, rate)
            self._add_voice_bubble(from_user, cache_path)
        except Exception as e:
            self.append_log(f"[语音消息] 缓存失败：{e}")

    def on_voice_frame(self, from_user, audio_bytes, channels, sampwidth, rate):
        if from_user != self.peer:
            return
        if self.voice_player is None:
            try:
                self.voice_player = PCMPlayer(rate=rate, channels=channels, sampwidth=sampwidth)
            except Exception as e:
                self.append_log(f"[语音] 无法创建播放设备: {e}")
                return
        self.voice_player.submit(audio_bytes)

    def on_call_accepted(self, from_user):
        if from_user != self.peer:
            return
        self.append_log(f"[系统] {from_user} 已接听")
        self.call_active = True
        self.outgoing_call_pending = False
        self.incoming_call_pending = False
        self.status.setText(f"通话中：{self.peer}")
        self.btnAccept.setVisible(False)
        self.btnReject.setVisible(False)
        self._start_mic_thread()

    def on_call_rejected(self, from_user):
        if from_user != self.peer:
            return
        self.append_log(f"[系统] {from_user} 已拒绝通话")
        self._stop_call_local(update_remote=False, keep_status=False)
        self.status.setText("对方已拒绝")

    def on_call_ended(self, from_user):
        if from_user != self.peer:
            return
        self.append_log(f"[系统] {from_user} 已挂断")
        self._stop_call_local(update_remote=False, keep_status=False)

    def receive_incoming_call(self, rate, channels, sampwidth):
        if self.call_active or self.outgoing_call_pending or self.incoming_call_pending:
            try:
                self.net.reject_voice_call(self.peer)
            except Exception:
                pass
            return
        self.incoming_call_pending = True
        self.call_rate = rate
        self.call_channels = channels
        self.call_sampwidth = sampwidth
        self.status.setText(f"收到来自 {self.peer} 的语音通话邀请")
        self.append_log(f"[系统] 收到来自 {self.peer} 的语音通话邀请")
        self.btnAccept.setVisible(True)
        self.btnReject.setVisible(True)
        self.raise_()
        self.activateWindow()

    def send_private_text(self):
        text = self.input.text().strip()
        if not text:
            return
        try:
            self.net.send_private_text(self.peer, text)
            bubble = self._make_text_message("[我]", text, outgoing=True)
            self._add_row(bubble, align="right")
            self.input.clear()
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def send_audio_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 wav 文件", "", "WAV Files (*.wav)")
        if not path:
            return
        try:
            self.net.send_audio_file(self.peer, path)
            self.append_log(f"[我] 发送语音文件: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def record_and_send(self):
        seconds = float(self.seconds.value())

        def done(ok, text):
            if ok:
                self.append_log(f"[我] {text}")
            else:
                QMessageBox.critical(self, "录音/发送失败", text)

        try:
            self.net.record_and_send_audio(self.peer, seconds, on_done=done)
            self.append_log(f"[系统] 正在录音 {seconds} 秒...")
        except Exception as e:
            QMessageBox.critical(self, "录音/发送失败", str(e))

    def start_voice_call(self):
        if self.call_active or self.outgoing_call_pending or self.incoming_call_pending:
            QMessageBox.information(self, "提示", "当前已有通话或邀请在进行中")
            return

        parent = self.parent()
        if parent and hasattr(parent, "get_friend_status"):
            peer_status = parent.get_friend_status(self.peer)
            if peer_status == "not_online":
                QMessageBox.information(self, "提示", "对方当前离线，不能发起语音通话")
                return
            if peer_status == "calling":
                QMessageBox.information(self, "提示", "对方当前通话中，暂时不能呼叫")
                return

        try:
            self.net.start_voice_call(self.peer)
            # 若在极端快速网络下已经收到接听回执，这里不要覆盖成“呼叫中”
            if not self.call_active:
                self.outgoing_call_pending = True
                self.status.setText(f"正在呼叫 {self.peer} ...")
            self.append_log(f"[系统] 已向 {self.peer} 发起语音通话")
        except Exception as e:
            self.outgoing_call_pending = False
            QMessageBox.critical(self, "发起通话失败", str(e))

    def accept_voice_call(self):
        try:
            self.net.accept_voice_call(self.peer)
            self.call_active = True
            self.incoming_call_pending = False
            self.outgoing_call_pending = False
            self.status.setText(f"通话中：{self.peer}")
            self.append_log(f"[系统] 已接听 {self.peer} 的通话")
            self.btnAccept.setVisible(False)
            self.btnReject.setVisible(False)
            self._start_mic_thread()
        except Exception as e:
            self.call_active = False
            QMessageBox.critical(self, "接听失败", str(e))

    def reject_voice_call(self):
        try:
            self.net.reject_voice_call(self.peer)
            self.append_log(f"[系统] 已拒绝 {self.peer} 的通话")
            self.incoming_call_pending = False
            self.btnAccept.setVisible(False)
            self.btnReject.setVisible(False)
            self.status.setText("未通话")
        except Exception as e:
            QMessageBox.critical(self, "拒绝失败", str(e))

    def _start_mic_thread(self):
        if self.mic_thread and self.mic_thread.is_alive():
            return
        if not pyaudio:
            self.append_log("[系统] pyaudio 未安装，无法进行实时语音通话")
            return
        self.mic_stop.clear()

        def worker():
            pa = None
            stream = None
            try:
                pa = pyaudio.PyAudio()
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=self.call_channels,
                    rate=self.call_rate,
                    input=True,
                    frames_per_buffer=VOICE_CHUNK,
                )
                while self.call_active and not self.mic_stop.is_set():
                    data = stream.read(VOICE_CHUNK, exception_on_overflow=False)
                    if not data:
                        continue
                    with self.tx_lock:
                        try:
                            self.net.send_voice_frame(self.peer, data)
                        except Exception:
                            break
            except Exception as e:
                self.append_log(f"[系统] 麦克风发送线程异常: {e}")
            finally:
                try:
                    if stream:
                        stream.stop_stream()
                        stream.close()
                except Exception:
                    pass
                try:
                    if pa:
                        pa.terminate()
                except Exception:
                    pass

        self.mic_thread = threading.Thread(target=worker, daemon=True)
        self.mic_thread.start()

    def _stop_call_local(self, update_remote=True, keep_status=False):
        self.call_active = False
        self.outgoing_call_pending = False
        self.incoming_call_pending = False
        self.mic_stop.set()
        if self.mic_thread and self.mic_thread.is_alive():
            try:
                self.mic_thread.join(timeout=1.0)
            except Exception:
                pass
        self.mic_thread = None
        if self.voice_player:
            try:
                self.voice_player.close()
            except Exception:
                pass
            self.voice_player = None
        self.btnAccept.setVisible(False)
        self.btnReject.setVisible(False)
        if not keep_status:
            self.status.setText("未通话")
        if update_remote:
            try:
                self.net.end_voice_call(self.peer)
            except Exception:
                pass

    def end_voice_call(self):
        if not (self.call_active or self.outgoing_call_pending or self.incoming_call_pending):
            self.append_log("[系统] 当前没有通话")
            return
        try:
            self._stop_call_local(update_remote=True)
            self.append_log(f"[系统] 已挂断与 {self.peer} 的通话")
        except Exception as e:
            QMessageBox.critical(self, "挂断失败", str(e))

    def closeEvent(self, event):
        try:
            self._stop_call_local(update_remote=True)
        except Exception:
            pass
        try:
            self._cleanup_voice_cache()
        except Exception:
            pass
        self._disconnect_signals()
        event.accept()


class MainPage(QWidget):
    def __init__(self, net: NetworkClient, get_current_user_func):
        super().__init__()
        self.net = net
        self.get_current_user = get_current_user_func
        self.private_windows = {}
        self.current_username = ""
        self.friends_local = set()
        self.friend_status = {}

        self.status = QLabel("未登录")
        self.status.setStyleSheet("font-weight: bold;")

        self.log = QTextBrowser()
        self.log.setPlaceholderText("广播消息、在线提示和系统提示会显示在这里")

        self.input = QLineEdit()
        self.input.setPlaceholderText("输入广播内容")

        self.friendInput = QLineEdit()
        self.friendInput.setPlaceholderText("输入用户名后点击添加好友")

        self.users = QComboBox()
        self.users.setEditable(True)
        self.users.setPlaceholderText("好友列表")

        self.btnSend = QPushButton("发送广播")
        self.btnAddFriend = QPushButton("添加好友")
        self.btnList = QPushButton("刷新在线列表")
        self.btnOpenPrivate = QPushButton("打开私聊窗口")
        self.btnQuit = QPushButton("退出")

        top = QHBoxLayout()
        top.addWidget(self.status)
        top.addStretch(1)

        user_row = QHBoxLayout()
        user_row.addWidget(QLabel("好友对象"))
        user_row.addWidget(self.users)
        user_row.addWidget(self.btnOpenPrivate)

        send_row = QHBoxLayout()
        send_row.addWidget(self.input)

        friend_row = QHBoxLayout()
        friend_row.addWidget(QLabel("添加好友"))
        friend_row.addWidget(self.friendInput)
        friend_row.addWidget(self.btnAddFriend)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btnSend)
        btn_row.addWidget(self.btnList)
        btn_row.addWidget(self.btnQuit)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.log)
        layout.addLayout(send_row)
        layout.addLayout(friend_row)
        layout.addLayout(user_row)
        layout.addLayout(btn_row)

        self.btnSend.clicked.connect(self.send_broadcast)
        self.btnAddFriend.clicked.connect(self.send_friend_apply)
        self.btnList.clicked.connect(self.request_list)
        self.btnOpenPrivate.clicked.connect(self.open_private_chat)
        self.btnQuit.clicked.connect(self.quit_client)
        self.input.returnPressed.connect(self.send_broadcast)
        self.friendInput.returnPressed.connect(self.send_friend_apply)

        self.net.broadcastReceived.connect(self.on_broadcast)
        self.net.usersReceived.connect(self.on_users)
        self.net.statusReceived.connect(self.append_log)
        self.net.disconnected.connect(self.on_disconnected)
        self.net.callInvited.connect(self.on_call_invited)
        self.net.callAccepted.connect(self.on_call_accepted)
        self.net.callRejected.connect(self.on_call_rejected)
        self.net.callEnded.connect(self.on_call_ended)
        self.net.messageReceived.connect(self.on_private_message_preview)
        self.net.audioMessageReceived.connect(self.on_audio_message_preview)
        self.net.friendRequestReceived.connect(self.on_friend_request)
        self.net.friendAdded.connect(self.on_friend_added)
        self.net.friendRejected.connect(self.on_friend_rejected)
        self.net.friendListReceived.connect(self.on_friend_list)
        self.net.friendStatusUpdated.connect(self.on_friend_status)

    def append_log(self, text):
        # 默认按纯文本写入，避免普通消息被 QTextBrowser 解释成 HTML。
        self.log.append(html.escape(str(text)))

    def append_html_log(self, html_text):
        self.log.append(html_text)

    def set_user(self, username):
        self.current_username = username
        self.friends_local = load_friends_from_file(username)
        self.friend_status = {u: "not_online" for u in self.friends_local}
        self._refresh_friend_combo()
        self.status.setText(f"已登录: {username}")
        try:
            self.net.request_friend_list()
        except Exception as e:
            self.append_log(f"[系统] 拉取好友列表失败: {e}")

    def _save_friends_local(self):
        if not self.current_username:
            return
        try:
            save_friends_to_file(self.current_username, self.friends_local)
        except Exception as e:
            self.append_log(f"[系统] 保存好友列表失败: {e}")

    def _selected_peer(self):
        idx = self.users.currentIndex()
        if idx >= 0:
            data = self.users.itemData(idx)
            if isinstance(data, str) and data.strip():
                return data.strip()
        text = self.users.currentText().strip()
        if text.endswith("]") and " [" in text:
            text = text.rsplit(" [", 1)[0].strip()
        return text

    def _refresh_friend_combo(self):
        current = self._selected_peer()
        self.users.blockSignals(True)
        self.users.clear()
        for friend in sorted(self.friends_local):
            status = self.friend_status.get(friend, "not_online")
            label = STATUS_LABELS.get(status, status)
            self.users.addItem(f"{friend} [{label}]", friend)
        if current in self.friends_local:
            idx = self.users.findData(current)
            if idx >= 0:
                self.users.setCurrentIndex(idx)
        self.users.blockSignals(False)

    def get_friend_status(self, friend):
        return self.friend_status.get(friend, "not_online")

    def send_broadcast(self):
        text = self.input.text().strip()
        if not text:
            return

        if text.startswith("#sym:"):
            target = text[5:].strip()
            if not target:
                QMessageBox.warning(self, "提示", "请按 #sym:用户名 的格式填写好友申请")
                return
            if target == self.get_current_user():
                QMessageBox.warning(self, "提示", "不能添加自己为好友")
                return
            try:
                self.net.send_friend_request(target)
                self.append_log(f"[系统] 已发送好友申请给 {target}")
                self.input.clear()
            except Exception as e:
                QMessageBox.critical(self, "发送失败", str(e))
            return

        try:
            self.net.send_broadcast(text)
            self.append_log(f"[我] {text}")
            self.input.clear()
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def send_friend_apply(self):
        target = self.friendInput.text().strip()
        if not target:
            QMessageBox.warning(self, "提示", "请输入要添加的用户名")
            return
        if target == self.get_current_user():
            QMessageBox.warning(self, "提示", "不能添加自己为好友")
            return
        try:
            self.net.send_friend_request(target)
            self.append_log(f"[系统] 已发送好友申请给 {target}")
            self.friendInput.clear()
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def request_list(self):
        try:
            self.net.request_users()
            if self.net.friend_feature_supported:
                self.net.request_friend_list()
        except Exception as e:
            QMessageBox.critical(self, "请求失败", str(e))

    def on_broadcast(self, from_user, text):
        self.append_log(f"[广播] {from_user}: {text}")

    def on_private_message_preview(self, from_user, text):
        if from_user not in self.friends_local:
            self.append_log(f"[系统] 收到来自非好友 {from_user} 的消息，已忽略")
            return
        if from_user not in self.private_windows:
            self.append_log(f"[私聊提醒] {from_user}: {text}")

    def on_audio_message_preview(self, from_user, audio_bytes, channels, sampwidth, rate):
        if from_user not in self.friends_local:
            return
        if from_user in self.private_windows:
            return
        self.append_log(f"[语音提醒] 收到来自 {from_user} 的语音消息")
        win = self.show_private_window(from_user)
        if win:
            win.on_audio_message(from_user, audio_bytes, channels, sampwidth, rate)
            try:
                win.raise_()
                win.activateWindow()
            except Exception:
                pass

    def on_users(self, users):
        self.append_log("[在线用户] " + (", ".join(users) if users else "无"))

    def on_friend_list(self, friends):
        new_friends = set()
        new_status = {}
        for item in friends:
            if not isinstance(item, dict):
                continue
            uname = str(item.get("username", "")).strip()
            if not uname or uname == self.get_current_user():
                continue
            st = str(item.get("status", "not_online")).strip() or "not_online"
            new_friends.add(uname)
            new_status[uname] = st

        self.friends_local = new_friends
        self.friend_status = new_status
        self._save_friends_local()
        self._refresh_friend_combo()

    def on_friend_request(self, from_user):
        if not from_user or from_user == self.get_current_user():
            return
        ret = QMessageBox.question(
            self,
            "好友申请",
            f"{from_user} 想添加你为好友，是否同意？",
            QMessageBox.Yes | QMessageBox.No,
        )
        accept = (ret == QMessageBox.Yes)
        try:
            self.net.respond_friend_request(from_user, accept)
            if accept:
                self.append_log(f"[系统] 你已同意 {from_user} 的好友申请")
            else:
                self.append_log(f"[系统] 你已拒绝 {from_user} 的好友申请")
        except Exception as e:
            QMessageBox.critical(self, "处理好友申请失败", str(e))

    def on_friend_added(self, friend, status):
        friend = (friend or "").strip()
        if not friend or friend == self.get_current_user():
            return
        self.friends_local.add(friend)
        self.friend_status[friend] = status or "not_online"
        self._save_friends_local()
        self._refresh_friend_combo()
        self.append_log(f"[系统] 你与 {friend} 已成为好友")

    def on_friend_rejected(self, from_user):
        if from_user:
            self.append_log(f"[系统] {from_user} 拒绝了你的好友申请")

    def on_friend_status(self, friend, status):
        friend = (friend or "").strip()
        if friend not in self.friends_local:
            return
        self.friend_status[friend] = status or "not_online"
        self._refresh_friend_combo()

    def open_private_chat(self):
        peer = self._selected_peer()
        if not peer:
            QMessageBox.warning(self, "提示", "请先选择一个私聊对象")
            return
        if peer == self.get_current_user():
            QMessageBox.warning(self, "提示", "不能和自己私聊")
            return
        if peer not in self.friends_local:
            QMessageBox.warning(self, "提示", "对方不是你的好友，不能私聊")
            return
        self.show_private_window(peer)

    def show_private_window(self, peer):
        if peer in self.private_windows:
            win = self.private_windows[peer]
            win.raise_()
            win.activateWindow()
            win.show()
            return win

        win = PrivateChatDialog(self.net, peer, self.get_current_user(), self)
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        win.destroyed.connect(lambda *_: self.private_windows.pop(peer, None))
        self.private_windows[peer] = win
        win.show()
        return win

    def on_call_invited(self, from_user, rate, channels, sampwidth):
        if from_user not in self.friends_local:
            try:
                self.net.reject_voice_call(from_user)
            except Exception:
                pass
            return
        win = self.show_private_window(from_user)
        if win:
            win.receive_incoming_call(rate, channels, sampwidth)

    def on_call_accepted(self, from_user):
        if from_user in self.private_windows:
            self.private_windows[from_user].append_log(f"[系统] {from_user} 已接听")

    def on_call_rejected(self, from_user):
        if from_user in self.private_windows:
            self.private_windows[from_user].append_log(f"[系统] {from_user} 已拒绝通话")

    def on_call_ended(self, from_user):
        if from_user in self.private_windows:
            self.private_windows[from_user].append_log(f"[系统] {from_user} 已挂断")
            try:
                self.private_windows[from_user]._stop_call_local(update_remote=False, keep_status=False)
            except Exception:
                pass

    def on_disconnected(self, text):
        QMessageBox.critical(self, "连接断开", text)
        self.append_log(f"[系统] {text}")

    def quit_client(self):
        try:
            for win in list(self.private_windows.values()):
                try:
                    win.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.net.close()
        finally:
            QApplication.quit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyQt TCP 聊天客户端")
        self.resize(980, 700)

        self.net = NetworkClient(SERVER)
        self.current_username = ""

        self.stack = QStackedWidget()
        self.loginPage = LoginPage(self.net)
        self.mainPage = MainPage(self.net, self.get_current_user)

        self.stack.addWidget(self.loginPage)
        self.stack.addWidget(self.mainPage)
        self.setCentralWidget(self.stack)

        self.net.loginSucceeded.connect(self.on_login_success)
        self.net.loginFailed.connect(self.on_login_failed)
        self.net.disconnected.connect(self.on_disconnected)

        self.stack.setCurrentWidget(self.loginPage)

    def get_current_user(self):
        return self.current_username

    def on_login_success(self, text):
        username = self.loginPage.username.text().strip()
        self.current_username = username
        self.mainPage.set_user(username)
        self.mainPage.append_log(f"[系统] {text}")
        self.stack.setCurrentWidget(self.mainPage)
        self.mainPage.request_list()

    def on_login_failed(self, text):
        QMessageBox.warning(self, "登录/注册失败", text)
        self.mainPage.append_log(f"[系统] {text}")

    def on_disconnected(self, text):
        QMessageBox.critical(self, "连接断开", text)
        self.mainPage.append_log(f"[系统] {text}")
        self.stack.setCurrentWidget(self.loginPage)
        self.current_username = ""

    def closeEvent(self, event):
        try:
            for win in list(self.mainPage.private_windows.values()):
                try:
                    win.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.net.close()
        except Exception:
            pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
