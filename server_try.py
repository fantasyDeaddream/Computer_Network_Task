#!/usr/bin/env python3
# threaded_tcp_chat_server.py
import socket
import threading
import json
import struct
import pickle
import os
import sys
import time

HOST = '10.192.60.12'
PORT = 65432
MAX_MSG = 100 * 1024 * 1024

# 运行时客户端字典：username -> (conn, addr)
clients = {}
clients_lock = threading.Lock()

# 持久化的已注册用户（只保存 username->password，不保存 socket）
USERS_FILE = 'users.pkl'
users = {}

shutdown_event = threading.Event()

def users_write():
    try:
        with open(USERS_FILE, 'wb') as f:
            pickle.dump(users, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        print("保存 users 失败:", e)

def users_read():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'rb') as f:
            return pickle.load(f)
    except Exception as e:
        print("读取 users 失败:", e)
        return {}

# json封装，大端序4字节头存data数据长度，后续存data
def send_json(conn, obj):
    try:
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        length = struct.pack('>I', len(data))
        conn.sendall(length + data)
    except Exception as e:
        # 发送失败通常说明连接不可用，抛出让调用方处理
        raise

# json读取，先读长度，再读data数据
def recv_json(conn):
    raw_len = recvall(conn, 4)
    if not raw_len:
        return None
    msg_len = struct.unpack('>I', raw_len)[0]
    data = recvall(conn, msg_len)
    if not data:
        return None
    return json.loads(data.decode('utf-8'))

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

# 信息广播（会跳过已出错的连接并移除它）
def broadcast(msg_obj, exclude_username=None):
    with clients_lock:
        for uname, tup in list(clients.items()):
            if uname == exclude_username:
                continue
            conn, _ = tup
            try:
                send_json(conn, msg_obj)
            except Exception:
                # 若发送失败，移除该客户端
                print(f"广播给 {uname} 失败，移除客户端")
                remove_client(uname)

# 客户端退出机制（优雅关闭连接并广播信息）
def remove_client(username):
    with clients_lock:
        tup = clients.pop(username, None)
    if tup:
        conn, addr = tup
        try:
            # 尝试优雅关闭
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            conn.close()
        except Exception:
            pass
        info = {"type": "info", "text": f"用户 {username} 已断开"}
        # 广播可以在新的线程中做，避免在持有锁时阻塞太久
        broadcast(info, exclude_username=None)
        print(f"Removed client {username} {addr}")

