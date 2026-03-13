import socket
import threading
import json
import sys
import time


class ClientApp:
    def __init__(self, server_ip='10.192.28.30'):
        self.server_ip = server_ip
        self.server_port = 9999
        self.p2p_rcv_port = 0
        self.client_socket = None

    def start_p2p_listener(self):
        """PtoP 接收方监听逻辑"""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('0.0.0.0', 0))  # 自动分配端口
        self.p2p_rcv_port = listener.getsockname()[1]
        listener.listen(5)
        while True:
            conn, addr = listener.accept()
            try:
                # 接收第一条消息（握手/通知消息）
                initial_data = conn.recv(1024).decode('utf-8')
                if initial_data.startswith("P2P_CONNECT_REQ:"):
                    sender_addr = initial_data.split(":", 1)[1]
                    sys.stdout.write('\r\033[K')  # 清除当前行输入提示
                    print(f"\n[P2P通知] {sender_addr} 请求建立的P2P连接成功")

                    # 接收实际的消息内容
                    data = conn.recv(1024).decode('utf-8')
                    print(f"[P2P直连收到 {addr}]: {data}")
                    sys.stdout.write("消息 >> ")
                    sys.stdout.flush()
            except Exception as e:
                print(f"\n[P2P错误] 接收异常: {e}")
            finally:
                conn.close()

    def p2p_send(self, ip, port, msg):
        """PtoP 发送方发起直连"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 记录自己的本地地址信息用于通知对方
            s.connect((ip, int(port)))
            local_addr = s.getsockname()

            # 1. 提示发送方连接已建立
            print(f"\n[系统] 已建立与 {ip}:{port} 的P2P连接")

            # 2. 发送一个协议头，通知接收方是谁在连接（即 client_snd 的提示）
            s.send(f"P2P_CONNECT_REQ:{local_addr[0]}:{local_addr[1]}".encode('utf-8'))

            # 稍作停顿确保接收端逻辑处理
            time.sleep(0.1)

            # 3. 发送实际消息
            s.send(msg.encode('utf-8'))
            s.close()
            print(f"[系统] P2P 消息已直接送达 {ip}:{port}")
        except Exception as e:
            sys.stdout.write('\r\033[K')
            print(f"\n[错误] P2P 连接失败: {e}")
        finally:
            sys.stdout.write("消息 >> ")
            sys.stdout.flush()

    def receive_handler(self):
        """处理来自服务器的消息（广播、名单更新、P2P信令）"""
        while True:
            try:
                data = self.client_socket.recv(4096).decode('utf-8')
                if not data: break

                # 更新在线列表
                if data.startswith("ONLINE_LIST:"):
                    raw_list = data[12:]
                    sys.stdout.write('\r\033[K')
                    print(f"\n[系统] 在线列表更新: {raw_list}")

                # 收到 P2P 信令返回
                elif data.startswith("P2P_READY:"):
                    _, target_ip, target_port, msg = data.split(":", 3)
                    # 发起直连
                    threading.Thread(target=self.p2p_send, args=(target_ip, target_port, msg)).start()

                # 普通广播
                else:
                    sys.stdout.write('\r\033[K')  # 清行
                    print(f"{data}")

                sys.stdout.write("消息 >> ")
                sys.stdout.flush()
            except:
                break

    def run(self):
        # 启动 P2P 监听
        threading.Thread(target=self.start_p2p_listener, daemon=True).start()
        time.sleep(0.1)  # 等待端口分配完成

        while True:
            try:
                self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.client_socket.connect((self.server_ip, self.server_port))

                # ① 注册：发送 P2P 接收端口给 Server
                self.client_socket.send(f"REG_P2P_PORT:{self.p2p_rcv_port}".encode('utf-8'))

                threading.Thread(target=self.receive_handler, daemon=True).start()
                print(f"[系统] 已连接。广播请直接发送消息，单独发送请使用<to:IP>msg。")

                while True:
                    msg = input("消息 >> ")
                    if msg.lower() == 'exit': return
                    # ② 广播消息直接发给 Server
                    # ③ P2P 消息格式为 <to:127.0.0.1>Hello
                    self.client_socket.send(msg.encode('utf-8'))
            except Exception as e:
                print(f"连接断开 ({e})，5秒后重连...")
                time.sleep(5)


if __name__ == "__main__":
    # 如果在不同机器运行，请修改这里的 IP
    app = ClientApp('10.192.28.30')
    app.run()