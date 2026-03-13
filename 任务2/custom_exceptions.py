"""
自定义异常类

定义音频通信系统使用的所有自定义异常。
"""


class AudioDeviceError(Exception):
    """
    音频设备错误
    
    当pyaudio无法初始化音频设备或设备不可用时抛出。
    """
    pass


class EncodingError(Exception):
    """
    编码错误
    
    当音频数据编码为WAV格式失败时抛出。
    """
    pass


class DecodingError(Exception):
    """
    解码错误
    
    当WAV格式数据解码失败或格式不匹配时抛出。
    """
    pass


class ProtocolError(Exception):
    """
    协议错误
    
    当音频消息格式无效或缺少必需字段时抛出。
    """
    pass


class PlaybackError(Exception):
    """
    播放错误
    
    当音频播放过程中发生错误时抛出。
    """
    pass