# 客户端通信线程
def handle_client(conn, addr):
    print("连接来自", addr)
    current_username = None
    try:
        while not shutdown_event.is_set():
            msg = recv_json(conn)
            if msg is None:
                # 连接关闭
                break
            mtype = msg.get('type')
            if mtype == 'register':
                username = msg.get('username')
                password = msg.get('password')
                if not username:
                    send_json(conn, {"type": "error", "text": "用户名不能为空"})
                    continue
                with clients_lock:
                    if username in clients:
                        send_json(conn, {"type": "error", "text": "用户名已被占用（在线）"})
                        continue
                    # 注册成功：把 socket 放入 clients，且把密码存入 users（持久化）
                    current_username = username
                    clients[username] = (conn, addr)
                    users[username] = password
                    users_write()
                send_json(conn, {"type": "info", "text": f"注册成功，欢迎 {username}"})
                broadcast({"type": "info", "text": f"用户 {username} 已上线"}, exclude_username=username)
                print(f"用户注册: {username} from {addr}")
                print("当前 clients:", list(clients.keys()))
            elif mtype == 'login':
                username = msg.get('username')
                password = msg.get('password')
                if not username:
                    send_json(conn, {"type": "error", "text": "此用户不存在"})
                else:
                    if not password == users[username]:
                        send_json(conn, {"type": "error", "text": "密码错误"})
                    else:
                        send_json(conn, {"type": "info", "text": f"登录成功，欢迎 {username}"})
                        broadcast({"type": "info", "text": f"用户 {username} 已上线"}, exclude_username=username)
                        clients[username] = (conn, addr)
                        current_username = username
            elif mtype == 'list_request':
                with clients_lock:
                    names = list(clients.keys())
                send_json(conn, {"type": "list_response", "users": names})
            elif mtype == 'broadcast':
                text = msg.get('text', '')
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先 /register 注册用户名"})
                    continue
                obj = {"type": "broadcast", "from": current_username, "text": text}
                broadcast(obj, exclude_username=None)
            elif mtype == 'message':
                # 私聊
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先 /register 注册用户名"})
                    continue
                to = msg.get('to')
                text = msg.get('text', '')
                if not to:
                    send_json(conn, {"type": "error", "text": "缺少 'to' 字段"})
                    continue
                with clients_lock:
                    target = clients.get(to)
                if not target:
                    send_json(conn, {"type": "error", "text": f"用户 {to} 不在线"})
                    continue
                target_conn, _ = target
                try:
                    send_json(target_conn, {"type": "message", "from": current_username, "text": text})
                    # 回执给发送者
                    send_json(conn, {"type": "info", "text": f"已发送给 {to}"})
                except Exception:
                    send_json(conn, {"type": "error", "text": f"发送给 {to} 失败"})

            #音频转发
            elif mtype == 'audio':
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先 /nick 注册用户名"})
                    continue
                to = msg.get('to')
                bytes_len = msg.get('bytes_len')
                if not to or not isinstance(bytes_len, int):
                    send_json(conn, {"type": "error", "text": "audio 需要 'to' 与 'bytes_len' 字段"})
                    continue
                if bytes_len <= 0 or bytes_len > MAX_MSG:
                    send_json(conn, {"type": "error", "text": "音频大小不合规"})
                    continue
                # 从发送者 socket 读取原始音频字节
                audio_bytes = recvall(conn, bytes_len)
                if audio_bytes is None:
                    break
                # 查找目标并转发（头 + 原始字节）
                with clients_lock:
                    target = clients.get(to)
                if not target:
                    send_json(conn, {"type": "error", "text": f"用户 {to} 不在线，无法发送语音"})
                    continue
                target_conn, _ = target
                # 发给目标：先发 header json（包含 bytes_len 与音频参数），然后发 raw bytes
                try:
                    header = {
                        "type": "audio",
                        "from": current_username,
                        "bytes_len": bytes_len,
                        "format": msg.get("format", "wav"),
                        "channels": msg.get("channels"),
                        "sampwidth": msg.get("sampwidth"),
                        "framerate": msg.get("framerate"),
                    }
                    send_json(target_conn, header)
                    target_conn.sendall(audio_bytes)
                    send_json(conn, {"type": "info", "text": f"已向 {to} 发送语音"})
                except Exception:
                    send_json(conn, {"type": "error", "text": f"发送给 {to} 时失败"})
                    remove_client(to)

            elif mtype == 'quit':
                send_json(conn, {"type": "info", "text": "bye"})
                break
            else:
                send_json(conn, {"type": "error", "text": f"未知消息类型: {mtype}"})
    except Exception as e:
        print("客户端处理出错:", e)
    finally:
        if current_username:
            remove_client(current_username)
        else:
            try:
                conn.close()
            except Exception:
                pass
        print("连接关闭", addr)

# repl 运行在单独线程：接收 /list 和 /quit
def repl():
    print("命令：/list（列在线用户) /quit（退出）")
    while not shutdown_event.is_set():
        try:
            line = input()
        except EOFError:
            break
        if not line:
            continue
        if line.strip() == '/list':
            with clients_lock:
                print("Users:", list(clients.keys()))
        elif line.strip() == '/quit':
            print("[系统]服务器关闭")
            shutdown_event.set()
            break

def main():
    global users
    users = users_read()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen()
    sock.settimeout(1.0)  # 允许周期性检查 shutdown_event
    print(f"聊天服务器在 {HOST}:{PORT} 启动")
    print("已加载已注册用户：", list(users.keys()))

    # 启动 repl 线程（监听 /quit）
    repl_thread = threading.Thread(target=repl, daemon=True)
    repl_thread.start()

    try:
        while not shutdown_event.is_set():
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                # 例如 sock 已 close()
                break
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("收到 KeyboardInterrupt，服务器关闭")
        shutdown_event.set()
    finally:
        print("开始关闭所有客户端连接...")
        # 先停止接受新的连接
        try:
            sock.close()
        except Exception:
            pass

        # 关闭所有已连接客户端
        with clients_lock:
            for uname, tup in list(clients.items()):
                conn, _ = tup
                try:
                    send_json(conn, {"type": "info", "text": "服务器即将关闭"})
                except Exception:
                    pass
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
            clients.clear()

        # 保存 users（已在注册时保存，这里再保存一次以确保）
        users_write()

        print("服务器已彻底关闭。")

if __name__ == '__main__':
    main()