#!/usr/bin/env python3
# simple_tcp_chat_client.py
import socket
import threading
import json
import struct
import sys

SERVER = ('10.192.31.112', 65432)

def send_json(conn, obj):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    length = struct.pack('>I', len(data))
    conn.sendall(length + data)

def recvall(conn, n):
    buf = b''
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
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
    return json.loads(data.decode('utf-8'))

def listener_thread(conn):
    try:
        while True:
            msg = recv_json(conn)
            if msg is None:
                print("与服务器的连接已断开")
                break
            # 简单展示不同类型
            mtype = msg.get('type')
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

def repl(conn):
    print("命令：/nick <name>（注册） /list（列在线用户） /msg <user> <text>（私聊） /quit（退出）")
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line:
            continue
        if line.startswith('/nick '):
            name = line.split(maxsplit=1)[1].strip()
            send_json(conn, {"type": "register", "username": name})
        elif line.strip() == '/list':
            send_json(conn, {"type": "list_request"})
        elif line.startswith('/msg '):
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                print("格式: /msg <user> <text>")
                continue
            to = parts[1]; text = parts[2]
            send_json(conn, {"type": "message", "to": to, "text": text})
        elif line.strip() == '/quit':
            send_json(conn, {"type": "quit"})
            break
        else:
            # 默认作为广播
            send_json(conn, {"type": "broadcast", "text": line})
    print("输入循环结束，等待线程结束...")

def main():
    conn = socket.create_connection(SERVER)
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