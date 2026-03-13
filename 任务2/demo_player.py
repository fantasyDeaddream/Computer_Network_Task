"""
音频播放器演示脚本

演示如何使用AudioPlayer播放音频数据。
"""

from audio_player import AudioPlayer
from audio_recorder import AudioRecorder
from audio_encoder import AudioEncoder
from audio_decoder import AudioDecoder
from custom_exceptions import AudioDeviceError, PlaybackError
import time


def demo_record_and_play():
    """演示录制音频并立即播放"""
    print("=== 音频播放器演示 ===\n")
    
    # 录制音频
    print("1. 录制音频（3秒）...")
    recorder = AudioRecorder()
    
    try:
        recorder.start_recording()
        print("   录音中... 请说话")
        time.sleep(3)  # 录制3秒
        audio_data = recorder.stop_recording()
        print(f"   录音完成！数据大小: {len(audio_data)} 字节\n")
    except AudioDeviceError as e:
        print(f"   错误: {e}")
        return
    
    # 编码为WAV格式
    print("2. 编码为WAV格式...")
    encoder = AudioEncoder()
    wav_data = encoder.encode_to_wav(audio_data)
    print(f"   编码完成！WAV数据大小: {len(wav_data)} 字节\n")
    
    # 解码WAV数据
    print("3. 解码WAV数据...")
    decoder = AudioDecoder()
    decoded_data, sample_rate, channels, sample_width = decoder.decode_wav(wav_data)
    print(f"   解码完成！参数: {sample_rate}Hz, {channels}声道, {sample_width}字节\n")
    
    # 播放音频
    print("4. 播放音频...")
    player = AudioPlayer()
    
    try:
        player.play(decoded_data)
        print("   播放完成！\n")
    except AudioDeviceError as e:
        print(f"   音频设备错误: {e}")
    except PlaybackError as e:
        print(f"   播放错误: {e}")


def demo_play_empty_data():
    """演示播放空数据的错误处理"""
    print("=== 测试空数据错误处理 ===\n")
    
    player = AudioPlayer()
    
    try:
        player.play(b"")
        print("错误：应该抛出PlaybackError")
    except PlaybackError as e:
        print(f"正确捕获错误: {e}\n")


if __name__ == "__main__":
    try:
        demo_record_and_play()
        demo_play_empty_data()
        
        print("=== 演示完成 ===")
    except KeyboardInterrupt:
        print("\n\n演示被用户中断")
    except Exception as e:
        print(f"\n发生错误: {e}")
