"""
AudioPlayer单元测试

测试音频播放器的功能和错误处理。
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Mock pyaudio before importing audio_player
sys.modules['pyaudio'] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from audio_player import AudioPlayer
from custom_exceptions import AudioDeviceError, PlaybackError
from audio_config import SAMPLE_RATE, CHANNELS, AUDIO_FORMAT


class TestAudioPlayer:
    """AudioPlayer类的单元测试"""
    
    def test_init_default_parameters(self):
        """测试使用默认参数初始化"""
        player = AudioPlayer()
        
        assert player.sample_rate == SAMPLE_RATE
        assert player.channels == CHANNELS
        assert player.format == AUDIO_FORMAT
        assert player.pyaudio_instance is None
        assert player.stream is None
    
    def test_init_custom_parameters(self):
        """测试使用自定义参数初始化"""
        player = AudioPlayer(sample_rate=44100, channels=2, format=8)
        
        assert player.sample_rate == 44100
        assert player.channels == 2
        assert player.format == 8
    
    @patch('audio_player.pyaudio.PyAudio')
    def test_play_success(self, mock_pyaudio_class):
        """测试正常播放流程"""
        # 设置mock
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        
        # 创建测试数据
        audio_data = b'\x00\x01' * 1024  # 2048字节的测试数据
        
        # 播放音频
        player = AudioPlayer()
        player.play(audio_data)
        
        # 验证pyaudio被正确调用
        mock_pyaudio_class.assert_called_once()
        mock_pyaudio.open.assert_called_once_with(
            format=AUDIO_FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            output=True,
            frames_per_buffer=player.chunk_size
        )
        
        # 验证音频数据被写入
        assert mock_stream.write.called
        
        # 验证资源被清理
        mock_stream.stop_stream.assert_called_once()
        mock_stream.close.assert_called_once()
        mock_pyaudio.terminate.assert_called_once()
    
    @patch('audio_player.pyaudio.PyAudio')
    def test_play_empty_data(self, mock_pyaudio_class):
        """测试播放空数据抛出PlaybackError"""
        player = AudioPlayer()
        
        with pytest.raises(PlaybackError) as exc_info:
            player.play(b"")
        
        assert "音频数据为空" in str(exc_info.value)
        # 确保pyaudio没有被初始化
        mock_pyaudio_class.assert_not_called()
    
    @patch('audio_player.pyaudio.PyAudio')
    def test_play_device_error(self, mock_pyaudio_class):
        """测试音频设备不可用时抛出AudioDeviceError"""
        # 设置mock使open抛出异常
        mock_pyaudio = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.side_effect = Exception("设备不可用")
        
        audio_data = b'\x00\x01' * 1024
        
        player = AudioPlayer()
        with pytest.raises(AudioDeviceError) as exc_info:
            player.play(audio_data)
        
        assert "无法初始化音频输出设备" in str(exc_info.value)
        
        # 验证资源被清理
        mock_pyaudio.terminate.assert_called_once()
    
    @patch('audio_player.pyaudio.PyAudio')
    def test_play_playback_error(self, mock_pyaudio_class):
        """测试播放过程中发生错误抛出PlaybackError"""
        # 设置mock使write抛出异常
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        mock_stream.write.side_effect = Exception("播放失败")
        
        audio_data = b'\x00\x01' * 1024
        
        player = AudioPlayer()
        with pytest.raises(PlaybackError) as exc_info:
            player.play(audio_data)
        
        assert "播放音频时发生错误" in str(exc_info.value)
        
        # 验证资源被清理
        mock_stream.stop_stream.assert_called_once()
        mock_stream.close.assert_called_once()
        mock_pyaudio.terminate.assert_called_once()
    
    @patch('audio_player.pyaudio.PyAudio')
    def test_play_chunks_audio_data(self, mock_pyaudio_class):
        """测试音频数据被正确分块播放"""
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        
        # 创建大于chunk_size的测试数据
        chunk_size = 1024
        audio_data = b'\x00\x01' * (chunk_size * 2 + 512)  # 2.5个chunk = 5120字节
        
        player = AudioPlayer()
        player.play(audio_data)
        
        # 验证write被调用了正确的次数
        # 5120字节 / 1024字节每块 = 5块
        expected_calls = (len(audio_data) + chunk_size - 1) // chunk_size
        assert mock_stream.write.call_count == expected_calls
    
    @patch('audio_player.pyaudio.PyAudio')
    def test_cleanup_handles_exceptions(self, mock_pyaudio_class):
        """测试清理过程中的异常被正确处理"""
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        
        # 设置清理时抛出异常
        mock_stream.stop_stream.side_effect = Exception("清理失败")
        mock_stream.close.side_effect = Exception("关闭失败")
        mock_pyaudio.terminate.side_effect = Exception("终止失败")
        
        audio_data = b'\x00\x01' * 1024
        
        player = AudioPlayer()
        # 应该不抛出异常，清理错误被忽略
        player.play(audio_data)
    
    @patch('audio_player.pyaudio.PyAudio')
    def test_play_with_custom_parameters(self, mock_pyaudio_class):
        """测试使用自定义参数播放"""
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        
        # 使用自定义参数
        player = AudioPlayer(sample_rate=44100, channels=2, format=8)
        audio_data = b'\x00\x01' * 1024
        
        player.play(audio_data)
        
        # 验证使用了自定义参数
        mock_pyaudio.open.assert_called_once_with(
            format=8,
            channels=2,
            rate=44100,
            output=True,
            frames_per_buffer=player.chunk_size
        )
