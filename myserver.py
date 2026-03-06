import socket

# 创建TCP socket
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# 绑定IP和端口（0.0.0.0表示监听所有网络接口）
server_socket.bind(('0.0.0.0', 8888))

# 开始监听，设置最大连接数
server_socket.listen(5)
print("服务端启动，等待客户端连接...")

while True:
    # 接受客户端连接
    client_socket, addr = server_socket.accept()
    print(f"客户端 {addr} 已连接")

    try:
        while True:
            # 接收客户端数据（最大1024字节）
            data = client_socket.recv(1024)
            if not data:
                break  # 客户端断开连接
            print(f"收到消息: {data.decode('utf-8')}")

            # 回复客户端
            response = "服务端已收到！"
            client_socket.send(response.encode('utf-8'))
    except Exception as e:
        print(f"错误: {e}")
    finally:
        client_socket.close()  # 关闭客户端连接
