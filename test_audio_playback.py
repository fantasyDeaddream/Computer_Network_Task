"""
测试音频播放功能
"""

from audio_player import AudioPlayer
from audio_encoder import AudioEncoder
import wave
from io import BytesIO

print("测试音频播放功能...")

# 创建一些测试音频数据（1秒的静音）
sample_rate = 16000
duration = 1  # 秒
audio_data = b'\x00\x00' * sample_rate * duration  # 16位静音数据

print(f"生成测试音频数据: {len(audio_data)} bytes")

# 编码为WAV格式
encoder = AudioEncoder()
wav_data = encoder.encode_to_wav(audio_data)
print(f"编码为WAV格式: {len(wav_data)} bytes")

# 解码WAV数据
wav_buffer = BytesIO(wav_data)
with wave.open(wav_buffer, 'rb') as wav_file:
    frames = wav_file.readframes(wav_file.getnframes())
    print(f"解码音频帧: {len(frames)} bytes")

# 播放音频
try:
    player = AudioPlayer()
    print("开始播放...")
    player.play(frames)
    print("✓ 播放成功！")
except Exception as e:
    print(f"✗ 播放失败: {e}")
    import traceback
    traceback.print_exc()
