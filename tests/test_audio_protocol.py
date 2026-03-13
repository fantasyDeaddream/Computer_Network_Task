"""
AudioProtocol单元测试
"""

import pytest
import json
import base64
import sys
import os

# 添加父目录到路径以便导入模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_protocol import AudioProtocol
from custom_exceptions import ProtocolError


class TestAudioProtocolEncodeMessage:
    """测试AudioProtocol.encode_message方法"""
    
    def test_encode_message_basic(self):
        """测试基本的消息封装功能"""
        filename = "test_audio.wav"
        audio_data = b'\x00\x01\x02\x03\x04\x05'
        
        result = AudioProtocol.encode_message(filename, audio_data)
        
        # 验证返回的是字符串
        assert isinstance(result, str)
        
        # 解析JSON
        message = json.loads(result)
        
        # 验证消息结构
        assert message["type"] == "audio"
        assert message["filename"] == filename
        assert message["length"] == len(audio_data)
        
        # 验证base64编码的数据
        decoded_data = base64.b64decode(message["data"])
        assert decoded_data == audio_data
    
    def test_encode_message_empty_filename(self):
        """测试空文件名应该抛出异常"""
        audio_data = b'\x00\x01\x02\x03'
        
        with pytest.raises(ProtocolError, match="filename cannot be empty"):
            AudioProtocol.encode_message("", audio_data)
    
    def test_encode_message_filename_too_long(self):
        """测试文件名超过255字符应该抛出异常"""
        filename = "a" * 256  # 256个字符
        audio_data = b'\x00\x01\x02\x03'
        
        with pytest.raises(ProtocolError, match="filename too long"):
            AudioProtocol.encode_message(filename, audio_data)
    
    def test_encode_message_empty_audio_data(self):
        """测试空音频数据应该抛出异常"""
        filename = "test.wav"
        
        with pytest.raises(ProtocolError, match="audio data cannot be empty"):
            AudioProtocol.encode_message(filename, b'')
    
    def test_encode_message_invalid_audio_data_type(self):
        """测试非bytes类型的音频数据应该抛出异常"""
        filename = "test.wav"
        
        with pytest.raises(ProtocolError, match="audio data must be bytes"):
            AudioProtocol.encode_message(filename, "not bytes")
    
    def test_encode_message_max_filename_length(self):
        """测试255字符的文件名应该成功"""
        filename = "a" * 255  # 正好255个字符
        audio_data = b'\x00\x01\x02\x03'
        
        result = AudioProtocol.encode_message(filename, audio_data)
        message = json.loads(result)
        
        assert message["filename"] == filename
    
    def test_encode_message_large_audio_data(self):
        """测试较大的音频数据"""
        filename = "large_audio.wav"
        audio_data = b'\x00' * 10000  # 10KB数据
        
        result = AudioProtocol.encode_message(filename, audio_data)
        message = json.loads(result)
        
        assert message["length"] == 10000
        decoded_data = base64.b64decode(message["data"])
        assert decoded_data == audio_data


