#!/usr/bin/env python3
import sys
import os
import time
import json
import wave
import queue
import struct
import threading
import ipaddress
import socket
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *

from stable_merged_client_dualmode import *

class LoginPage(QWidget):
    def __init__(self, net):
        super().__init__()
        self.net = net

        self.title = QLabel("P2P / TCP 语音聊天客户端")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 24px; font-weight: bold; color: #1e3a8a;")

        self.username = QLineEdit()
        self.username.setPlaceholderText("用户名")

        self.password = QLineEdit()
        self.password.setPlaceholderText("密码")
        self.password.setEchoMode(QLineEdit.Password)

        self.autoSave = QCheckBox("保存到 password.bin（多账号）")
        self.savedUsers = QComboBox()
        self.savedUsers.setPlaceholderText("选择已保存账号")

        self.btnLogin = QPushButton("登录")
        self.btnRegister = QPushButton("注册")
        self.btnAuto = QPushButton("尝试自动登录")
        self.btnRefreshSaved = QPushButton("刷新")
        self.btnDeleteSaved = QPushButton("删除账号")

        form = QGridLayout()
        form.addWidget(QLabel("用户名"), 0, 0)
        form.addWidget(self.username, 0, 1)
        form.addWidget(QLabel("密码"), 1, 0)
        form.addWidget(self.password, 1, 1)

        btns = QHBoxLayout()
        btns.addWidget(self.btnLogin)
        btns.addWidget(self.btnRegister)
        btns.addWidget(self.btnAuto)

        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel("已保存账号"))
        saved_row.addWidget(self.savedUsers)
        saved_row.addWidget(self.btnRefreshSaved)
        saved_row.addWidget(self.btnDeleteSaved)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addLayout(form)
        layout.addWidget(self.autoSave)
        layout.addLayout(saved_row)
        layout.addLayout(btns)

        self.btnLogin.clicked.connect(self.do_login)
        self.btnRegister.clicked.connect(self.do_register)
        self.btnAuto.clicked.connect(self.do_auto_login)
        self.btnRefreshSaved.clicked.connect(self.refresh_saved_accounts)
        self.btnDeleteSaved.clicked.connect(self.delete_saved_account)

        self.refresh_saved_accounts()

    def refresh_saved_accounts(self):
        current_user = self.savedUsers.currentData()
        self.savedUsers.blockSignals(True)
        self.savedUsers.clear()
        for acc in load_saved_accounts():
            nickname = acc.get("nickname", "")
            password = acc.get("password", "")
            if nickname:
                self.savedUsers.addItem(nickname, (nickname, password))
        if current_user is not None:
            for i in range(self.savedUsers.count()):
                if self.savedUsers.itemData(i) == current_user:
                    self.savedUsers.setCurrentIndex(i)
                    break
        self.savedUsers.blockSignals(False)

    def delete_saved_account(self):
        item = self.savedUsers.currentData()
        if not item:
            QMessageBox.warning(self, "提示", "请选择要删除的账号")
            return
        nickname, _ = item
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除账号 '{nickname}' 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            accounts = [a for a in load_saved_accounts() if a.get("nickname") != nickname]
            save_saved_accounts(accounts)
            self.refresh_saved_accounts()

    def do_login(self):
        u = self.username.text().strip()
        p = self.password.text()
        if not u or not p:
            QMessageBox.warning(self, "提示", "请输入用户名和密码")
            return
        if self.autoSave.isChecked():
            password_pack(u, p)
            self.refresh_saved_accounts()
        try:
            if not self.net.conn:
                self.net.connect_server()
            self.net.login(u, p)
        except Exception as e:
            QMessageBox.critical(self, "连接/发送失败", str(e))

    def do_register(self):
        u = self.username.text().strip()
        p = self.password.text()
        if not u or not p:
            QMessageBox.warning(self, "提示", "请输入用户名和密码")
            return
        if self.autoSave.isChecked():
            password_pack(u, p)
            self.refresh_saved_accounts()
        try:
            if not self.net.conn:
                self.net.connect_server()
            self.net.register(u, p)
        except Exception as e:
            QMessageBox.critical(self, "连接/发送失败", str(e))

    def do_auto_login(self):
        item = self.savedUsers.currentData()
        if item:
            nickname, password = item
        else:
            nickname, password = password_unpack()
            if not nickname:
                QMessageBox.warning(self, "提示", "没有可用的已保存账号")
                return

        self.username.setText(nickname)
        self.password.setText(password)
        try:
            if not self.net.conn:
                self.net.connect_server()
            self.net.login(nickname, password)
        except Exception as e:
            QMessageBox.critical(self, "连接/发送失败", str(e))


