#!/usr/bin/env python3
# gui_tcp_chat_client.py

import os
import sys
import json
import time
import wave
import struct
import socket
import threading

try:
    import pyaudio
except Exception:
    pyaudio = None

from PyQt5.QtCore import Qt, QObject, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QWidget, QMainWindow, QStackedWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QTextBrowser, QFileDialog, QMessageBox,
    QSpinBox, QCheckBox, QGroupBox, QComboBox
)

SERVER = ("10.192.60.12", 65432)
LOGIN_WAIT_TIMEOUT = 5.0

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
    """
    将 raw pcm bytes 写成临时 wav 并播放。
    """
    if not pyaudio:
        return

    fname = f"received_{int(time.time())}.wav"
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
            output=True
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
        frames_per_buffer=frames_per_buffer
    )

    frames = []
    try:
        for _ in range(0, int(rate / frames_per_buffer * seconds)):
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

# ---------- 网络模块 ----------
class NetworkClient(QObject):
    messageReceived = pyqtSignal(str)
    statusReceived = pyqtSignal(str)
    usersReceived = pyqtSignal(list)
    loginSucceeded = pyqtSignal(str)
    loginFailed = pyqtSignal(str)
    disconnected = pyqtSignal(str)

    def __init__(self, server):
        super().__init__()
        self.server = server
        self.conn = None
        self.listener = None
        self.running = False
        self.send_lock = threading.Lock()
        self.pending_auth = None  # {"type": "login"/"register", "username": ...}

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

    #接收端口监听线程
    def _listener_loop(self):
        try:
            while self.running:
                msg = recv_json(self.conn)
                if msg is None:
                    self.disconnected.emit("与服务器的连接已断开")
                    break

                mtype = msg.get("type")
                text = msg.get("text", "")

                # 登录/注册结果识别
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
                    self.messageReceived.emit(f"[私聊] {msg.get('from')}: {msg.get('text')}")
                elif mtype == "broadcast":
                    self.messageReceived.emit(f"[广播] {msg.get('from')}: {msg.get('text')}")
                elif mtype == "list_response":
                    self.usersReceived.emit(msg.get("users", []))
                elif mtype == "info":
                    self.statusReceived.emit(f"[info] {text}")
                elif mtype == "error":
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
                    threading.Thread(
                        target=play_wav_bytes,
                        args=(audio_bytes, channels, sampwidth, framerate),
                        daemon=True
                    ).start()
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
    def __init__(self, net: NetworkClient, on_login_ok):
        super().__init__()
        self.net = net
        self.on_login_ok = on_login_ok

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

