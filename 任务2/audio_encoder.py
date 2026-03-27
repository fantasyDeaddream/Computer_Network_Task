"""
音频编码器

负责将原始音频数据编码为WAV格式。
"""

import wave
from io import BytesIO
from custom_exceptions import EncodingError
from audio_config import SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH


class AudioEncoder:
    """音频编码器"""
    
    @staticmethod
    def encode_to_wav(audio_data: bytes,
                      sample_rate: int = SAMPLE_RATE,
                      channels: int = CHANNELS,
                      sample_width: int = SAMPLE_WIDTH) -> bytes:
        """
        将原始音频数据编码为WAV格式
        
        Args:
            audio_data: 原始音频帧数据
            sample_rate: 采样率（默认16000Hz）
            channels: 声道数（默认1）
            sample_width: 采样宽度（字节，默认2）
            
        Returns:
            完整的WAV格式二进制数据（包含头部）
            
        Raises:
            EncodingError: 编码失败
        """
        if not audio_data:
            raise EncodingError("音频数据不能为空")
        
        if not isinstance(audio_data, bytes):
            raise EncodingError("音频数据必须是bytes类型")
        
        try:
            # 使用BytesIO在内存中处理WAV数据
            wav_buffer = BytesIO()
            
            # 打开wave文件写入器
            with wave.open(wav_buffer, 'wb') as wav_file:
                # 设置音频参数
                wav_file.setnchannels(channels)  # 声道数
                wav_file.setsampwidth(sample_width)  # 采样宽度（字节）
                wav_file.setframerate(sample_rate)  # 采样率
                
                # 写入音频数据
                wav_file.writeframes(audio_data)
            
            # 获取完整的WAV数据（包含头部）
            wav_data = wav_buffer.getvalue()
            
            return wav_data
            
        except Exception as e:
            raise EncodingError(f"编码WAV格式失败: {str(e)}")