class TestAudioProtocolDecodeMessage:
    """测试AudioProtocol.decode_message方法"""
    
    def test_decode_message_basic(self):
        """测试基本的消息解析功能"""
        filename = "test_audio.wav"
        audio_data = b'\x00\x01\x02\x03\x04\x05'
        
        # 先编码
        encoded_message = AudioProtocol.encode_message(filename, audio_data)
        
        # 再解码
        decoded_filename, decoded_data = AudioProtocol.decode_message(encoded_message)
        
        # 验证解码结果
        assert decoded_filename == filename
        assert decoded_data == audio_data
    
    def test_decode_message_empty_string(self):
        """测试空字符串应该抛出异常"""
        with pytest.raises(ProtocolError, match="message cannot be empty"):
            AudioProtocol.decode_message("")
    
    def test_decode_message_invalid_type(self):
        """测试非字符串类型应该抛出异常"""
        with pytest.raises(ProtocolError, match="message must be a string"):
            AudioProtocol.decode_message(123)
    
    def test_decode_message_invalid_json(self):
        """测试无效的JSON格式应该抛出异常"""
        with pytest.raises(ProtocolError, match="invalid JSON format"):
            AudioProtocol.decode_message("not a json string")
    
    def test_decode_message_missing_type_field(self):
        """测试缺少type字段应该抛出异常"""
        message = json.dumps({
            "filename": "test.wav",
            "length": 100,
            "data": "YWJjZA=="
        })
        
        with pytest.raises(ProtocolError, match="message validation failed"):
            AudioProtocol.decode_message(message)
    
    def test_decode_message_missing_filename_field(self):
        """测试缺少filename字段应该抛出异常"""
        message = json.dumps({
            "type": "audio",
            "length": 100,
            "data": "YWJjZA=="
        })
        
        with pytest.raises(ProtocolError, match="message validation failed"):
            AudioProtocol.decode_message(message)
    
    def test_decode_message_missing_length_field(self):
        """测试缺少length字段应该抛出异常"""
        message = json.dumps({
            "type": "audio",
            "filename": "test.wav",
            "data": "YWJjZA=="
        })
        
        with pytest.raises(ProtocolError, match="message validation failed"):
            AudioProtocol.decode_message(message)
    
    def test_decode_message_missing_data_field(self):
        """测试缺少data字段应该抛出异常"""
        message = json.dumps({
            "type": "audio",
            "filename": "test.wav",
            "length": 100
        })
        
        with pytest.raises(ProtocolError, match="message validation failed"):
            AudioProtocol.decode_message(message)
    
    def test_decode_message_invalid_base64(self):
        """测试无效的base64数据应该抛出异常"""
        message = json.dumps({
            "type": "audio",
            "filename": "test.wav",
            "length": 100,
            "data": "not valid base64!!!"
        })
        
        with pytest.raises(ProtocolError, match="failed to decode message"):
            AudioProtocol.decode_message(message)
    
    def test_decode_message_large_data(self):
        """测试较大的音频数据解码"""
        filename = "large_audio.wav"
        audio_data = b'\x00' * 10000  # 10KB数据
        
        # 编码
        encoded_message = AudioProtocol.encode_message(filename, audio_data)
        
        # 解码
        decoded_filename, decoded_data = AudioProtocol.decode_message(encoded_message)
        
        # 验证
        assert decoded_filename == filename
        assert decoded_data == audio_data
        assert len(decoded_data) == 10000


class TestAudioProtocolValidateMessage:
    """测试AudioProtocol.validate_message方法"""
    
    def test_validate_message_valid(self):
        """测试有效的消息应该返回True"""
        message = {
            "type": "audio",
            "filename": "test.wav",
            "length": 100,
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is True
    
    def test_validate_message_not_dict(self):
        """测试非字典类型应该返回False"""
        assert AudioProtocol.validate_message("not a dict") is False
        assert AudioProtocol.validate_message(123) is False
        assert AudioProtocol.validate_message([]) is False
    
    def test_validate_message_missing_type(self):
        """测试缺少type字段应该返回False"""
        message = {
            "filename": "test.wav",
            "length": 100,
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_missing_filename(self):
        """测试缺少filename字段应该返回False"""
        message = {
            "type": "audio",
            "length": 100,
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_missing_length(self):
        """测试缺少length字段应该返回False"""
        message = {
            "type": "audio",
            "filename": "test.wav",
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_missing_data(self):
        """测试缺少data字段应该返回False"""
        message = {
            "type": "audio",
            "filename": "test.wav",
            "length": 100
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_wrong_type_field_type(self):
        """测试type字段类型错误应该返回False"""
        message = {
            "type": 123,  # 应该是字符串
            "filename": "test.wav",
            "length": 100,
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_wrong_filename_field_type(self):
        """测试filename字段类型错误应该返回False"""
        message = {
            "type": "audio",
            "filename": 123,  # 应该是字符串
            "length": 100,
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_wrong_length_field_type(self):
        """测试length字段类型错误应该返回False"""
        message = {
            "type": "audio",
            "filename": "test.wav",
            "length": "100",  # 应该是整数
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_wrong_data_field_type(self):
        """测试data字段类型错误应该返回False"""
        message = {
            "type": "audio",
            "filename": "test.wav",
            "length": 100,
            "data": 123  # 应该是字符串
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_wrong_type_value(self):
        """测试type字段值不是'audio'应该返回False"""
        message = {
            "type": "text",  # 应该是'audio'
            "filename": "test.wav",
            "length": 100,
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_empty_filename(self):
        """测试空文件名应该返回False"""
        message = {
            "type": "audio",
            "filename": "",  # 空字符串
            "length": 100,
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_negative_length(self):
        """测试负数长度应该返回False"""
        message = {
            "type": "audio",
            "filename": "test.wav",
            "length": -1,  # 负数
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_empty_data(self):
        """测试空数据应该返回False"""
        message = {
            "type": "audio",
            "filename": "test.wav",
            "length": 100,
            "data": ""  # 空字符串
        }
        
        assert AudioProtocol.validate_message(message) is False
    
    def test_validate_message_zero_length(self):
        """测试长度为0应该返回True（有效的边界情况）"""
        message = {
            "type": "audio",
            "filename": "test.wav",
            "length": 0,
            "data": "YWJjZA=="
        }
        
        assert AudioProtocol.validate_message(message) is True