class PrivateChatDialog(QDialog):
    def __init__(self, net, peer, current_user, parent=None):
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
        self.call_mode = "tcp"
        self.incoming_p2p_ip = ""
        self.incoming_p2p_port = 0

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

    def _main_page_owner(self):
        # Embedded mode may change direct parent to QStackedWidget.
        owner = getattr(self, "main_page_owner", None)
        if owner is not None:
            return owner
        p = self.parentWidget()
        while p is not None:
            if hasattr(p, "resolve_call_mode") and hasattr(p, "get_friend_status"):
                return p
            p = p.parentWidget()
        return None

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _bubble_frame(self, title, text, bg, border):
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        t = QLabel(title)
        t.setStyleSheet("font-weight: bold;")
        b = QLabel(text)
        b.setWordWrap(True)
        b.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lay.addWidget(t)
        lay.addWidget(b)
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
        self._add_row(self._bubble_frame("[系统]", str(text), "#f5f7fb", "#d7def0"), align="left")

    def _make_text_message(self, title, text, outgoing=False):
        bg = "#e8f3ff" if outgoing else "#f5f7fb"
        border = "#bcd7ff" if outgoing else "#d7def0"
        return self._bubble_frame(title, text, bg, border)

    def _cache_voice_message(self, from_user, audio_bytes, channels, sampwidth, rate):
        safe_from = safe_name(from_user, "peer")
        path = os.path.join(self.cache_dir, f"{safe_from}_{int(time.time() * 1000)}.wav")
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
        frame.setStyleSheet("QFrame { background: #fff7e6; border: 1px solid #f0d39d; border-radius: 10px; }")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        title = QLabel(f"[语音消息] 来自 {from_user}，已缓存：{os.path.basename(path)}")
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: bold;")

        btn = QPushButton("点击播放")
        btn.clicked.connect(lambda _=False, p=path: self._play_cached_voice(p))

        lay.addWidget(title)
        lay.addWidget(btn, alignment=Qt.AlignLeft)
        self._add_row(frame, align="left")

    def _play_cached_voice(self, path):
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "提示", "语音缓存文件不存在")
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
        if text.startswith("[info] 已发送给 "):
            return
        self.append_log(text)

    def on_private_message(self, from_user, text):
        if from_user != self.peer:
            return
        self._add_row(self._make_text_message(f"[{from_user}]", text, outgoing=False), align="left")

    def on_audio_message(self, from_user, audio_bytes, channels, sampwidth, rate):
        if from_user != self.peer:
            return
        try:
            p = self._cache_voice_message(from_user, audio_bytes, channels, sampwidth, rate)
            self._add_voice_bubble(from_user, p)
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

    def receive_incoming_call(self, rate, channels, sampwidth, mode, p2p_peer_ip, p2p_peer_port):
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
        self.call_mode = mode if mode in ("tcp", "p2p") else "tcp"
        self.incoming_p2p_ip = p2p_peer_ip or ""
        self.incoming_p2p_port = int(p2p_peer_port or 0)

        self.status.setText(f"收到来自 {self.peer} 的语音通话邀请（{self.call_mode.upper()}）")
        self.append_log(f"[系统] 收到来自 {self.peer} 的语音通话邀请（{self.call_mode.upper()}）")
        self.btnAccept.setVisible(True)
        self.btnReject.setVisible(True)
        owner = self._main_page_owner()
        if owner and hasattr(owner, "_focus_session"):
            owner._focus_session(self.peer)

    def on_call_accepted(self, from_user, mode, p2p_peer_ip, p2p_peer_port):
        if from_user != self.peer:
            return

        self.call_mode = mode if mode in ("tcp", "p2p") else "tcp"
        if self.call_mode == "p2p":
            try:
                self.net.open_p2p_to_peer(self.peer, p2p_peer_ip, p2p_peer_port)
            except Exception as e:
                self.append_log(f"[系统] P2P 建连失败，已回退 TCP：{e}")
                self.call_mode = "tcp"

        self.append_log(f"[系统] {from_user} 已接听（{self.call_mode.upper()}）")
        self.call_active = True
        self.outgoing_call_pending = False
        self.incoming_call_pending = False
        self.status.setText(f"通话中：{self.peer} ({self.call_mode.upper()})")
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

    def send_private_text(self):
        text = self.input.text().strip()
        if not text:
            return
        try:
            self.net.send_private_text(self.peer, text)
            self._add_row(self._make_text_message("[我]", text, outgoing=True), align="right")
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

        owner = self._main_page_owner()
        if owner and hasattr(owner, "get_friend_status"):
            st = owner.get_friend_status(self.peer)
            if st == "not_online":
                QMessageBox.information(self, "提示", "对方当前离线，不能发起语音通话")
                return
            if st == "calling":
                QMessageBox.information(self, "提示", "对方当前通话中，暂时不能呼叫")
                return

        mode = "tcp"
        if owner and hasattr(owner, "resolve_call_mode"):
            mode = owner.resolve_call_mode(self.peer)

        try:
            self.call_mode = mode
            self.net.start_voice_call(self.peer, mode)
            self.outgoing_call_pending = True
            self.status.setText(f"正在呼叫 {self.peer} ... ({mode.upper()})")
            self.append_log(f"[系统] 已向 {self.peer} 发起语音通话（{mode.upper()}）")
        except Exception as e:
            self.outgoing_call_pending = False
            QMessageBox.critical(self, "发起通话失败", str(e))

    def accept_voice_call(self):
        try:
            self.net.accept_voice_call(self.peer)
            self.call_active = True
            self.incoming_call_pending = False
            self.outgoing_call_pending = False
            self.status.setText(f"通话中：{self.peer} ({self.call_mode.upper()})")
            self.append_log(f"[系统] 已接听 {self.peer} 的通话（{self.call_mode.upper()}）")
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
                        self.net.send_voice_frame(self.peer, data, mode=self.call_mode)
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
        self._stop_call_local(update_remote=True)
        self.append_log(f"[系统] 已挂断与 {self.peer} 的通话")

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
    def __init__(self, net, get_current_user_func):
        super().__init__()
        self.net = net
        self.get_current_user = get_current_user_func

        self.private_windows = {}
        self.current_username = ""
        self.friends_local = set()
        self.friend_status = {}

        self.group_voice_player = {}
        self.group_send_thread = None
        self.group_send_stop = threading.Event()
        self.group_call_active = False
        self.group_call_initiator = False

        self.status = QLabel("未登录")
        self.status.setStyleSheet("font-weight: bold; color: #1f2937;")

        self.log = QTextBrowser()
        self.log.setPlaceholderText("广播消息、在线提示和系统提示会显示在这里")

        self.input = QLineEdit()
        self.input.setPlaceholderText("输入广播内容")

        self.friendInput = QLineEdit()
        self.friendInput.setPlaceholderText("输入用户名后点击添加好友")

        self.users = QComboBox()
        self.users.setEditable(True)
        self.users.setPlaceholderText("好友列表")

        self.callModeBox = QComboBox()
        self.callModeBox.addItem("TCP 中转", "tcp")
        self.callModeBox.addItem("P2P 直连", "p2p")
        self.autoCallModeCheck = QCheckBox("启用同子网自动模式（同网段=P2P，异网段=TCP）")
        self.autoCallModeCheck.setChecked(True)

        self.btnSend = QPushButton("发送广播")
        self.btnAddFriend = QPushButton("添加好友")
        self.btnList = QPushButton("刷新在线列表")
        self.btnOpenPrivate = QPushButton("打开私聊窗口")
        self.btnGroupStart = QPushButton("发起组播语音")
        self.btnGroupEnd = QPushButton("结束组播语音（仅发起者）")
        self.btnQuit = QPushButton("退出")

        self.sessionList = QListWidget()
        self.sessionList.setMinimumWidth(190)
        self.sessionList.currentTextChanged.connect(self._on_session_selected)

        self.session_broadcast_name = "广播大厅"
        self.sessionList.addItem(self.session_broadcast_name)

        self.pageStack = QStackedWidget()
        self.broadcastPage = QWidget()

        top = QHBoxLayout()
        top.addWidget(self.status)
        top.addStretch(1)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("通话模式参数"))
        mode_row.addWidget(self.callModeBox)
        mode_row.addWidget(self.autoCallModeCheck)

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

        group_row = QHBoxLayout()
        group_row.addWidget(self.btnGroupStart)
        group_row.addWidget(self.btnGroupEnd)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btnSend)
        btn_row.addWidget(self.btnList)
        btn_row.addWidget(self.btnQuit)

        broadcast_layout = QVBoxLayout(self.broadcastPage)
        broadcast_layout.addLayout(top)
        broadcast_layout.addLayout(mode_row)
        broadcast_layout.addWidget(self.log)
        broadcast_layout.addLayout(send_row)
        broadcast_layout.addLayout(friend_row)
        broadcast_layout.addLayout(user_row)
        broadcast_layout.addLayout(group_row)
        broadcast_layout.addLayout(btn_row)

        self.pageStack.addWidget(self.broadcastPage)

        layout = QHBoxLayout(self)
        layout.addWidget(self.sessionList, 1)
        layout.addWidget(self.pageStack, 4)

        self.btnSend.clicked.connect(self.send_broadcast)
        self.btnAddFriend.clicked.connect(self.send_friend_apply)
        self.btnList.clicked.connect(self.request_list)
        self.btnOpenPrivate.clicked.connect(self.open_private_chat)
        self.btnGroupStart.clicked.connect(self.start_group_call)
        self.btnGroupEnd.clicked.connect(self.end_group_call)
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

        self.net.groupCallInvite.connect(self.on_group_call_invite)
        self.net.groupCallJoinOk.connect(self.on_group_call_join_ok)
        self.net.groupCallEnded.connect(self.on_group_call_ended)
        self.net.groupVoiceFrameReceived.connect(self.on_group_voice_frame)

        self._focus_session(self.session_broadcast_name)

    def _on_session_selected(self, name):
        name = (name or "").strip()
        if not name or name == self.session_broadcast_name:
            self.pageStack.setCurrentWidget(self.broadcastPage)
            return
        win = self.private_windows.get(name)
        if win is None:
            win = self.show_private_window(name)
        if win is not None:
            self.pageStack.setCurrentWidget(win)

    def _focus_session(self, name):
        name = (name or "").strip()
        if not name:
            return
        for i in range(self.sessionList.count()):
            if self.sessionList.item(i).text() == name:
                self.sessionList.setCurrentRow(i)
                return

    def _ensure_session_item(self, peer):
        peer = (peer or "").strip()
        if not peer or peer == self.session_broadcast_name:
            return
        for i in range(self.sessionList.count()):
            if self.sessionList.item(i).text() == peer:
                return
        self.sessionList.addItem(peer)

    def _remove_session_item(self, peer):
        peer = (peer or "").strip()
        if not peer:
            return
        win = self.private_windows.pop(peer, None)
        if win is not None:
            try:
                self.pageStack.removeWidget(win)
            except Exception:
                pass
        for i in range(self.sessionList.count()):
            if self.sessionList.item(i).text() == peer:
                self.sessionList.takeItem(i)
                break
        self._focus_session(self.session_broadcast_name)

    def append_log(self, text):
        self.log.append(html.escape(str(text)))

    def set_user(self, username):
        self.current_username = username
        self.friends_local = load_friends_from_file(username)
        self.friend_status = {u: "not_online" for u in self.friends_local}
        self._refresh_friend_combo()
        self.status.setText(f"已登录: {username} | 本地P2P监听端口: {self.net.p2p_port}")
        self.net.current_user = username
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
        for f in sorted(self.friends_local):
            st = self.friend_status.get(f, "not_online")
            label = STATUS_LABELS.get(st, st)
            self.users.addItem(f"{f} [{label}]", f)
        if current in self.friends_local:
            idx = self.users.findData(current)
            if idx >= 0:
                self.users.setCurrentIndex(idx)
        self.users.blockSignals(False)

    def get_friend_status(self, friend):
        return self.friend_status.get(friend, "not_online")

    def resolve_call_mode(self, _peer):
        if self.autoCallModeCheck.isChecked():
            return "auto"
        return self.callModeBox.currentData() or "tcp"

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
        if from_user == self.get_current_user():
            return
        self.append_log(f"[广播] {from_user}: {text}")

    def on_private_message_preview(self, from_user, text):
        if from_user not in self.friends_local:
            self.append_log(f"[系统] 收到来自非好友 {from_user} 的消息，已忽略")
            return
        if from_user not in self.private_windows:
            self.append_log(f"[私聊提醒] {from_user}: {text}")
        self._ensure_session_item(from_user)

    def on_audio_message_preview(self, from_user, audio_bytes, channels, sampwidth, rate):
        if from_user not in self.friends_local:
            return
        if from_user in self.private_windows:
            self._focus_session(from_user)
            return
        self._ensure_session_item(from_user)
        self.append_log(f"[语音提醒] 收到来自 {from_user} 的语音消息")
        win = self.show_private_window(from_user)
        if win:
            win.on_audio_message(from_user, audio_bytes, channels, sampwidth, rate)
            self._focus_session(from_user)

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
        accept = ret == QMessageBox.Yes
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
            self._focus_session(peer)
            self.pageStack.setCurrentWidget(win)
            return win

        win = PrivateChatDialog(self.net, peer, self.get_current_user(), self)
        win.main_page_owner = self
        win.setWindowFlags(Qt.Widget)
        self.private_windows[peer] = win
        self._ensure_session_item(peer)
        self.pageStack.addWidget(win)
        self.pageStack.setCurrentWidget(win)
        self._focus_session(peer)
        return win

    def on_call_invited(self, from_user, rate, channels, sampwidth, mode, p2p_peer_ip, p2p_peer_port):
        if from_user not in self.friends_local:
            try:
                self.net.reject_voice_call(from_user)
            except Exception:
                pass
            return
        win = self.show_private_window(from_user)
        if win:
            win.receive_incoming_call(rate, channels, sampwidth, mode, p2p_peer_ip, p2p_peer_port)

    def on_call_accepted(self, from_user, mode, p2p_peer_ip, p2p_peer_port):
        if from_user in self.private_windows:
            self.private_windows[from_user].on_call_accepted(from_user, mode, p2p_peer_ip, p2p_peer_port)

    def on_call_rejected(self, from_user):
        if from_user in self.private_windows:
            self.private_windows[from_user].on_call_rejected(from_user)

    def on_call_ended(self, from_user):
        if from_user in self.private_windows:
            self.private_windows[from_user].on_call_ended(from_user)

    # ----- group voice -----
    def start_group_call(self):
        if self.group_call_active:
            QMessageBox.information(self, "提示", "组播语音已在进行中")
            return
        try:
            self.net.start_group_call()
            self.net.join_group_voice(GROUP_VOICE_PORT)
            self._start_group_send_thread()
            self.group_call_active = True
            self.group_call_initiator = True
            self.append_log("[组播] 你已发起组播语音，其他用户可选择加入")
        except Exception as e:
            QMessageBox.critical(self, "组播语音", str(e))

    def end_group_call(self):
        if not self.group_call_active:
            QMessageBox.information(self, "提示", "当前没有组播语音")
            return
        if not self.group_call_initiator:
            QMessageBox.warning(self, "提示", "仅发起者可结束组播语音")
            return
        try:
            self.net.end_group_call()
        except Exception as e:
            QMessageBox.critical(self, "组播语音", str(e))

    def on_group_call_invite(self, from_user, port):
        ret = QMessageBox.question(
            self,
            "组播语音邀请",
            f"收到来自 {from_user} 的组播语音邀请，是否加入？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        join = ret == QMessageBox.Yes
        try:
            self.net.respond_group_call(join)
            if join:
                self.net.join_group_voice(port)
                self._start_group_send_thread()
                self.group_call_active = True
                self.group_call_initiator = False
                self.append_log(f"[组播] 已加入 {from_user} 发起的组播语音")
            else:
                self.append_log("[组播] 你已拒绝本次组播语音邀请")
        except Exception as e:
            QMessageBox.critical(self, "组播语音", str(e))

    def on_group_call_join_ok(self, _port, initiator):
        self.append_log(f"[组播] 服务器确认加入成功，发起者：{initiator}")

    def on_group_call_ended(self, from_user, text):
        self.append_log(f"[组播] {from_user}: {text}")
        self._stop_group_voice_local()

    def _start_group_send_thread(self):
        if self.group_send_thread and self.group_send_thread.is_alive():
            return
        if not pyaudio:
            self.append_log("[组播] pyaudio 未安装，无法发送组播语音")
            return

        self.group_send_stop.clear()

        def worker():
            pa = None
            stream = None
            try:
                pa = pyaudio.PyAudio()
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=VOICE_CHANNELS,
                    rate=VOICE_RATE,
                    input=True,
                    frames_per_buffer=VOICE_CHUNK,
                )
                while not self.group_send_stop.is_set():
                    data = stream.read(VOICE_CHUNK, exception_on_overflow=False)
                    if not data:
                        continue
                    self.net.send_group_voice_frame(data)
            except Exception as e:
                self.append_log(f"[组播] 麦克风发送线程异常: {e}")
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

        self.group_send_thread = threading.Thread(target=worker, daemon=True)
        self.group_send_thread.start()

    def on_group_voice_frame(self, from_user, audio_bytes, channels, sampwidth, rate):
        player = self.group_voice_player.get(from_user)
        if player is None:
            try:
                player = PCMPlayer(rate=rate, channels=channels, sampwidth=sampwidth)
                self.group_voice_player[from_user] = player
            except Exception as e:
                self.append_log(f"[组播] 创建播放设备失败: {e}")
                return
        player.submit(audio_bytes)

    def _stop_group_voice_local(self):
        self.group_call_active = False
        self.group_call_initiator = False
        self.group_send_stop.set()
        if self.group_send_thread and self.group_send_thread.is_alive():
            try:
                self.group_send_thread.join(timeout=1.0)
            except Exception:
                pass
        self.group_send_thread = None

        for p in list(self.group_voice_player.values()):
            try:
                p.close()
            except Exception:
                pass
        self.group_voice_player.clear()

        self.net._close_group_voice()

    def on_disconnected(self, text):
        QMessageBox.critical(self, "连接断开", text)
        self.append_log(f"[系统] {text}")

    def quit_client(self):
        self._stop_group_voice_local()
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
        self.setWindowTitle("融合版语音聊天客户端")
        self.resize(1080, 760)

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

        self._apply_v3_style()
        self.stack.setCurrentWidget(self.loginPage)

    def _apply_v3_style(self):
        self.setStyleSheet(
            """
            QWidget { font-family: 'Microsoft YaHei'; font-size: 13px; }
            QMainWindow { background: #f3f7ff; }
            QLabel { color: #1f2937; }
            QTextBrowser, QLineEdit, QComboBox, QSpinBox {
                background: #ffffff;
                border: 1px solid #bfdbfe;
                border-radius: 8px;
                padding: 6px;
            }
            QPushButton {
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #1d4ed8; }
            QPushButton:disabled { background: #9ca3af; }
            QCheckBox { padding: 3px; }
            """
        )

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

    def on_disconnected(self, text):
        QMessageBox.critical(self, "连接断开", text)
        self.mainPage.append_log(f"[系统] {text}")
        self.stack.setCurrentWidget(self.loginPage)
        self.current_username = ""

    def closeEvent(self, event):
        try:
            self.mainPage.quit_client()
        except Exception:
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
