#!/usr/bin/env python3
# simple_tcp_chat_client_sync_login.py
import socket
import threading
import json
import struct
import sys
import os
import time
import wave
import pyaudio

SERVER = ('10.192.8.240', 65432)

# ---------- 全局同步变量（用于 login <-> listener 通信） ----------
login_event = threading.Event()
login_lock = threading.Lock()
login_result = {"success": False, "message": "", "username": None}
waiting_username = None  # 正在等待哪个用户名的登录/注册结果（或 None）
LOGIN_WAIT_TIMEOUT = 5.0  # 等待登录结果的超时（秒），按需调整

# ---------- password pack/unpack (unchanged逻辑但用字节长度) ----------
def password_pack(nickname: str, password: str):
    b_nickname = nickname.encode('utf-8')
    b_password = password.encode('utf-8')
    with open('password.bin', 'wb') as f:
        f.write(struct.pack('>I', len(b_nickname)))
        f.write(b_nickname)
        f.write(struct.pack('>I', len(b_password)))
        f.write(b_password)

def password_unpack():
    if not os.path.exists('password.bin'):
        return None, None
    with open('password.bin', 'rb') as f:
        len_nickname = struct.unpack('>I', f.read(4))[0]
        b_nickname = f.read(len_nickname)
        len_password = struct.unpack('>I', f.read(4))[0]
        b_password = f.read(len_password)
    return b_nickname.decode('utf-8'), b_password.decode('utf-8')

# ---------- framing send ----------
def send_json(conn, obj):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    length = struct.pack('>I', len(data))
    conn.sendall(length + data)

# ---------- blocking recv helpers ----------
def recvall(conn, n):
    buf = b''
    while len(buf) < n:
        try:
            chunk = conn.recv(n - len(buf))
        except ConnectionResetError:
            return None
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
    msg_len = struct.unpack('>I', raw_len)[0]
    data = recvall(conn, msg_len)
    if not data:
        return None
    try:
        return json.loads(data.decode('utf-8'))
    except Exception:
        return {"type": "error", "text": "无法解析服务器返回的 JSON"}

def play_wav_bytes(wav_bytes, channels, sampwidth, framerate):
    """用 pyaudio 直接播放原始 wav bytes（不包含 WAV header），
       这里我们用 wave模块写成临时文件再播放更稳妥（兼容性好）。"""
    global recv_wav_counter
    recv_wav_counter += 1
    fname = f"received_{int(time.time())}_{recv_wav_counter}.wav"
    # 写入 WAV 文件头并保存
    wf = wave.open(fname, 'wb')
    wf.setnchannels(channels)
    wf.setsampwidth(sampwidth)
    wf.setframerate(framerate)
    wf.writeframes(wav_bytes)
    wf.close()
    print(f"已保存语音到 {fname}，开始播放...")
    if pyaudio:
        # 播放
        wf = wave.open(fname, 'rb')
        pa = pyaudio.PyAudio()
        stream = pa.open(format=pa.get_format_from_width(wf.getsampwidth()),
                         channels=wf.getnchannels(),
                         rate=wf.getframerate(),
                         output=True)
        data = wf.readframes(1024)
        while data:
            stream.write(data)
            data = wf.readframes(1024)
        stream.stop_stream()
        stream.close()
        pa.terminate()
        wf.close()
    else:
        print("pyaudio 未安装，无法播放。")

