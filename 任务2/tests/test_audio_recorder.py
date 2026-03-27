"""
AudioRecorder单元测试

测试音频录制器的基本功能。
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import time
import sys
import os

# Mock pyaudio before importing audio_recorder
sys.modules['pyaudio'] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from audio_recorder import AudioRecorder
from custom_exceptions import AudioDeviceError
from audio_config import SAMPLE_RATE, CHANNELS, CHUNK_SIZE, AUDIO_FORMAT


class TestAudioRecorder:
    """AudioRecorder类的单元测试"""
    
    @patch('audio_recorder.pyaudio.PyAudio')
    def test_initialization(self, mock_pyaudio_class):
        """测试AudioRecorder初始化"""
        recorder = AudioRecorder()
        
        assert recorder.sample_rate == SAMPLE_RATE
        assert recorder.channels == CHANNELS
        assert recorder.chunk_size == CHUNK_SIZE
        assert recorder.format == AUDIO_FORMAT
        assert not recorder.is_recording()
    
    @patch('audio_recorder.pyaudio.PyAudio')
    def test_start_recording_success(self, mock_pyaudio_class):
        """测试成功启动录音"""
        # 设置mock
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        
        recorder = AudioRecorder()
        recorder.start_recording()
        
        # 验证pyaudio被正确调用
        mock_pyaudio_class.assert_called_once()
        mock_pyaudio.open.assert_called_once_with(
            format=AUDIO_FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )
        
        # 验证录音状态
        assert recorder.is_recording()

    @patch('audio_recorder.pyaudio.PyAudio')
    def test_start_recording_device_error(self, mock_pyaudio_class):
        """测试音频设备不可用时抛出AudioDeviceError"""
        # 模拟设备初始化失败
        mock_pyaudio_class.side_effect = Exception("No audio device found")
        
        recorder = AudioRecorder()
        
        with pytest.raises(AudioDeviceError) as exc_info:
            recorder.start_recording()
        
        assert "无法初始化音频设备" in str(exc_info.value)
        assert not recorder.is_recording()
    
    @patch('audio_recorder.pyaudio.PyAudio')
    def test_stop_recording_returns_data(self, mock_pyaudio_class):
        """测试停止录音返回音频数据"""
        # 设置mock
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        
        # 模拟音频数据 - 返回几次数据后停止
        call_count = [0]
        def read_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 5:
                return b'\x00\x01' * 512
            else:
                # 模拟流结束
                time.sleep(0.01)
                return b'\x00\x01' * 512
        
        mock_stream.read.side_effect = read_side_effect
        
        recorder = AudioRecorder()
        recorder.start_recording()
        
        # 等待一些数据被录制
        time.sleep(0.2)
        
        # 停止录音
        audio_data = recorder.stop_recording()
        
        # 验证返回了数据
        assert isinstance(audio_data, bytes)
        assert len(audio_data) > 0
        assert not recorder.is_recording()
        
        # 验证资源被清理
        mock_stream.stop_stream.assert_called_once()
        mock_stream.close.assert_called_once()
        mock_pyaudio.terminate.assert_called_once()
    
    @patch('audio_recorder.pyaudio.PyAudio')
    def test_stop_recording_without_start(self, mock_pyaudio_class):
        """测试未开始录音时调用stop_recording返回空数据"""
        recorder = AudioRecorder()
        
        audio_data = recorder.stop_recording()
        
        assert audio_data == b''
        assert not recorder.is_recording()
    
    @patch('audio_recorder.pyaudio.PyAudio')
    def test_is_recording_status(self, mock_pyaudio_class):
        """测试is_recording方法正确返回录音状态"""
        # 设置mock
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        
        # 模拟持续返回数据
        mock_stream.read.return_value = b'\x00' * 1024
        
        recorder = AudioRecorder()
        
        # 初始状态
        assert not recorder.is_recording()
        
        # 开始录音后
        recorder.start_recording()
        time.sleep(0.05)  # 等待线程启动
        assert recorder.is_recording()
        
        # 停止录音后
        recorder.stop_recording()
        assert not recorder.is_recording()

    @patch('audio_recorder.pyaudio.PyAudio')
    @patch('audio_recorder.MAX_DURATION', 0.2)  # 设置短的最大时长用于测试
    def test_max_duration_limit(self, mock_pyaudio_class):
        """测试60秒最大时长限制（使用短时长测试）"""
        # 设置mock
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        mock_stream.read.return_value = b'\x00' * 1024
        
        recorder = AudioRecorder()
        recorder.start_recording()
        
        assert recorder.is_recording()
        
        # 等待超过最大时长
        time.sleep(0.3)
        
        # 录音应该自动停止
        assert not recorder.is_recording()
        
        # 停止录音应该返回数据
        audio_data = recorder.stop_recording()
        assert isinstance(audio_data, bytes)
    
    @patch('audio_recorder.pyaudio.PyAudio')
    def test_multiple_start_calls(self, mock_pyaudio_class):
        """测试多次调用start_recording的幂等性"""
        # 设置mock
        mock_pyaudio = Mock()
        mock_stream = Mock()
        mock_pyaudio_class.return_value = mock_pyaudio
        mock_pyaudio.open.return_value = mock_stream
        mock_stream.read.return_value = b'\x00' * 1024
        
        recorder = AudioRecorder()
        
        # 第一次启动
        recorder.start_recording()
        time.sleep(0.05)  # 等待线程启动
        assert recorder.is_recording()
        
        # 第二次启动应该被忽略
        recorder.start_recording()
        assert recorder.is_recording()
        
        # pyaudio应该只被初始化一次
        assert mock_pyaudio_class.call_count == 1
        
        recorder.stop_recording()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
