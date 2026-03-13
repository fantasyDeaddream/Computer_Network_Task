"""
音频客户端

基于任务1的TCP客户端，扩展支持音频录制、发送和播放功能。
"""

import socket
import threading
import json
import time
from datetime import datetime

from audio_recorder import AudioRecorder
from audio_player import AudioPlayer
from audio_encoder import AudioEncoder
from audio_decoder import AudioDecoder
from audio_protocol import AudioProtocol
from audio_config import (
    DEFAULT_HOST, DEFAULT_PORT, MESSAGE_DELIMITER,
    MAX_AUDIO_SIZE
)
from custom_exceptions import (
    AudioDeviceError, EncodingError, DecodingError,
    ProtocolError, PlaybackError
)


class AudioClient:
    """
    音频客户端
    
    支持文本消息和音频消息的发送与接收。
    命令：
    - /record 或 /r: 开始录音
    - /stop 或 /s: 停止录音并发送
    - /quit 或 /q: 退出程序
    - 其他文本: 发送文本消息
    """
    
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, nickname: str = None):
        """
        初始化客户端
        
        Args:
            host: 服务器地址
            port: 服务器端口
            nickname: 用户昵称
        """
        self.host = host
        self.port = port
        self.socket = None
        self.is_connected = False
        self.is_running = False
        
        # 用户信息
        self.nickname = nickname or f"User_{id(self) % 10000}"
        self.user_id = None
        
        # 音频组件
        self.recorder = AudioRecorder()
        self.player = AudioPlayer()
        self.encoder = AudioEncoder()
        self.decoder = AudioDecoder()
        
        # 录音计数器（用于生成文件名）
        self.recording_count = 0
    
    def connect(self) -> bool:
        """
        连接到服务器并完成登录
        
        Returns:
            连接是否成功
        """
        try:
            # 建立TCP连接
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            print(f"[Client] 成功连接到服务器 {self.host}:{self.port}")
            
            # 发送登录消息
            login_message = json.dumps({
                'type': 'login',
                'nickname': self.nickname
            })
            self.socket.send(login_message.encode('utf-8'))
            
            # 接收服务器分配的用户ID
            response = self.socket.recv(1024).decode('utf-8')
            response_data = json.loads(response)
            self.user_id = response_data.get('id')
            
            self.is_connected = True
            print(f"[Client] 登录成功，用户ID: {self.user_id}, 昵称: {self.nickname}")
            return True
            
        except Exception as e:
            print(f"[Client] 连接失败: {e}")
            self.is_connected = False
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
            return False
    
    def disconnect(self) -> None:
        """断开连接并发送登出消息"""
        self.is_running = False
        
        # 发送登出消息
        if self.is_connected and self.socket:
            try:
                logout_message = json.dumps({
                    'type': 'logout',
                    'nickname': self.nickname
                })
                self.socket.send((logout_message + MESSAGE_DELIMITER).encode('utf-8'))
                print(f"[Client] 已发送登出消息")
            except Exception as e:
                print(f"[Client] 发送登出消息失败: {e}")
        
        self.is_connected = False
        
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        
        print("[Client] 已断开连接")
    
    def _receive_messages_thread(self) -> None:
        """
        接收消息线程
        
        持续接收服务器转发的消息（文本或音频）。
        """
        buffer = ""
        
        while self.is_running and self.is_connected:
            try:
                # 接收数据
                data = self.socket.recv(4096).decode('utf-8')
                
                if not data:
                    # 连接断开
                    print("[Client] 服务器断开连接")
                    self.is_connected = False
                    break
                
                # 添加到缓冲区
                buffer += data
                
                # 处理所有完整的消息（以换行符分隔）
                while MESSAGE_DELIMITER in buffer:
                    message, buffer = buffer.split(MESSAGE_DELIMITER, 1)
                    self._handle_received_message(message)
                    
            except Exception as e:
                if self.is_running:
                    print(f"[Client] 接收消息错误: {e}")
                break
        
        self.is_connected = False
    
    def _handle_received_message(self, message: str) -> None:
        """
        处理接收到的消息
        
        Args:
            message: JSON格式的消息字符串
        """
        try:
            # 解析JSON消息
            msg_dict = json.loads(message)
            msg_type = msg_dict.get('type', '')
            
            if msg_type == 'audio':
                # 音频消息
                self._handle_audio_message(message)
            elif 'sender_id' in msg_dict and 'message' in msg_dict:
                # 文本广播消息（来自服务器的广播）
                sender_id = msg_dict.get('sender_id', 0)
                sender_nickname = msg_dict.get('sender_nickname', 'Unknown')
                content = msg_dict.get('message', '')
                
                if sender_id == 0:
                    # 系统消息
                    print(f"[System] {content}")
                else:
                    # 用户消息
                    print(f"[{sender_nickname}] {content}")
            else:
                print(f"[Client] 未知消息格式: {message[:100]}")
                
        except json.JSONDecodeError:
            print(f"[Client] 无效的JSON消息")
        except Exception as e:
            print(f"[Client] 处理消息错误: {e}")
    
    def _handle_audio_message(self, message: str) -> None:
        """
        处理音频消息并播放
        
        Args:
            message: JSON格式的音频消息
        """
        try:
            # 解析音频消息
            filename, wav_data = AudioProtocol.decode_message(message)
            
            print(f"[Audio] 接收到音频: {filename}")
            
            # 解码WAV数据
            audio_frames, sample_rate, channels, sample_width = \
                self.decoder.decode_wav(wav_data)
            
            # 播放音频
            print(f"[Audio] 正在播放...")
            self.player.play(audio_frames)
            print(f"[Audio] 播放完成")
            
        except ProtocolError as e:
            print(f"[Audio] 协议错误: {e}")
        except DecodingError as e:
            print(f"[Audio] 解码错误: {e}")
        except AudioDeviceError as e:
            print(f"[Audio] 音频设备错误: {e}")
        except PlaybackError as e:
            print(f"[Audio] 播放错误: {e}")
        except Exception as e:
            print(f"[Audio] 处理音频消息错误: {e}")
    
    def start_recording(self) -> None:
        """启动录音功能"""
        if self.recorder.is_recording():
            print("[Audio] 已经在录音中")
            return
        
        try:
            self.recorder.start_recording()
            print("[Audio] 开始录音... (输入 /stop 或 /s 停止)")
        except AudioDeviceError as e:
            print(f"[Audio] 无法启动录音: {e}")
            print("[Audio] 请检查麦克风设备是否可用")
    
    def stop_recording_and_send(self) -> None:
        """停止录音并发送音频"""
        if not self.recorder.is_recording():
            print("[Audio] 当前没有在录音")
            return
        
        try:
            # 停止录音
            print("[Audio] 停止录音...")
            audio_data = self.recorder.stop_recording()
            
            if not audio_data:
                print("[Audio] 录音数据为空")
                return
            
            # 编码为WAV格式
            print("[Audio] 编码音频数据...")
            wav_data = self.encoder.encode_to_wav(audio_data)
            
            # 检查大小限制
            if len(wav_data) > MAX_AUDIO_SIZE:
                print(f"[Audio] 音频文件过大 ({len(wav_data)} bytes > {MAX_AUDIO_SIZE} bytes)")
                print("[Audio] 请录制更短的音频")
                return
            
            # 生成文件名
            self.recording_count += 1
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"recording_{self.recording_count}_{timestamp}.wav"
            
            # 封装为协议消息
            print("[Audio] 封装消息...")
            message = AudioProtocol.encode_message(filename, wav_data)
            
            # 发送消息
            self._send_message(message)
            
            print(f"[Audio] 音频已发送 ({len(wav_data)} bytes)")
            
        except AudioDeviceError as e:
            print(f"[Audio] 录音设备错误: {e}")
        except EncodingError as e:
            print(f"[Audio] 编码错误: {e}")
        except ProtocolError as e:
            print(f"[Audio] 协议错误: {e}")
        except Exception as e:
            print(f"[Audio] 发送音频错误: {e}")
    
    def send_text_message(self, content: str) -> None:
        """
        发送文本消息
        
        Args:
            content: 文本内容
        """
        try:
            message = json.dumps({
                'type': 'broadcast',
                'message': content
            })
            
            self._send_message(message)
            print(f"[You] {content}")
            
        except Exception as e:
            print(f"[Client] 发送文本消息错误: {e}")
    
    def _send_message(self, message: str) -> None:
        """
        发送消息到服务器
        
        Args:
            message: JSON格式的消息字符串
            
        Raises:
            Exception: 发送失败
        """
        if not self.is_connected:
            raise Exception("未连接到服务器")
        
        try:
            # 添加换行符作为消息分隔符
            data = (message + MESSAGE_DELIMITER).encode('utf-8')
            self.socket.sendall(data)
        except Exception as e:
            self.is_connected = False
            raise Exception(f"发送失败: {e}")
    
    def run(self) -> None:
        """运行客户端主循环"""
        # 连接到服务器
        if not self.connect():
            return
        
        # 启动接收消息线程
        self.is_running = True
        receive_thread = threading.Thread(target=self._receive_messages_thread)
        receive_thread.daemon = True
        receive_thread.start()
        
        # 显示欢迎信息
        print("\n" + "="*50)
        print("欢迎使用音频通信客户端")
        print("="*50)
        print("命令:")
        print("  /record 或 /r  - 开始录音")
        print("  /stop 或 /s    - 停止录音并发送")
        print("  /quit 或 /q    - 退出程序")
        print("  其他文本       - 发送文本消息")
        print("="*50 + "\n")
        
        # 主循环：处理用户输入
        try:
            while self.is_running and self.is_connected:
                try:
                    user_input = input()
                    
                    if not user_input:
                        continue
                    
                    # 处理命令
                    if user_input in ['/record', '/r']:
                        self.start_recording()
                    
                    elif user_input in ['/stop', '/s']:
                        self.stop_recording_and_send()
                    
                    elif user_input in ['/quit', '/q']:
                        print("[Client] 正在退出...")
                        break
                    
                    else:
                        # 发送文本消息
                        self.send_text_message(user_input)
                
                except KeyboardInterrupt:
                    print("\n[Client] 收到中断信号，正在退出...")
                    break
                
        finally:
            # 清理资源
            if self.recorder.is_recording():
                try:
                    self.recorder.stop_recording()
                except:
                    pass
            
            self.disconnect()


def main():
    """主函数"""
    import sys
    
    # 解析命令行参数
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    nickname = None
    
    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:
        try:
            port = int(sys.argv[2])
        except ValueError:
            print(f"无效的端口号: {sys.argv[2]}")
            sys.exit(1)
    if len(sys.argv) > 3:
        nickname = sys.argv[3]
    
    # 如果没有提供昵称，提示用户输入
    if not nickname:
        try:
            nickname = input("请输入你的昵称: ").strip()
            if not nickname:
                nickname = None  # 使用默认昵称
        except (KeyboardInterrupt, EOFError):
            print("\n[Client] 已取消")
            sys.exit(0)
    
    # 创建并运行客户端
    client = AudioClient(host, port, nickname)
    client.run()


if __name__ == '__main__':
    main()