# ---------- listener 线程：接收所有服务器消息并处理 ---------
def listener_thread(conn):
    global waiting_username, login_event, login_result
    try:
        while True:
            msg = recv_json(conn)
            if msg is None:
                print("与服务器的连接已断开")
                # 如果我们正在等待登录结果，通知主线程失败
                with login_lock:
                    if waiting_username:
                        login_result["success"] = False
                        login_result["message"] = "连接断开"
                        login_event.set()
                break

            # 简单展示不同类型
            mtype = msg.get('type')
            # ----- 在这里尝试识别 login/register 的回应，并唤醒主线程 -----
            handled_by_login = False
            with login_lock:
                if waiting_username:
                    # 如果服务器给出 error，视为失败
                    if mtype == 'error':
                        login_result["success"] = False
                        login_result["message"] = msg.get('text', '')
                        login_result["username"] = waiting_username
                        login_event.set()
                        handled_by_login = True
                        waiting_username = None
                    elif mtype == 'info':
                        txt = msg.get('text', '')
                        # 常见表示成功的关键字
                        if "登录成功" in txt:
                            login_result["success"] = True
                            login_result["message"] = txt
                            login_result["username"] = waiting_username
                            login_event.set()
                            handled_by_login = True
                            waiting_username = None
                    # 其他类型暂不处理
            # ----- end login handling -----

            # 如果 login 处理过该消息，就把处理结果打印或忽略该消息的重复显示
            if handled_by_login:
                # 我们仍然希望在客户端看到服务器 info/error 消息的输出，
                # 所以不直接 continue，这里 fallthrough 到下面的打印逻辑。
                pass

            # 常规消息展示
            if mtype == 'message':
                print(f"[私聊] {msg.get('from')}: {msg.get('text')}")
            elif mtype == 'broadcast':
                print(f"[广播] {msg.get('from')}: {msg.get('text')}")
            elif mtype == 'list_response':
                print("在线用户:", ", ".join(msg.get('users', [])))
            elif mtype == 'info':
                print("[info]", msg.get('text'))
            elif mtype == 'error':
                print("[error]", msg.get('text'))

            elif mtype == 'audio':
                # 收到音频头，随后从 socket 读取指定字节数
                from_user = msg.get('from')
                bytes_len = msg.get('bytes_len')
                fmt = msg.get('format', 'wav')
                channels = msg.get('channels') or 1
                sampwidth = msg.get('sampwidth') or 2
                framerate = msg.get('framerate') or 16000
                print(f"[audio] 来自 {from_user} 的语音，大小 {bytes_len} 字节，format={fmt}")
                if not isinstance(bytes_len, int) or bytes_len <= 0:
                    print("收到不合法的音频长度")
                    continue
                audio_bytes = recvall(conn, bytes_len)
                if audio_bytes is None:
                    print("在接收音频数据时连接被中断")
                    break
                # 保存并播放
                try:
                    play_wav_bytes(audio_bytes, channels, sampwidth, framerate)
                except Exception as e:
                    print("播放或保存音频时出错:", e)
            else:
                print("[recv]", msg)
    except Exception as e:
        print("监听线程异常:", e)
    finally:
        try:
            conn.close()
        except:
            pass
        print("监听线程结束")

# ---------- login 模块：发送请求后等待 listener 通知结果 ----------
def login(conn):
    global waiting_username, login_event, login_result
    print("是否自动调用上次信息登录(y/n)：")
    line = input().strip().lower()
    if line == 'y':
        nickname, password = password_unpack()
        if nickname:
            # 先清理上次状态
            with login_lock:
                waiting_username = nickname
                login_event.clear()
                login_result = {"success": False, "message": "", "username": None}
            send_json(conn, {"type": "login", "username": nickname, "password": password})
            # 等待 listener 的回应
            waited = login_event.wait(timeout=LOGIN_WAIT_TIMEOUT)
            with login_lock:
                success = login_result.get("success", False)
                message = login_result.get("message", "")
            if not waited:
                print(f"登录请求超时（等待 {LOGIN_WAIT_TIMEOUT} 秒），继续 ...")
            elif success:
                #print(f"自动登录成功: {message}")
                return True
            else:
                print(f"自动登录失败: {message}")
        else:
            print("没有保存的密码文件")
    # 进入交互式登录/注册引导（直到登录成功或用户中断）
    while True:
        print("登录：输入 '/login <username> <password>' 或 输入 '/register <username> <password>' 来注册")
        try:
            line = input()
        except EOFError:
            return False
        if not line:
            continue
        if line.startswith('/register '):
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                print("格式: /register <username> <password>")
                continue
            username = parts[1].strip()
            password = parts[2].strip()
            # 保存密码文件（可选）
            password_pack(username, password)
            # 发送注册请求并等待结果
            with login_lock:
                waiting_username = username
                login_event.clear()
                login_result = {"success": False, "message": "", "username": None}
            send_json(conn, {"type": "register", "username": username, "password": password})
            waited = login_event.wait(timeout=LOGIN_WAIT_TIMEOUT)
            with login_lock:
                success = login_result.get("success", False)
                message = login_result.get("message", "")
            if not waited:
                print(f"注册请求超时（等待 {LOGIN_WAIT_TIMEOUT} 秒），请查看服务器是否在线")
                # 允许继续尝试或手动处理
                continue
            if success:
                #print(f"注册成功：{message}")
                return True
            else:
                print(f"注册失败：{message}")
                continue
        elif line.startswith('/login '):
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                print("格式: /login <username> <password>")
                continue
            username = parts[1].strip()
            password = parts[2].strip()
            password_pack(username, password)
            # 发送登录请求并等待结果
            with login_lock:
                waiting_username = username
                login_event.clear()
                login_result = {"success": False, "message": "", "username": None}
            send_json(conn, {"type": "login", "username": username, "password": password})
            waited = login_event.wait(timeout=LOGIN_WAIT_TIMEOUT)
            with login_lock:
                success = login_result.get("success", False)
                message = login_result.get("message", "")
            if not waited:
                print(f"登录请求超时（等待 {LOGIN_WAIT_TIMEOUT} 秒），请查看服务器是否在线")
                continue
            if success:
                #print(f"登录成功：{message}")
                return True
            else:
                print(f"登录失败：{message}")
                continue
        else:
            print("请输入 /register 或 /login 指令进行登录或注册")

