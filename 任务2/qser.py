"""
音频服务器 (qser.py)

基于任务1的TCP服务器，扩展支持音频消息的接收和广播。
"""

import socket
import threading
import json
from datetime import datetime
from audio_protocol import AudioProtocol
from custom_exceptions import ProtocolError
from audio_config import DEFAULT_PORT


class AudioServer:
    """音频服务器"""

    def __init__(self, host: str = '0.0.0.0', port: int = DEFAULT_PORT):
        """
        初始化服务器
        
        Args:
            host: 监听地址
            port: 监听端口
        """
        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.__host = host
        self.__port = port
        self.__connections = list()
        self.__nicknames = list()
        self.__lock = threading.Lock()  # 用于保护连接列表的线程锁

    def __log(self, message: str, level: str = 'INFO'):
        """
        记录日志到控制台
        
        Args:
            message: 日志消息
            level: 日志级别
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f'[{timestamp}] {level}: {message}')

    def __user_thread(self, user_id: int):
        """
        用户子线程，处理单个客户端连接
        
        Args:
            user_id: 用户ID
        """
        connection = self.__connections[user_id]
        nickname = self.__nicknames[user_id]
        
        self.__log(f'用户 {user_id} {nickname} 加入聊天室')
        self.__broadcast(message=f'用户 {nickname}({user_id}) 加入聊天室')

        # 侦听客户端消息
        buffer = ''
        while True:
            try:
                # 接收数据
                data = connection.recv(4096).decode('utf-8')
                
                if not data:
                    # 连接关闭
                    self.__log(f'用户 {user_id} {nickname} 连接关闭')
                    break
                
                # 将接收到的数据添加到缓冲区
                buffer += data
                
                # 处理缓冲区中的完整消息（以换行符分隔）
                while '\n' in buffer:
                    # 提取一条完整消息
                    message, buffer = buffer.split('\n', 1)
                    
                    if message.strip():
                        self.__handle_message(user_id, message.strip())
                        
            except ConnectionResetError:
                self.__log(f'用户 {user_id} {nickname} 连接重置', 'WARNING')
                break
            except Exception as e:
                self.__log(f'处理用户 {user_id} 消息时发生错误: {str(e)}', 'ERROR')
                break
        
        # 清理连接
        self.__cleanup_connection(user_id, nickname)

    def __handle_message(self, user_id: int, message: str):
        """
        处理接收到的消息
        
        Args:
            user_id: 发送者用户ID
            message: 消息内容
        """
        try:
            # 解析JSON消息
            obj = json.loads(message)
            message_type = obj.get('type', '')
            
            if message_type == 'audio':
                # 处理音频消息
                self.__handle_audio_message(user_id, message)
            elif message_type == 'broadcast':
                # 处理文本广播消息（复用任务1功能）
                self.__broadcast(user_id, obj.get('message', ''))
            elif message_type == 'logout':
                # 处理登出消息
                nickname = self.__nicknames[user_id]
                self.__log(f'用户 {user_id} {nickname} 退出聊天室')
                self.__broadcast(message=f'用户 {nickname}({user_id}) 退出聊天室')
                self.__connections[user_id].close()
                self.__connections[user_id] = None
                self.__nicknames[user_id] = None
            else:
                self.__log(f'未知消息类型: {message_type}', 'WARNING')
                
        except json.JSONDecodeError as e:
            self.__log(f'无法解析JSON数据: {str(e)}', 'ERROR')
        except Exception as e:
            self.__log(f'处理消息时发生错误: {str(e)}', 'ERROR')

    def __handle_audio_message(self, user_id: int, message: str):
        """
        处理音频消息
        
        Args:
            user_id: 发送者用户ID
            message: 音频消息JSON字符串
        """
        try:
            # 验证音频消息格式
            filename, audio_data = AudioProtocol.decode_message(message)
            
            nickname = self.__nicknames[user_id]
            self.__log(f'接收到用户 {user_id} {nickname} 的音频消息: {filename} ({len(audio_data)} bytes)')
            
            # 广播音频消息给其他客户端
            self.__broadcast_audio(user_id, message)
            
        except ProtocolError as e:
            self.__log(f'音频消息格式错误: {str(e)}', 'ERROR')
        except Exception as e:
            self.__log(f'处理音频消息时发生错误: {str(e)}', 'ERROR')

    def __broadcast(self, user_id: int = 0, message: str = ''):
        """
        广播文本消息
        
        Args:
            user_id: 用户ID（0为系统消息）
            message: 广播内容
        """
        with self.__lock:
            for i in range(1, len(self.__connections)):
                if user_id != i and self.__connections[i]:
                    try:
                        self.__connections[i].send(json.dumps({
                            'sender_id': user_id,
                            'sender_nickname': self.__nicknames[user_id],
                            'message': message
                        }).encode() + b'\n')
                    except Exception as e:
                        self.__log(f'向用户 {i} 发送消息失败: {str(e)}', 'ERROR')

    def __broadcast_audio(self, sender_id: int, audio_message: str):
        """
        广播音频消息给所有客户端（排除发送者）
        
        Args:
            sender_id: 发送者用户ID
            audio_message: 音频消息JSON字符串
        """
        with self.__lock:
            for i in range(1, len(self.__connections)):
                # 排除发送者本身
                if i != sender_id and self.__connections[i]:
                    try:
                        # 发送音频消息，添加换行符作为分隔符
                        self.__connections[i].send(audio_message.encode('utf-8') + b'\n')
                    except Exception as e:
                        self.__log(f'向用户 {i} 广播音频消息失败: {str(e)}', 'ERROR')
                        # 标记连接为失效
                        self.__connections[i] = None

    def __cleanup_connection(self, user_id: int, nickname: str):
        """
        清理失效的客户端连接
        
        Args:
            user_id: 用户ID
            nickname: 用户昵称
        """
        with self.__lock:
            if self.__connections[user_id]:
                try:
                    self.__connections[user_id].close()
                except Exception:
                    pass
                self.__connections[user_id] = None
                self.__nicknames[user_id] = None
        
        self.__log(f'用户 {user_id} {nickname} 连接已清理')
        self.__broadcast(message=f'用户 {nickname}({user_id}) 离开聊天室')

    def __wait_for_login(self, connection: socket.socket):
        """
        等待客户端登录
        
        Args:
            connection: 客户端连接
        """
        try:
            buffer = connection.recv(1024).decode('utf-8')
            obj = json.loads(buffer)
            
            if obj['type'] == 'login':
                nickname = obj['nickname']
                
                with self.__lock:
                    self.__connections.append(connection)
                    self.__nicknames.append(nickname)
                    user_id = len(self.__connections) - 1
                
                # 发送用户ID给客户端
                connection.send(json.dumps({
                    'id': user_id
                }).encode())
                
                self.__log(f'新用户登录: {nickname} (ID: {user_id})')
                
                # 为该用户创建处理线程
                thread = threading.Thread(target=self.__user_thread, args=(user_id,))
                thread.daemon = True
                thread.start()
            else:
                self.__log(f'无法解析登录数据包: {connection.getpeername()}', 'WARNING')
                connection.close()
                
        except json.JSONDecodeError as e:
            self.__log(f'登录数据JSON解析失败: {str(e)}', 'ERROR')
            connection.close()
        except Exception as e:
            self.__log(f'处理登录请求时发生错误: {str(e)}', 'ERROR')
            connection.close()

    def start(self):
        """启动服务器"""
        try:
            # 绑定端口
            self.__socket.bind((self.__host, self.__port))
            # 启用监听
            self.__socket.listen(10)
            
            self.__log(f'服务器正在运行，监听 {self.__host}:{self.__port}')
            
            # 初始化连接列表
            self.__connections.clear()
            self.__nicknames.clear()
            self.__connections.append(None)
            self.__nicknames.append('System')
            
            # 开始侦听连接
            while True:
                connection, address = self.__socket.accept()
                self.__log(f'收到新连接: {address}')
                
                # 为每个新连接创建登录处理线程
                thread = threading.Thread(target=self.__wait_for_login, args=(connection,))
                thread.daemon = True
                thread.start()
                
        except KeyboardInterrupt:
            self.__log('服务器收到中断信号，正在关闭...', 'INFO')
        except Exception as e:
            self.__log(f'服务器启动失败: {str(e)}', 'ERROR')
        finally:
            self.__socket.close()
            self.__log('服务器已关闭')


def main():
    """主函数"""
    server = AudioServer()
    server.start()


if __name__ == '__main__':
    main()
