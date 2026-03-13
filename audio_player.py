"""
音频播放器

使用pyaudio播放音频数据。
"""

import pyaudio
from audio_config import SAMPLE_RATE, CHANNELS, AUDIO_FORMAT, CHUNK_SIZE
from custom_exceptions import AudioDeviceError, PlaybackError


class AudioPlayer:
    """
    音频播放器
    
    使用pyaudio播放接收到的音频数据。
    配置参数与录制相同（16位、16000Hz、单声道）。
    """
    
    def __init__(self, 
                 sample_rate: int = SAMPLE_RATE,
                 channels: int = CHANNELS,
                 format: int = AUDIO_FORMAT):
        """
        初始化播放器
        
        Args:
            sample_rate: 采样率（Hz），默认16000
            channels: 声道数，默认1（单声道）
            format: 音频格式，默认pyaudio.paInt16（16位）
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.format = format
        self.chunk_size = CHUNK_SIZE
        self.pyaudio_instance = None
        self.stream = None
    
    def play(self, audio_data: bytes) -> None:
        """
        播放音频数据
        
        Args:
            audio_data: 原始音频帧数据（bytes）
            
        Raises:
            AudioDeviceError: 音频设备不可用
            PlaybackError: 播放过程中发生错误
        """
        if not audio_data:
            raise PlaybackError("音频数据为空，无法播放")
        
        try:
            # 初始化pyaudio实例
            self.pyaudio_instance = pyaudio.PyAudio()
            
            # 打开音频输出流
            try:
                self.stream = self.pyaudio_instance.open(
                    format=self.format,
                    channels=self.channels,
                    rate=self.sample_rate,
                    output=True,
                    frames_per_buffer=self.chunk_size
                )
            except Exception as e:
                raise AudioDeviceError(f"无法初始化音频输出设备: {e}")
            
            # 播放音频数据
            try:
                # 确保audio_data是bytes类型
                if not isinstance(audio_data, bytes):
                    audio_data = bytes(audio_data)
                
                # 分块写入音频数据
                for i in range(0, len(audio_data), self.chunk_size):
                    chunk = audio_data[i:i + self.chunk_size]
                    # 使用exception_on_underflow=False避免缓冲区问题
                    self.stream.write(chunk, exception_on_underflow=False)
            except Exception as e:
                raise PlaybackError(f"播放音频时发生错误: {e}")
            
        finally:
            # 确保资源被正确释放
            self._cleanup()
    
    def _cleanup(self) -> None:
        """
        清理资源，关闭音频流并释放pyaudio实例
        """
        try:
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
        except Exception:
            pass  # 忽略清理过程中的错误
        
        try:
            if self.pyaudio_instance is not None:
                self.pyaudio_instance.terminate()
                self.pyaudio_instance = None
        except Exception:
            pass  # 忽略清理过程中的错误
