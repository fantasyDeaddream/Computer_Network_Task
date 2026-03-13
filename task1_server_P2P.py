import socket
import threading
import json

# 在线成员表: { addr_str: {"p2p_port": int, "conn": socket} }
online_clients = {}
lock = threading.Lock()


def broadcast_online_list():
    """向所有客户端广播最新的在线成员信息"""
    with lock:
        # 只提取展示信息，不发送 socket 对象
        display_list = {addr: info["p2p_port"] for addr, info in online_clients.items()}
        data = f"ONLINE_LIST:{json.dumps(display_list)}".encode('utf-8')
        for client in online_clients.values():
            try:
                client["conn"].send(data)
            except:
                pass


def handle_client(conn, addr):
    addr_str = f"{addr[0]}:{addr[1]}"
    try:
        # ① 接收客户端注册信息 (P2P 接收端口)
        reg_data = conn.recv(1024).decode('utf-8')
        if reg_data.startswith("REG_P2P_PORT:"):
            p2p_port = int(reg_data.split(":")[1])
            with lock:
                online_clients[addr_str] = {"p2p_port": p2p_port, "conn": conn, "ip": addr[0]}
            print(f"[系统] {addr_str} 已注册，P2P接收端口: {p2p_port}")
            broadcast_online_list()

        while True:
            data = conn.recv(1024).decode('utf-8')
            if not data: break

            # ③ 处理 P2P 请求 <to:IP>msg
            if data.startswith("<to:"):
                target_ip = data[4:data.find(">")]
                msg_content = data[data.find(">") + 1:]

                # 查找目标 IP 对应的 P2P 端口
                target_p2p_port = None
                with lock:
                    for info in online_clients.values():
                        if info["ip"] == target_ip:
                            target_p2p_port = info["p2p_port"]
                            break

                if target_p2p_port:
                    # 返回信令给发送方
                    conn.send(f"P2P_READY:{target_ip}:{target_p2p_port}:{msg_content}".encode('utf-8'))
                else:
                    conn.send("[错误] 目标IP不在线".encode('utf-8'))

            # ② 处理普通广播
            else:
                broadcast_data = f"[广播 {addr_str}]: {data}".encode('utf-8')
                with lock:
                    for a_str, info in online_clients.items():
                        if a_str != addr_str:
                            info["conn"].send(broadcast_data)
    except:
        pass
    finally:
        with lock:
            if addr_str in online_clients:
                del online_clients[addr_str]
        conn.close()
        print(f"[系统] {addr_str} 已下线")
        broadcast_online_list()


def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', 9999))
    server.listen(10)
    print("[服务端] 正在监听 9999 端口...")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    start_server()