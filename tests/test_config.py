"""
配置和异常类的基础测试

验证项目结构和测试框架正常工作。
"""

import pytest
from hypothesis import given, strategies as st

# 导入配置和异常
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_config import (
    SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH, AUDIO_FORMAT,
    CHUNK_SIZE, MAX_DURATION, MAX_FILENAME_LENGTH, MAX_AUDIO_SIZE
)
from custom_exceptions import (
    AudioDeviceError, EncodingError, DecodingError,
    ProtocolError, PlaybackError
)


@pytest.mark.unit
class TestAudioConfig:
    """测试音频配置常量"""
    
    def test_sample_rate(self):
        """测试采样率配置"""
        assert SAMPLE_RATE == 16000
    
    def test_channels(self):
        """测试声道数配置"""
        assert CHANNELS == 1
    
    def test_sample_width(self):
        """测试采样宽度配置"""
        assert SAMPLE_WIDTH == 2
    
    def test_chunk_size(self):
        """测试缓冲区大小配置"""
        assert CHUNK_SIZE == 1024
    
    def test_max_duration(self):
        """测试最大录音时长配置"""
        assert MAX_DURATION == 60
    
    def test_max_filename_length(self):
        """测试文件名最大长度配置"""
        assert MAX_FILENAME_LENGTH == 255
    
    def test_max_audio_size(self):
        """测试最大音频大小配置"""
        assert MAX_AUDIO_SIZE == 10 * 1024 * 1024


@pytest.mark.unit
class TestCustomExceptions:
    """测试自定义异常类"""
    
    def test_audio_device_error(self):
        """测试AudioDeviceError可以正常抛出和捕获"""
        with pytest.raises(AudioDeviceError):
            raise AudioDeviceError("测试错误")
    
    def test_encoding_error(self):
        """测试EncodingError可以正常抛出和捕获"""
        with pytest.raises(EncodingError):
            raise EncodingError("测试错误")
    
    def test_decoding_error(self):
        """测试DecodingError可以正常抛出和捕获"""
        with pytest.raises(DecodingError):
            raise DecodingError("测试错误")
    
    def test_protocol_error(self):
        """测试ProtocolError可以正常抛出和捕获"""
        with pytest.raises(ProtocolError):
            raise ProtocolError("测试错误")
    
    def test_playback_error(self):
        """测试PlaybackError可以正常抛出和捕获"""
        with pytest.raises(PlaybackError):
            raise PlaybackError("测试错误")
    
    def test_exception_inheritance(self):
        """测试所有自定义异常都继承自Exception"""
        assert issubclass(AudioDeviceError, Exception)
        assert issubclass(EncodingError, Exception)
        assert issubclass(DecodingError, Exception)
        assert issubclass(ProtocolError, Exception)
        assert issubclass(PlaybackError, Exception)


@pytest.mark.property
class TestHypothesisSetup:
    """测试Hypothesis框架配置"""
    
    @given(st.integers(min_value=0, max_value=100))
    def test_hypothesis_basic(self, value):
        """基础属性测试：验证Hypothesis正常工作"""
        assert value >= 0
        assert value <= 100
    
    @given(st.text(min_size=1, max_size=MAX_FILENAME_LENGTH))
    def test_filename_length_property(self, filename):
        """属性测试：文件名长度限制"""
        assert len(filename) <= MAX_FILENAME_LENGTH
