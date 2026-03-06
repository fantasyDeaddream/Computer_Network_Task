import socket

# 创建TCP socket
client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# 连接服务端
server_address = ('127.0.0.1', 8888)  # 若服务端在远程，替换为服务端IP
client_socket.connect(server_address)
print("已连接到服务端")

try:
    while True:
        # 发送数据
        message = input("输入消息 (输入 'exit' 退出): ")
        if message.lower() == 'exit':
            break
        client_socket.send(message.encode('utf-8'))

        # 接收服务端回复
        response = client_socket.recv(1024)
        print(f"服务端回复: {response.decode('utf-8')}")
except Exception as e:
    print(f"错误: {e}")
finally:
    client_socket.close()  # 关闭连接
