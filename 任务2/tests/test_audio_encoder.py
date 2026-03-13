"""
AudioEncoder单元测试
"""

import pytest
import wave
from io import BytesIO
from audio_encoder import AudioEncoder
from custom_exceptions import EncodingError
from audio_config import SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH


class TestAudioEncoder:
    """AudioEncoder测试类"""
    
    def test_encode_to_wav_basic(self):
        """测试基本的WAV编码功能"""
        # 创建一些测试音频数据（1024字节的静音）
        audio_data = b'\x00' * 1024
        
        # 编码为WAV格式
        wav_data = AudioEncoder.encode_to_wav(audio_data)
        
        # 验证返回的是bytes类型
        assert isinstance(wav_data, bytes)
        
        # 验证数据不为空
        assert len(wav_data) > 0
        
        # 验证数据大于原始数据（因为包含了WAV头部）
        assert len(wav_data) > len(audio_data)
    
    def test_encode_to_wav_header_format(self):
        """测试WAV头部格式是否正确"""
        audio_data = b'\x00' * 1024
        wav_data = AudioEncoder.encode_to_wav(audio_data)
        
        # WAV文件应该以"RIFF"开头
        assert wav_data[:4] == b'RIFF'
        
        # 偏移8字节处应该是"WAVE"标识
        assert wav_data[8:12] == b'WAVE'
    
    def test_encode_to_wav_parameters(self):
        """测试编码后的音频参数是否正确"""
        audio_data = b'\x00' * 2048
        wav_data = AudioEncoder.encode_to_wav(audio_data)
        
        # 使用wave库解析验证参数
        wav_buffer = BytesIO(wav_data)
        with wave.open(wav_buffer, 'rb') as wav_file:
            assert wav_file.getnchannels() == CHANNELS
            assert wav_file.getsampwidth() == SAMPLE_WIDTH
            assert wav_file.getframerate() == SAMPLE_RATE
    
    def test_encode_to_wav_custom_parameters(self):
        """测试使用自定义参数编码"""
        audio_data = b'\x00' * 1024
        custom_rate = 8000
        custom_channels = 2
        custom_width = 1
        
        wav_data = AudioEncoder.encode_to_wav(
            audio_data,
            sample_rate=custom_rate,
            channels=custom_channels,
            sample_width=custom_width
        )
        
        # 验证自定义参数
        wav_buffer = BytesIO(wav_data)
        with wave.open(wav_buffer, 'rb') as wav_file:
            assert wav_file.getnchannels() == custom_channels
            assert wav_file.getsampwidth() == custom_width
            assert wav_file.getframerate() == custom_rate
    
    def test_encode_to_wav_empty_data(self):
        """测试空数据应该抛出EncodingError"""
        with pytest.raises(EncodingError) as exc_info:
            AudioEncoder.encode_to_wav(b'')
        
        assert "不能为空" in str(exc_info.value)
    
    def test_encode_to_wav_invalid_type(self):
        """测试无效数据类型应该抛出EncodingError"""
        with pytest.raises(EncodingError) as exc_info:
            AudioEncoder.encode_to_wav("not bytes")
        
        assert "必须是bytes类型" in str(exc_info.value)
    
    def test_encode_to_wav_data_integrity(self):
        """测试编码后数据可以被正确解码"""
        # 创建一些非零的测试数据
        audio_data = bytes(range(256)) * 4  # 1024字节
        
        wav_data = AudioEncoder.encode_to_wav(audio_data)
        
        # 解码并验证数据完整性
        wav_buffer = BytesIO(wav_data)
        with wave.open(wav_buffer, 'rb') as wav_file:
            decoded_data = wav_file.readframes(wav_file.getnframes())
            assert decoded_data == audio_data
    
    def test_encode_to_wav_large_data(self):
        """测试编码大量音频数据"""
        # 创建1MB的音频数据
        audio_data = b'\x00' * (1024 * 1024)
        
        wav_data = AudioEncoder.encode_to_wav(audio_data)
        
        # 验证编码成功
        assert isinstance(wav_data, bytes)
        assert len(wav_data) > len(audio_data)
        
        # 验证可以正确解析
        wav_buffer = BytesIO(wav_data)
        with wave.open(wav_buffer, 'rb') as wav_file:
            assert wav_file.getnframes() == len(audio_data) // SAMPLE_WIDTH
