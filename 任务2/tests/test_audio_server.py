"""
音频服务器单元测试
"""

import pytest
import socket
import json
import threading
import time
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# 添加父目录到路径以便导入模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from qser import AudioServer
from audio_protocol import AudioProtocol
from audio_encoder import AudioEncoder
from custom_exceptions import ProtocolError


class TestAudioServer:
    """测试音频服务器"""
    
    def test_server_initialization(self):
        """测试服务器初始化"""
        server = AudioServer(host='127.0.0.1', port=8881)
        assert server is not None
    
    def test_server_broadcast_excludes_sender(self):
        """测试服务器广播排除发送者 - Property 9"""
        server = AudioServer()
        
        # 模拟连接列表
        mock_conn1 = Mock()
        mock_conn2 = Mock()
        mock_conn3 = Mock()
        
        server._AudioServer__connections = [None, mock_conn1, mock_conn2, mock_conn3]
        server._AudioServer__nicknames = ['System', 'User1', 'User2', 'User3']
        
        # 用户1发送音频消息
        audio_message = '{"type":"audio","filename":"test.wav","length":100,"data":"dGVzdA=="}'
        server._AudioServer__broadcast_audio(1, audio_message)
        
        # 验证：用户1不应该收到消息
        mock_conn1.send.assert_not_called()
        
        # 验证：用户2和用户3应该收到消息
        mock_conn2.send.assert_called_once()
        mock_conn3.send.assert_called_once()
        
        # 验证消息内容包含换行符
        sent_data = mock_conn2.send.call_args[0][0]
        assert sent_data.endswith(b'\n')
        assert audio_message.encode('utf-8') in sent_data
    
    def test_server_handles_protocol_error(self):
        """测试服务器处理协议错误 - Property 14"""
        server = AudioServer()
        
        # 模拟无效的音频消息
        invalid_message = '{"type":"audio","filename":"test.wav"}'  # 缺少必需字段
        
        # 应该不会抛出异常，而是记录错误并继续
        try:
            server._AudioServer__handle_audio_message(1, invalid_message)
            # 如果没有抛出异常，测试通过
            assert True
        except Exception as e:
            pytest.fail(f"服务器应该优雅处理错误，但抛出了异常: {e}")
    
    def test_server_handles_json_decode_error(self):
        """测试服务器处理JSON解析错误"""
        server = AudioServer()
        
        # 模拟无效的JSON消息
        invalid_json = "not a json string"
        
        # 应该不会抛出异常
        try:
            server._AudioServer__handle_message(1, invalid_json)
            assert True
        except Exception as e:
            pytest.fail(f"服务器应该优雅处理JSON错误，但抛出了异常: {e}")
    
    def test_server_cleanup_connection(self):
        """测试服务器清理失效连接"""
        server = AudioServer()
        
        # 模拟连接
        mock_conn = Mock()
        server._AudioServer__connections = [None, mock_conn]
        server._AudioServer__nicknames = ['System', 'TestUser']
        
        # 清理连接
        server._AudioServer__cleanup_connection(1, 'TestUser')
        
        # 验证连接被关闭
        mock_conn.close.assert_called_once()
        
        # 验证连接被设置为None
        assert server._AudioServer__connections[1] is None
        assert server._AudioServer__nicknames[1] is None
    
    def test_server_message_delimiter(self):
        """测试服务器消息分隔符 - Property 8"""
        server = AudioServer()
        
        mock_conn = Mock()
        server._AudioServer__connections = [None, mock_conn]
        server._AudioServer__nicknames = ['System', 'User1']
        
        # 发送文本消息
        server._AudioServer__broadcast(0, 'Test message')
        
        # 验证消息以换行符结尾
        sent_data = mock_conn.send.call_args[0][0]
        assert sent_data.endswith(b'\n')
    
    def test_server_handles_connection_error(self):
        """测试服务器处理连接错误"""
        server = AudioServer()
        
        # 模拟一个会抛出异常的连接
        mock_conn = Mock()
        mock_conn.send.side_effect = Exception("Connection error")
        
        server._AudioServer__connections = [None, mock_conn]
        server._AudioServer__nicknames = ['System', 'User1']
        
        # 应该不会抛出异常
        try:
            server._AudioServer__broadcast(0, 'Test message')
            assert True
        except Exception as e:
            pytest.fail(f"服务器应该优雅处理连接错误，但抛出了异常: {e}")


class TestAudioServerIntegration:
    """音频服务器集成测试"""
    
    def test_audio_message_format_validation(self):
        """测试音频消息格式验证"""
        server = AudioServer()
        
        # 创建有效的音频消息
        test_audio_data = b'\x00\x01\x02\x03' * 100
        wav_data = AudioEncoder.encode_to_wav(test_audio_data)
        audio_message = AudioProtocol.encode_message('test.wav', wav_data)
        
        # 模拟连接
        server._AudioServer__connections = [None, Mock()]
        server._AudioServer__nicknames = ['System', 'TestUser']
        
        # 应该能够成功处理
        try:
            server._AudioServer__handle_audio_message(1, audio_message)
            assert True
        except Exception as e:
            pytest.fail(f"服务器应该能够处理有效的音频消息，但抛出了异常: {e}")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