def record_wav(path, seconds, channels=1, rate=16000, frames_per_buffer=1024):
    if not pyaudio:
        raise RuntimeError("pyaudio 未安装")
    pa = pyaudio.PyAudio()
    sample_format = pyaudio.paInt16
    sampwidth = pa.get_sample_size(sample_format)
    stream = pa.open(format=sample_format,
                     channels=channels,
                     rate=rate,
                     input=True,
                     frames_per_buffer=frames_per_buffer)
    print(f"开始录音 {seconds}s ...")
    frames = []
    try:
        for _ in range(0, int(rate / frames_per_buffer * seconds)):
            data = stream.read(frames_per_buffer, exception_on_overflow=False)
            frames.append(data)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
    wf = wave.open(path, 'wb')
    wf.setnchannels(channels)
    wf.setsampwidth(sampwidth)
    wf.setframerate(rate)
    wf.writeframes(b''.join(frames))
    wf.close()
    print("录音完成，保存到", path)
    return sampwidth  # 返回每样本字节数（用于 metadata）

def send_wav_file_over_conn(conn, filepath, to_user):
    # 读取 wav 并发送：先发 header JSON，然后发 raw pcm bytes（wave.readframes）
    if not os.path.exists(filepath):
        print("文件不存在:", filepath); return
    with wave.open(filepath, 'rb') as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        # 读取所有帧（注意大小）
        raw = wf.readframes(nframes)
    bytes_len = len(raw)
    # 头部元信息
    header = {
        "type": "audio",
        "to": to_user,
        "bytes_len": bytes_len,
        "format": "wav",
        "channels": channels,
        "sampwidth": sampwidth,
        "framerate": framerate,
    }
    try:
        send_json(conn, header)
        conn.sendall(raw)
        print(f"已发送 {filepath} 给 {to_user}，大小 {bytes_len} 字节")
    except Exception as e:
        print("发送失败:", e)

# ---------- repl：在登录成功后进入聊天命令循环 ----------
def repl(conn):
    ok = login(conn)
    if not ok:
        print("未登录，退出客户端或尝试重新连接")
        return
    time.sleep(1)
    print("命令：/list（列在线用户） /msg <user> <text>（私聊） \n /sendfile <wav_path> <user>（发送录音文件） /record <seconds> <user>（录音） /quit（退出）")
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line:
            continue
        if line.startswith('/register '):
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                print("格式: /register <username> <password>")
                continue
            username = parts[1].strip()
            password = parts[2].strip()
            password_pack(username, password)
            send_json(conn, {"type": "register", "username": username, "password": password})
        elif line.strip() == '/list':
            send_json(conn, {"type": "list_request"})
        elif line.startswith('/msg '):
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                print("格式: /msg <user> <text>")
                continue
            to = parts[1]; text = parts[2]
            send_json(conn, {"type": "message", "to": to, "text": text})
        elif line.startswith('/sendfile '):
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                print("格式: /sendfile <wav_path> <user>")
                continue
            path = parts[1];
            to = parts[2]
            send_wav_file_over_conn(conn, path, to)
        elif line.startswith('/record '):
            # /record <seconds> <user>
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                print("格式: /record <seconds> <user>")
                continue
            try:
                seconds = float(parts[1])
            except:
                print("seconds 必须是数字")
                continue
            to = parts[2]
            tmpfile = f"tmp_record_{int(time.time())}.wav"
            try:
                sampwidth = record_wav(tmpfile, seconds)
                send_wav_file_over_conn(conn, tmpfile, to)
            except Exception as e:
                print("录音或发送出错:", e)
            finally:
                try:
                    os.remove(tmpfile)
                except:
                    pass
        elif line.strip() == '/quit':
            send_json(conn, {"type": "quit"})
            break
        else:
            # 默认作为广播
            send_json(conn, {"type": "broadcast", "text": line})
    print("输入循环结束，等待线程结束...")

# ---------- main ----------
def main():
    try:
        conn = socket.create_connection(SERVER) # TCP 连接
    except Exception as e:
        print("连接服务器失败:", e)
        return
    t = threading.Thread(target=listener_thread, args=(conn,), daemon=True)
    t.start()
    try:
        repl(conn)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            conn.close()
        except:
            pass
        print("客户端退出")

if __name__ == '__main__':
    main()