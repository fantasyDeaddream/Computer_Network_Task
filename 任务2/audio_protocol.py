"""
音频传输协议

负责音频消息的封装和解析。
"""

import json
import base64
from custom_exceptions import ProtocolError
from audio_config import MAX_FILENAME_LENGTH


class AudioProtocol:
    """音频传输协议"""
    
    @staticmethod
    def encode_message(filename: str, audio_data: bytes) -> str:
        """
        封装音频消息为JSON格式
        
        Args:
            filename: 音频文件名
            audio_data: WAV格式的音频二进制数据
            
        Returns:
            JSON格式的消息字符串
            
        Message Format:
            {
                "type": "audio",
                "filename": "recording_001.wav",
                "length": 12345,
                "data": "base64_encoded_audio_data..."
            }
            
        Raises:
            ProtocolError: 文件名长度超过限制或数据无效
        """
        # 验证文件名长度
        if len(filename) > MAX_FILENAME_LENGTH:
            raise ProtocolError(
                f"文件名长度超过限制: {len(filename)} > {MAX_FILENAME_LENGTH}"
            )
        
        # 验证文件名不为空
        if not filename:
            raise ProtocolError("文件名不能为空")
        
        # 验证音频数据不为空
        if not audio_data:
            raise ProtocolError("音频数据不能为空")
        
        # 验证音频数据类型
        if not isinstance(audio_data, bytes):
            raise ProtocolError("音频数据必须是bytes类型")
        
        try:
            # 使用base64编码二进制数据
            encoded_data = base64.b64encode(audio_data).decode('utf-8')
            
            # 构建消息字典
            message = {
                "type": "audio",
                "filename": filename,
                "length": len(audio_data),
                "data": encoded_data
            }
            
            # 转换为JSON字符串
            json_message = json.dumps(message)
            
            return json_message
            
        except Exception as e:
            raise ProtocolError(f"封装消息失败: {str(e)}")
    
    @staticmethod
    def decode_message(message: str) -> tuple[str, bytes]:
        """
        解析音频消息
        
        Args:
            message: JSON格式的消息字符串
            
        Returns:
            (filename, audio_data) - 文件名和解码后的音频二进制数据
            
        Raises:
            ProtocolError: 消息格式无效或缺少必需字段
        """
        # 验证消息不为空
        if not message:
            raise ProtocolError("消息不能为空")
        
        # 验证消息类型
        if not isinstance(message, str):
            raise ProtocolError("消息必须是字符串类型")
        
        try:
            # 解析JSON字符串
            message_dict = json.loads(message)
        except json.JSONDecodeError as e:
            raise ProtocolError(f"无效的JSON格式: {str(e)}")
        
        # 验证消息格式
        if not AudioProtocol.validate_message(message_dict):
            raise ProtocolError("消息格式验证失败")
        
        try:
            # 提取字段
            filename = message_dict["filename"]
            encoded_data = message_dict["data"]
            
            # 解码base64数据
            audio_data = base64.b64decode(encoded_data)
            
            return filename, audio_data
            
        except KeyError as e:
            raise ProtocolError(f"缺少必需字段: {str(e)}")
        except Exception as e:
            raise ProtocolError(f"解析消息失败: {str(e)}")
    
    @staticmethod
    def validate_message(message: dict) -> bool:
        """
        验证消息格式
        
        Args:
            message: 解析后的消息字典
            
        Returns:
            消息是否有效
        """
        # 验证消息类型
        if not isinstance(message, dict):
            return False
        
        # 验证必需字段存在
        required_fields = ["type", "filename", "length", "data"]
        for field in required_fields:
            if field not in message:
                return False
        
        # 验证字段类型
        if not isinstance(message["type"], str):
            return False
        
        if not isinstance(message["filename"], str):
            return False
        
        if not isinstance(message["length"], int):
            return False
        
        if not isinstance(message["data"], str):
            return False
        
        # 验证type字段值
        if message["type"] != "audio":
            return False
        
        # 验证filename不为空
        if not message["filename"]:
            return False
        
        # 验证length为非负数
        if message["length"] < 0:
            return False
        
        # 验证data不为空
        if not message["data"]:
            return False
        
        return True
