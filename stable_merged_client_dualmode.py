#!/usr/bin/env python3
# merged dual-mode chat client (TCP relay + P2P direct + group voice)

import html
import ipaddress
import json
import os
import queue
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import wave

try:
    import pyaudio
except Exception:
    pyaudio = None

from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

SERVER = ("10.192.22.43", 65432)
GROUP_VOICE_PORT = 65433
VOICE_RATE = 16000
VOICE_CHANNELS = 1
VOICE_SAMPWIDTH = 2
VOICE_CHUNK = 1024

STATUS_LABELS = {
    "online_free": "online_free在线空闲",
    "calling": "calling通话中",
    "not_online": "not_online离线",
}


# ---------- 基础协议 ----------
def send_json(conn, obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    conn.sendall(struct.pack(">I", len(data)) + data)


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
    head = recvall(conn, 4)
    if not head:
        return None
    msg_len = struct.unpack(">I", head)[0]
    body = recvall(conn, msg_len)
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {"type": "error", "text": "无法解析 JSON"}


def safe_name(text, fallback="user"):
    text = (text or "").strip()
    if not text:
        return fallback
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in text)[:48] or fallback


def friend_json_path(username):
    return f"{safe_name(username, 'user')}.json"


# ---------- 账号存储（fixed4 方式） ----------
def load_saved_accounts():
    if not os.path.exists("password.bin"):
        return []

    try:
        with open("password.bin", "rb") as f:
            data = f.read()
        if not data:
            return []
        obj = json.loads(data.decode("utf-8"))
        if isinstance(obj, list):
            out = []
            for item in obj:
                if not isinstance(item, dict):
                    continue
                nickname = str(item.get("nickname", "")).strip()
                if not nickname:
                    continue
                out.append({"nickname": nickname, "password": str(item.get("password", ""))})
            return out
    except Exception:
        pass

    # 兼容旧单账号二进制格式
    try:
        with open("password.bin", "rb") as f:
            len_nickname = struct.unpack(">I", f.read(4))[0]
            b_nickname = f.read(len_nickname)
            len_password = struct.unpack(">I", f.read(4))[0]
            b_password = f.read(len_password)
        nickname = b_nickname.decode("utf-8")
        password = b_password.decode("utf-8")
        return [{"nickname": nickname, "password": password}] if nickname else []
    except Exception:
        return []


def save_saved_accounts(accounts):
    out = []
    seen = set()
    for item in accounts:
        if not isinstance(item, dict):
            continue
        nickname = str(item.get("nickname", "")).strip()
        if not nickname or nickname in seen:
            continue
        seen.add(nickname)
        out.append({"nickname": nickname, "password": str(item.get("password", ""))})

    with open("password.bin", "wb") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2).encode("utf-8"))


def password_pack(nickname, password):
    nickname = (nickname or "").strip()
    if not nickname:
        return
    accounts = load_saved_accounts()
    updated = False
    for item in accounts:
        if item.get("nickname") == nickname:
            item["password"] = password
            updated = True
            break
    if not updated:
        accounts.append({"nickname": nickname, "password": password})
    save_saved_accounts(accounts)


def password_unpack():
    accounts = load_saved_accounts()
    if not accounts:
        return None, None
    first = accounts[0]
    return first.get("nickname"), first.get("password")


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


# ---------- 语音工具 ----------
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
        loops = max(1, int(rate / frames_per_buffer * float(seconds)))
        for _ in range(loops):
            frames.append(stream.read(frames_per_buffer, exception_on_overflow=False))
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


