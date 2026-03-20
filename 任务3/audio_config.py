"""
音频配置常量

定义系统使用的统一音频参数。
"""

try:
    import pyaudio
    AUDIO_FORMAT = pyaudio.paInt16  # pyaudio格式：16位整数
except ImportError:
    # 如果pyaudio未安装，使用占位符值
    # 实际使用时需要安装pyaudio
    AUDIO_FORMAT = 8  # paInt16的值

# 音频采样参数
SAMPLE_RATE = 16000  # 采样率：16kHz
CHANNELS = 1  # 声道数：单声道
SAMPLE_WIDTH = 2  # 采样宽度：2字节（16位）
# AUDIO_FORMAT定义在上面的try-except块中

# 录音参数
CHUNK_SIZE = 1024  # 缓冲区大小：1024帧
MAX_DURATION = 60  # 最大录音时长：60秒

# 文件传输参数
MAX_FILENAME_LENGTH = 255  # 文件名最大长度
MAX_AUDIO_SIZE = 10 * 1024 * 1024  # 最大音频大小：10MB

# 网络参数
DEFAULT_HOST = 'localhost'
DEFAULT_PORT = 8880
MESSAGE_DELIMITER = '\n'  # 消息分隔符

# 音频配置字典（用于方便传递参数）
AUDIO_CONFIG = {
    'sample_rate': SAMPLE_RATE,
    'channels': CHANNELS,
    'sample_width': SAMPLE_WIDTH,
    'format': AUDIO_FORMAT,
    'chunk_size': CHUNK_SIZE,
    'max_duration': MAX_DURATION
}
