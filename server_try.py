#!/usr/bin/env python3
# threaded_tcp_chat_server.py
import socket
import threading
import json
import struct

HOST = '10.192.31.112'
PORT = 65432

# username -> (conn, addr)
clients = {}
clients_lock = threading.Lock()

# --- framing helpers: length-prefix (4 bytes big-endian) ---
def send_json(conn, obj):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    length = struct.pack('>I', len(data))
    conn.sendall(length + data)

def recv_json(conn):
    # read 4 bytes length
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
        if not chunk:
            return None
        buf += chunk
    return buf

def broadcast(msg_obj, exclude_username=None):
    with clients_lock:
        for uname, (conn, _) in list(clients.items()):
            if uname == exclude_username:
                continue
            try:
                send_json(conn, msg_obj)
            except Exception:
                # 若发送失败，移除该客户端
                remove_client(uname)

def remove_client(username):
    with clients_lock:
        tup = clients.pop(username, None)
    if tup:
        conn, addr = tup
        try:
            conn.close()
        except Exception:
            pass
        info = {"type": "info", "text": f"用户 {username} 已断开"}
        broadcast(info, exclude_username=None)
        print(f"Removed client {username} {addr}")

def handle_client(conn, addr):
    print("连接来自", addr)
    current_username = None
    try:
        while True:
            msg = recv_json(conn)
            if msg is None:
                # 连接关闭
                break
            mtype = msg.get('type')
            if mtype == 'register':
                desired = msg.get('username')
                if not desired:
                    send_json(conn, {"type": "error", "text": "用户名不能为空"})
                    continue
                with clients_lock:
                    if desired in clients:
                        send_json(conn, {"type": "error", "text": "用户名已被占用"})
                        continue
                    # 注册成功
                    clients[desired] = (conn, addr)
                    current_username = desired
                send_json(conn, {"type": "info", "text": f"注册成功，欢迎 {desired}"})
                broadcast({"type": "info", "text": f"用户 {desired} 已上线"}, exclude_username=desired)
                print(f"用户注册: {desired} from {addr}")
            elif mtype == 'list_request':
                with clients_lock:
                    names = list(clients.keys())
                send_json(conn, {"type": "list_response", "users": names})
            elif mtype == 'broadcast':
                text = msg.get('text', '')
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先 /nick 注册用户名"})
                    continue
                obj = {"type": "broadcast", "from": current_username, "text": text}
                broadcast(obj, exclude_username=None)
            elif mtype == 'message':
                # 私聊
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先 /nick 注册用户名"})
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
                send_json(target_conn, {"type": "message", "from": current_username, "text": text})
                # 回执给发送者
                send_json(conn, {"type": "info", "text": f"已发送给 {to}"})
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

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen()
    print(f"聊天服务器在 {HOST}:{PORT} 启动")
    try:
        while True:
            conn, addr = sock.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("服务器关闭")
    finally:
        sock.close()

if __name__ == '__main__':
    main()