def send_wav_file_over_conn(conn, filepath, to_user):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"文件不存在: {filepath}")

    with wave.open(filepath, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()

    send_json(conn, {
        "type": "audio",
        "to": to_user,
        "bytes_len": len(raw),
        "format": "wav",
        "channels": channels,
        "sampwidth": sampwidth,
        "framerate": framerate,
    })
    conn.sendall(raw)


class PCMPlayer:
    def __init__(self, rate=16000, channels=1, sampwidth=2):
        if not pyaudio:
            raise RuntimeError("pyaudio 未安装，无法播放语音")
        self.pa = pyaudio.PyAudio()
        self.stream = self.pa.open(
            format=self.pa.get_format_from_width(sampwidth),
            channels=channels,
            rate=rate,
            output=True,
        )
        self.q = queue.Queue(maxsize=512)
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

    callInvited = pyqtSignal(str, int, int, int, str, str, int)
    callAccepted = pyqtSignal(str, str, str, int)
    callRejected = pyqtSignal(str)
    callEnded = pyqtSignal(str)

    friendRequestReceived = pyqtSignal(str)
    friendAdded = pyqtSignal(str, str)
    friendRejected = pyqtSignal(str)
    friendListReceived = pyqtSignal(list)
    friendStatusUpdated = pyqtSignal(str, str)

    groupCallInvite = pyqtSignal(str, int)
    groupCallJoinOk = pyqtSignal(int, str)
    groupCallEnded = pyqtSignal(str, str)
    groupVoiceFrameReceived = pyqtSignal(str, bytes, int, int, int)

    def __init__(self, server):
        super().__init__()
        self.server = server
        self.conn = None
        self.running = False
        self.listener = None
        self.send_lock = threading.Lock()
        self.pending_auth = None
        self.friend_feature_supported = True
        self.current_user = ""

        self.p2p_port = 0
        self.p2p_ip = ""
        self.p2p_listener_sock = None
        self.p2p_conns = {}
        self.p2p_locks = {}
        self.p2p_lock = threading.Lock()

        self.group_voice_conn = None
        self.group_voice_lock = threading.Lock()
        self.group_voice_running = False

        self._start_p2p_listener()

    def _start_p2p_listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 0))
        s.listen(8)
        s.settimeout(1.0)
        self.p2p_listener_sock = s
        self.p2p_port = s.getsockname()[1]
        threading.Thread(target=self._p2p_accept_loop, daemon=True).start()

    def _local_ip_for_server(self):
        try:
            tmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tmp.connect(self.server)
            ip = tmp.getsockname()[0]
            tmp.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def connect_server(self):
        if self.conn and self.running:
            return
        self.p2p_ip = self._local_ip_for_server()
        self.conn = socket.create_connection(self.server)
        self.running = True
        self.listener = threading.Thread(target=self._listener_loop, daemon=True)
        self.listener.start()

    def close(self):
        self.running = False
        self._close_group_voice()
        self._close_all_p2p()

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

    def _close_all_p2p(self):
        with self.p2p_lock:
            conns = list(self.p2p_conns.values())
            self.p2p_conns.clear()
            self.p2p_locks.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass

    def send(self, obj):
        if not self.conn:
            raise RuntimeError("尚未连接服务器")
        with self.send_lock:
            send_json(self.conn, obj)

    def login(self, username, password):
        self.pending_auth = {"type": "login", "username": username}
        self.send({
            "type": "login",
            "username": username,
            "password": password,
            "p2p_ip": self.p2p_ip,
            "p2p_port": self.p2p_port,
        })

    def register(self, username, password):
        self.pending_auth = {"type": "register", "username": username}
        self.send({
            "type": "register",
            "username": username,
            "password": password,
            "p2p_ip": self.p2p_ip,
            "p2p_port": self.p2p_port,
        })

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
        tmp = f"tmp_record_{int(time.time() * 1000)}.wav"

        def worker():
            try:
                record_wav(tmp, seconds)
                self.send_audio_file(to_user, tmp)
                if on_done:
                    on_done(True, f"已录音并发送 {seconds} 秒语音")
            except Exception as e:
                if on_done:
                    on_done(False, str(e))
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def start_voice_call(self, to_user, mode):
        self.send({
            "type": "call_start",
            "to": to_user,
            "mode": mode,
            "rate": VOICE_RATE,
            "channels": VOICE_CHANNELS,
            "sampwidth": VOICE_SAMPWIDTH,
        })

    def accept_voice_call(self, to_user):
        self.send({"type": "call_accept", "to": to_user})

    def reject_voice_call(self, to_user):
        self.send({"type": "call_reject", "to": to_user})

    def end_voice_call(self, to_user):
        self.send({"type": "call_end", "to": to_user})

    def send_voice_frame(self, to_user, raw_bytes, mode="tcp"):
        if mode == "p2p":
            self._send_p2p_voice_frame(to_user, raw_bytes)
            return

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

    # ----- P2P -----
    def _p2p_accept_loop(self):
        while True:
            if not self.p2p_listener_sock:
                break
            try:
                conn, _addr = self.p2p_listener_sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            threading.Thread(target=self._p2p_handshake_receiver, args=(conn,), daemon=True).start()

    def _p2p_handshake_receiver(self, conn):
        try:
            hello = recv_json(conn)
            if not hello or hello.get("type") != "p2p_hello":
                conn.close()
                return
            from_user = str(hello.get("from") or "").strip()
            if not from_user:
                conn.close()
                return
            self._register_p2p_conn(from_user, conn)
            self._p2p_recv_loop(from_user, conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    def open_p2p_to_peer(self, peer, ip, port):
        peer = (peer or "").strip()
        if not peer or not ip or int(port or 0) <= 0:
            raise RuntimeError("P2P 目标地址无效")

        with self.p2p_lock:
            if peer in self.p2p_conns:
                return

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((ip, int(port)))
        s.settimeout(None)
        send_json(s, {"type": "p2p_hello", "from": self.current_user})
        self._register_p2p_conn(peer, s)
        threading.Thread(target=self._p2p_recv_loop, args=(peer, s), daemon=True).start()

    def _register_p2p_conn(self, peer, conn):
        with self.p2p_lock:
            old = self.p2p_conns.get(peer)
            if old and old is not conn:
                try:
                    old.close()
                except Exception:
                    pass
            self.p2p_conns[peer] = conn
            self.p2p_locks[peer] = threading.Lock()

    def _p2p_recv_loop(self, peer, conn):
        try:
            while True:
                msg = recv_json(conn)
                if msg is None:
                    break
                mtype = msg.get("type")
                if mtype == "p2p_voice_frame":
                    blen = msg.get("bytes_len", 0)
                    if not isinstance(blen, int) or blen <= 0:
                        continue
                    raw = recvall(conn, blen)
                    if raw is None:
                        break
                    self.voiceFrameReceived.emit(
                        peer,
                        raw,
                        int(msg.get("channels", VOICE_CHANNELS)),
                        int(msg.get("sampwidth", VOICE_SAMPWIDTH)),
                        int(msg.get("rate", VOICE_RATE)),
                    )
        except Exception:
            pass
        finally:
            with self.p2p_lock:
                cur = self.p2p_conns.get(peer)
                if cur is conn:
                    self.p2p_conns.pop(peer, None)
                    self.p2p_locks.pop(peer, None)
            try:
                conn.close()
            except Exception:
                pass

    def _send_p2p_voice_frame(self, peer, raw_bytes):
        with self.p2p_lock:
            conn = self.p2p_conns.get(peer)
            lock = self.p2p_locks.get(peer)
        if not conn or not lock:
            return
        with lock:
            send_json(conn, {
                "type": "p2p_voice_frame",
                "bytes_len": len(raw_bytes),
                "rate": VOICE_RATE,
                "channels": VOICE_CHANNELS,
                "sampwidth": VOICE_SAMPWIDTH,
            })
            conn.sendall(raw_bytes)

    # ----- Group voice (dedicated tcp port) -----
    def start_group_call(self):
        self.send({"type": "group_call_start"})

    def respond_group_call(self, join):
        self.send({"type": "group_call_response", "join": bool(join)})

    def end_group_call(self):
        self.send({"type": "group_call_end"})

    def join_group_voice(self, port):
        self._close_group_voice()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.server[0], int(port)))
        send_json(s, {"type": "group_auth", "username": self.current_user})
        ack = recv_json(s)
        if not ack or ack.get("type") == "error":
            s.close()
            raise RuntimeError((ack or {}).get("text", "组播语音连接失败"))

        self.group_voice_conn = s
        self.group_voice_running = True
        threading.Thread(target=self._group_voice_recv_loop, daemon=True).start()

    def _group_voice_recv_loop(self):
        conn = self.group_voice_conn
        try:
            while self.group_voice_running and conn:
                msg = recv_json(conn)
                if msg is None:
                    break
                if msg.get("type") != "group_voice_frame":
                    continue
                blen = msg.get("bytes_len", 0)
                if not isinstance(blen, int) or blen <= 0:
                    continue
                raw = recvall(conn, blen)
                if raw is None:
                    break
                self.groupVoiceFrameReceived.emit(
                    msg.get("from", "?"),
                    raw,
                    int(msg.get("channels", VOICE_CHANNELS)),
                    int(msg.get("sampwidth", VOICE_SAMPWIDTH)),
                    int(msg.get("rate", VOICE_RATE)),
                )
        except Exception:
            pass
        finally:
            self._close_group_voice()

    def send_group_voice_frame(self, raw_bytes):
        with self.group_voice_lock:
            if not self.group_voice_conn:
                return
            send_json(self.group_voice_conn, {
                "type": "group_voice_frame",
                "bytes_len": len(raw_bytes),
                "rate": VOICE_RATE,
                "channels": VOICE_CHANNELS,
                "sampwidth": VOICE_SAMPWIDTH,
            })
            self.group_voice_conn.sendall(raw_bytes)

    def _close_group_voice(self):
        self.group_voice_running = False
        with self.group_voice_lock:
            conn = self.group_voice_conn
            self.group_voice_conn = None
        if conn:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

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
                    msg_text = msg.get("text", "")
                    if msg.get("offline"):
                        msg_text = f"[离线消息] {msg_text}"
                    self.messageReceived.emit(msg.get("from", "?"), msg_text)
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
                    self.statusReceived.emit(f"[error] {text}")
                elif mtype == "audio":
                    from_user = msg.get("from", "?")
                    blen = msg.get("bytes_len", 0)
                    if not isinstance(blen, int) or blen <= 0:
                        continue
                    raw = recvall(self.conn, blen)
                    if raw is None:
                        self.disconnected.emit("接收语音消息中断")
                        break
                    self.audioMessageReceived.emit(
                        from_user,
                        raw,
                        int(msg.get("channels", 1)),
                        int(msg.get("sampwidth", 2)),
                        int(msg.get("framerate", 16000)),
                    )
                elif mtype == "audio_frame":
                    from_user = msg.get("from", "?")
                    blen = msg.get("bytes_len", 0)
                    if not isinstance(blen, int) or blen <= 0:
                        continue
                    raw = recvall(self.conn, blen)
                    if raw is None:
                        self.disconnected.emit("接收实时语音中断")
                        break
                    self.voiceFrameReceived.emit(
                        from_user,
                        raw,
                        int(msg.get("channels", VOICE_CHANNELS)),
                        int(msg.get("sampwidth", VOICE_SAMPWIDTH)),
                        int(msg.get("rate", VOICE_RATE)),
                    )
                elif mtype == "call_start":
                    self.callInvited.emit(
                        msg.get("from", "?"),
                        int(msg.get("rate", VOICE_RATE)),
                        int(msg.get("channels", VOICE_CHANNELS)),
                        int(msg.get("sampwidth", VOICE_SAMPWIDTH)),
                        str(msg.get("mode", "tcp") or "tcp"),
                        str(msg.get("p2p_peer_ip", "") or ""),
                        int(msg.get("p2p_peer_port", 0) or 0),
                    )
                elif mtype == "call_accept":
                    self.callAccepted.emit(
                        msg.get("from", "?"),
                        str(msg.get("mode", "tcp") or "tcp"),
                        str(msg.get("p2p_peer_ip", "") or ""),
                        int(msg.get("p2p_peer_port", 0) or 0),
                    )
                elif mtype == "call_reject":
                    self.callRejected.emit(msg.get("from", "?"))
                elif mtype == "call_end":
                    self.callEnded.emit(msg.get("from", "?"))
                elif mtype == "group_call_invite":
                    self.groupCallInvite.emit(msg.get("from", "?"), int(msg.get("port", GROUP_VOICE_PORT)))
                elif mtype == "group_call_join_ok":
                    self.groupCallJoinOk.emit(int(msg.get("port", GROUP_VOICE_PORT)), str(msg.get("initiator", "")))
                elif mtype == "group_call_end":
                    self.groupCallEnded.emit(msg.get("from", "?"), msg.get("text", "组播语音已结束"))
                else:
                    self.statusReceived.emit(f"[recv] {msg}")
        except Exception as e:
            self.disconnected.emit(f"监听线程异常: {e}")
        finally:
            self.running = False
            try:
                if self.conn:
                    self.conn.close()
            except Exception:
                pass


