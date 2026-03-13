"""
AudioDecoder单元测试
"""

import pytest
import wave
from io import BytesIO
from audio_decoder import AudioDecoder
from audio_encoder import AudioEncoder
from custom_exceptions import DecodingError
from audio_config import SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH


class TestAudioDecoder:
    """AudioDecoder测试类"""
    
    def test_decode_wav_basic(self):
        """测试基本的WAV解码功能"""
        # 首先创建一个有效的WAV数据
        audio_data = b'\x00' * 1024
        wav_data = AudioEncoder.encode_to_wav(audio_data)
        
        # 解码WAV数据
        decoded_frames, sample_rate, channels, sample_width = AudioDecoder.decode_wav(wav_data)
        
        # 验证返回的数据
        assert isinstance(decoded_frames, bytes)
        assert len(decoded_frames) > 0
        assert sample_rate == SAMPLE_RATE
        assert channels == CHANNELS
        assert sample_width == SAMPLE_WIDTH
    
    def test_decode_wav_data_integrity(self):
        """测试解码后的数据与原始数据一致"""
        # 创建一些非零的测试数据
        original_data = bytes(range(256)) * 4  # 1024字节
        
        # 编码
        wav_data = AudioEncoder.encode_to_wav(original_data)
        
        # 解码
        decoded_frames, _, _, _ = AudioDecoder.decode_wav(wav_data)
        
        # 验证数据完整性
        assert decoded_frames == original_data
    
    def test_decode_wav_round_trip(self):
        """测试编码-解码往返一致性"""
        # 创建测试数据
        audio_data = b'\x01\x02\x03\x04' * 256  # 1024字节
        
        # 编码
        wav_data = AudioEncoder.encode_to_wav(audio_data)
        
        # 解码
        decoded_frames, sample_rate, channels, sample_width = AudioDecoder.decode_wav(wav_data)
        
        # 验证往返一致性
        assert decoded_frames == audio_data
        assert sample_rate == SAMPLE_RATE
        assert channels == CHANNELS
        assert sample_width == SAMPLE_WIDTH
    
    def test_decode_wav_empty_data(self):
        """测试空数据应该抛出DecodingError"""
        with pytest.raises(DecodingError) as exc_info:
            AudioDecoder.decode_wav(b'')
        
        assert "不能为空" in str(exc_info.value)
    
    def test_decode_wav_invalid_type(self):
        """测试无效数据类型应该抛出DecodingError"""
        with pytest.raises(DecodingError) as exc_info:
            AudioDecoder.decode_wav("not bytes")
        
        assert "必须是bytes类型" in str(exc_info.value)
    
    def test_decode_wav_invalid_format(self):
        """测试无效的WAV格式应该抛出DecodingError"""
        # 创建一些随机数据（不是有效的WAV格式）
        invalid_data = b'This is not a WAV file'
        
        with pytest.raises(DecodingError) as exc_info:
            AudioDecoder.decode_wav(invalid_data)
        
        assert "WAV格式" in str(exc_info.value)
    
    def test_decode_wav_wrong_channels(self):
        """测试声道数不匹配应该抛出DecodingError"""
        # 创建一个双声道的WAV数据
        audio_data = b'\x00' * 1024
        wav_data = AudioEncoder.encode_to_wav(
            audio_data,
            channels=2  # 使用双声道
        )
        
        with pytest.raises(DecodingError) as exc_info:
            AudioDecoder.decode_wav(wav_data)
        
        assert "声道数不匹配" in str(exc_info.value)
    
    def test_decode_wav_wrong_sample_width(self):
        """测试采样宽度不匹配应该抛出DecodingError"""
        # 创建一个1字节采样宽度的WAV数据
        audio_data = b'\x00' * 1024
        wav_data = AudioEncoder.encode_to_wav(
            audio_data,
            sample_width=1  # 使用1字节采样宽度
        )
        
        with pytest.raises(DecodingError) as exc_info:
            AudioDecoder.decode_wav(wav_data)
        
        assert "采样宽度不匹配" in str(exc_info.value)
    
    def test_decode_wav_wrong_sample_rate(self):
        """测试采样率不匹配应该抛出DecodingError"""
        # 创建一个8000Hz采样率的WAV数据
        audio_data = b'\x00' * 1024
        wav_data = AudioEncoder.encode_to_wav(
            audio_data,
            sample_rate=8000  # 使用8000Hz采样率
        )
        
        with pytest.raises(DecodingError) as exc_info:
            AudioDecoder.decode_wav(wav_data)
        
        assert "采样率不匹配" in str(exc_info.value)
    
    def test_decode_wav_large_data(self):
        """测试解码大量音频数据"""
        # 创建1MB的音频数据
        audio_data = b'\x00' * (1024 * 1024)
        
        # 编码
        wav_data = AudioEncoder.encode_to_wav(audio_data)
        
        # 解码
        decoded_frames, sample_rate, channels, sample_width = AudioDecoder.decode_wav(wav_data)
        
        # 验证解码成功
        assert decoded_frames == audio_data
        assert sample_rate == SAMPLE_RATE
        assert channels == CHANNELS
        assert sample_width == SAMPLE_WIDTH
    
    def test_decode_wav_various_sizes(self):
        """测试解码不同大小的音频数据"""
        sizes = [512, 1024, 2048, 4096, 8192]
        
        for size in sizes:
            audio_data = b'\x00' * size
            wav_data = AudioEncoder.encode_to_wav(audio_data)
            decoded_frames, _, _, _ = AudioDecoder.decode_wav(wav_data)
            
            assert len(decoded_frames) == size
            assert decoded_frames == audio_data
    
    def test_decode_wav_corrupted_header(self):
        """测试损坏的WAV头部应该抛出DecodingError"""
        # 创建一个有效的WAV数据
        audio_data = b'\x00' * 1024
        wav_data = AudioEncoder.encode_to_wav(audio_data)
        
        # 损坏头部（修改前4个字节）
        corrupted_data = b'XXXX' + wav_data[4:]
        
        with pytest.raises(DecodingError) as exc_info:
            AudioDecoder.decode_wav(corrupted_data)
        
        assert "WAV格式" in str(exc_info.value)
