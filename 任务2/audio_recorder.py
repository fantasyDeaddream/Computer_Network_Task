"""
音频录制器

使用pyaudio采集音频数据。
"""

import pyaudio
import threading
import time
from typing import Optional

from audio_config import (
    SAMPLE_RATE, CHANNELS, CHUNK_SIZE, 
    AUDIO_FORMAT, MAX_DURATION
)
from custom_exceptions import AudioDeviceError


class AudioRecorder:
    """
    音频录制器
    
    使用pyaudio采集音频数据，支持开始录音、停止录音和查询录音状态。
    """
    
    def __init__(self, 
                 sample_rate: int = SAMPLE_RATE,
                 channels: int = CHANNELS,
                 chunk_size: int = CHUNK_SIZE,
                 format: int = AUDIO_FORMAT):
        """
        初始化录音器
        
        Args:
            sample_rate: 采样率（Hz），默认16000
            channels: 声道数，默认1（单声道）
            chunk_size: 每次读取的帧数，默认1024
            format: 音频格式，默认pyaudio.paInt16
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.format = format
        
        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._frames: list[bytes] = []
        self._is_recording = False
        self._start_time: Optional[float] = None

    def start_recording(self) -> None:
        """
        开始录音，初始化pyaudio流
        
        启动后台线程持续采集音频数据，直到调用stop_recording或达到60秒最大时长。
        
        Raises:
            AudioDeviceError: 音频设备不可用或初始化失败
        """
        if self._is_recording:
            return  # 已经在录音中
        
        try:
            # 初始化pyaudio
            self._pyaudio = pyaudio.PyAudio()
            
            # 打开音频输入流
            self._stream = self._pyaudio.open(
                format=self.format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
            
            # 重置状态
            self._frames = []
            self._is_recording = True
            self._start_time = time.time()
            
            # 启动后台录音线程
            self._recording_thread = threading.Thread(target=self._record_audio)
            self._recording_thread.daemon = True
            self._recording_thread.start()
            
        except Exception as e:
            # 清理资源
            if self._stream:
                self._stream.close()
            if self._pyaudio:
                self._pyaudio.terminate()
            
            self._stream = None
            self._pyaudio = None
            self._is_recording = False
            
            raise AudioDeviceError(f"无法初始化音频设备: {str(e)}")
    
    def _record_audio(self) -> None:
        """
        后台线程：持续录制音频数据
        
        自动在达到MAX_DURATION时停止录音。
        """
        try:
            while self._is_recording and self._stream:
                # 检查是否超过最大时长
                if self._start_time and (time.time() - self._start_time) >= MAX_DURATION:
                    self._is_recording = False
                    break
                
                # 读取音频数据
                try:
                    data = self._stream.read(self.chunk_size, exception_on_overflow=False)
                    self._frames.append(data)
                except Exception:
                    # 读取错误，停止录音
                    self._is_recording = False
                    break
        except Exception:
            self._is_recording = False

    def stop_recording(self) -> bytes:
        """
        停止录音并返回音频数据
        
        停止后台录音线程，收集所有录制的帧，关闭流并释放资源。
        
        Returns:
            原始音频帧数据（bytes）
            
        Raises:
            AudioDeviceError: 音频设备操作失败
        """
        if not self._is_recording:
            return b''  # 没有在录音
        
        try:
            # 停止录音标志
            self._is_recording = False
            
            # 等待录音线程结束（最多等待1秒）
            if hasattr(self, '_recording_thread') and self._recording_thread.is_alive():
                self._recording_thread.join(timeout=1.0)
            
            # 收集所有帧
            audio_data = b''.join(self._frames)
            
            return audio_data
            
        except Exception as e:
            raise AudioDeviceError(f"停止录音时发生错误: {str(e)}")
        
        finally:
            # 清理资源
            if self._stream:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except:
                    pass
                self._stream = None
            
            if self._pyaudio:
                try:
                    self._pyaudio.terminate()
                except:
                    pass
                self._pyaudio = None
            
            self._frames = []
            self._start_time = None

    def is_recording(self) -> bool:
        """
        返回当前录音状态
        
        Returns:
            True表示正在录音，False表示未录音
        """
        return self._is_recording