class ChatPage(QWidget):
    def __init__(self, net: NetworkClient):
        super().__init__()
        self.net = net
        self.current_user = ""

        self.status = QLabel("未登录")
        self.status.setStyleSheet("font-weight: bold;")

        self.log = QTextBrowser()
        self.log.setPlaceholderText("聊天记录会显示在这里")

        self.input = QLineEdit()
        self.input.setPlaceholderText("输入消息；直接发送为广播")

        self.toUser = QLineEdit()
        self.toUser.setPlaceholderText("私聊目标用户名")

        self.btnSend = QPushButton("发送广播")
        self.btnPrivate = QPushButton("私聊发送")
        self.btnList = QPushButton("刷新在线列表")
        self.btnFile = QPushButton("发送 wav 文件")
        self.btnRecord = QPushButton("录音并发送")
        self.btnQuit = QPushButton("退出")

        self.seconds = QSpinBox()
        self.seconds.setRange(1, 300)
        self.seconds.setValue(5)
        self.seconds.setSuffix(" 秒")

        self.users = QComboBox()
        self.users.setEditable(True)
        self.users.setPlaceholderText("在线用户")

        top = QHBoxLayout()
        top.addWidget(self.status)
        top.addStretch(1)

        send_row = QHBoxLayout()
        send_row.addWidget(self.input)

        private_row = QHBoxLayout()
        private_row.addWidget(QLabel("私聊对象"))
        private_row.addWidget(self.toUser)

        rec_row = QHBoxLayout()
        rec_row.addWidget(QLabel("录音时长"))
        rec_row.addWidget(self.seconds)
        rec_row.addWidget(QLabel("秒"))

        user_row = QHBoxLayout()
        user_row.addWidget(QLabel("在线列表"))
        user_row.addWidget(self.users)

        btn_row1 = QHBoxLayout()
        btn_row1.addWidget(self.btnSend)
        btn_row1.addWidget(self.btnPrivate)
        btn_row1.addWidget(self.btnList)

        btn_row2 = QHBoxLayout()
        btn_row2.addWidget(self.btnFile)
        btn_row2.addWidget(self.btnRecord)
        btn_row2.addWidget(self.btnQuit)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.log)
        layout.addLayout(send_row)
        layout.addLayout(private_row)
        layout.addLayout(user_row)
        layout.addLayout(rec_row)
        layout.addLayout(btn_row1)
        layout.addLayout(btn_row2)

        self.btnSend.clicked.connect(self.send_broadcast)
        self.btnPrivate.clicked.connect(self.send_private)
        self.btnList.clicked.connect(self.request_list)
        self.btnFile.clicked.connect(self.send_file)
        self.btnRecord.clicked.connect(self.record_and_send)
        self.btnQuit.clicked.connect(self.quit_client)
        self.input.returnPressed.connect(self.send_broadcast)

        self.users.currentTextChanged.connect(self.toUser.setText)

    def append_log(self, text):
        self.log.append(text)

    def set_user(self, username):
        self.current_user = username
        self.status.setText(f"已登录: {username}")

    def send_broadcast(self):
        text = self.input.text().strip()
        if not text:
            return
        try:
            self.net.send({"type": "broadcast", "text": text})
            self.append_log(f"[我] {text}")
            self.input.clear()
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def send_private(self):
        to = self.toUser.text().strip()
        text = self.input.text().strip()
        if not to or not text:
            QMessageBox.warning(self, "提示", "请填写私聊对象和内容")
            return
        try:
            self.net.send({"type": "message", "to": to, "text": text})
            self.append_log(f"[我 -> {to}] {text}")
            self.input.clear()
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def request_list(self):
        try:
            self.net.send({"type": "list_request"})
        except Exception as e:
            QMessageBox.critical(self, "请求失败", str(e))

    def send_file(self):
        to = self.toUser.text().strip()
        if not to:
            QMessageBox.warning(self, "提示", "请先填写私聊对象")
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 wav 文件", "", "WAV Files (*.wav)")
        if not path:
            return
        try:
            send_wav_file_over_conn(self.net.conn, path, to)
            self.append_log(f"[我 -> {to}] 发送语音文件: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def record_and_send(self):
        to = self.toUser.text().strip()
        if not to:
            QMessageBox.warning(self, "提示", "请先填写私聊对象")
            return
        seconds = float(self.seconds.value())
        tmpfile = f"tmp_record_{int(time.time())}.wav"

        def worker():
            try:
                record_wav(tmpfile, seconds)
                send_wav_file_over_conn(self.net.conn, tmpfile, to)
                self.append_log(f"[我 -> {to}] 已录音并发送 {seconds} 秒语音")
            except Exception as e:
                QMessageBox.critical(self, "录音/发送失败", str(e))
            finally:
                try:
                    os.remove(tmpfile)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def quit_client(self):
        try:
            self.net.close()
        finally:
            QApplication.quit()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyQt TCP 聊天客户端")
        self.resize(900, 650)

        self.net = NetworkClient(SERVER)

        self.stack = QStackedWidget()
        self.loginPage = LoginPage(self.net, self.on_login_ok)
        self.chatPage = ChatPage(self.net)

        self.stack.addWidget(self.loginPage)
        self.stack.addWidget(self.chatPage)
        self.setCentralWidget(self.stack)

        # signals
        self.net.messageReceived.connect(self.chatPage.append_log)
        self.net.statusReceived.connect(self.chatPage.append_log)
        self.net.usersReceived.connect(self.on_users)
        self.net.loginSucceeded.connect(self.on_login_success)
        self.net.loginFailed.connect(self.on_login_failed)
        self.net.disconnected.connect(self.on_disconnected)

        self.stack.setCurrentWidget(self.loginPage)

    def on_login_ok(self):
        pass

    def on_login_success(self, text):
        username = self.loginPage.username.text().strip()
        self.chatPage.set_user(username)
        self.chatPage.append_log(f"[系统] {text}")
        self.stack.setCurrentWidget(self.chatPage)

    def on_login_failed(self, text):
        QMessageBox.warning(self, "登录/注册失败", text)
        self.chatPage.append_log(f"[系统] {text}")

    def on_users(self, users):
        self.chatPage.users.clear()
        self.chatPage.users.addItems(users)
        self.chatPage.append_log("[在线用户] " + ", ".join(users))

    def on_disconnected(self, text):
        QMessageBox.critical(self, "连接断开", text)
        self.chatPage.append_log(f"[系统] {text}")
        self.stack.setCurrentWidget(self.loginPage)

    def closeEvent(self, event):
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