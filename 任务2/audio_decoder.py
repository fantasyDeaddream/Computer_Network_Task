"""
音频解码器

负责解析WAV格式的音频数据。
"""

import wave
from io import BytesIO
from custom_exceptions import DecodingError
from audio_config import SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH


class AudioDecoder:
    """音频解码器"""
    
    @staticmethod
    def decode_wav(wav_data: bytes) -> tuple[bytes, int, int, int]:
        """
        解码WAV格式数据
        
        Args:
            wav_data: WAV格式的二进制数据
            
        Returns:
            (audio_frames, sample_rate, channels, sample_width)
            - audio_frames: 原始音频帧数据
            - sample_rate: 采样率
            - channels: 声道数
            - sample_width: 采样宽度（字节）
            
        Raises:
            DecodingError: 解码失败或格式不匹配
        """
        if not wav_data:
            raise DecodingError("WAV数据不能为空")
        
        if not isinstance(wav_data, bytes):
            raise DecodingError("WAV数据必须是bytes类型")
        
        try:
            # 使用BytesIO在内存中处理WAV数据
            wav_buffer = BytesIO(wav_data)
            
            # 打开wave文件读取器
            with wave.open(wav_buffer, 'rb') as wav_file:
                # 读取音频参数
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                
                # 验证音频格式参数
                if channels != CHANNELS:
                    raise DecodingError(
                        f"声道数不匹配: 期望{CHANNELS}，实际{channels}"
                    )
                
                if sample_width != SAMPLE_WIDTH:
                    raise DecodingError(
                        f"采样宽度不匹配: 期望{SAMPLE_WIDTH}字节，实际{sample_width}字节"
                    )
                
                if sample_rate != SAMPLE_RATE:
                    raise DecodingError(
                        f"采样率不匹配: 期望{SAMPLE_RATE}Hz，实际{sample_rate}Hz"
                    )
                
                # 读取所有音频帧数据
                audio_frames = wav_file.readframes(wav_file.getnframes())
            
            return audio_frames, sample_rate, channels, sample_width
            
        except DecodingError:
            # 重新抛出DecodingError
            raise
        except wave.Error as e:
            raise DecodingError(f"无效的WAV格式: {str(e)}")
        except Exception as e:
            raise DecodingError(f"解码WAV格式失败: {str(e)}")
