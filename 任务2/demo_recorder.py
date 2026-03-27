"""
AudioRecorder演示脚本

演示如何使用AudioRecorder类进行音频录制。

注意：需要安装pyaudio才能运行此脚本。
安装方法请参考INSTALL_PYAUDIO.md文件。
"""

import time
from audio_recorder import AudioRecorder
from audio_encoder import AudioEncoder
from custom_exceptions import AudioDeviceError


def demo_basic_recording():
    """演示基本的录音功能"""
    print("=== AudioRecorder基本录音演示 ===\n")
    
    try:
        # 创建录音器实例
        recorder = AudioRecorder()
        print("✓ 录音器初始化成功")
        
        # 开始录音
        print("\n开始录音...")
        recorder.start_recording()
        print(f"✓ 录音状态: {recorder.is_recording()}")
        
        # 录制3秒
        print("录制中... (3秒)")
        for i in range(3):
            time.sleep(1)
            print(f"  {i+1}秒...")
        
        # 停止录音
        print("\n停止录音...")
        audio_data = recorder.stop_recording()
        print(f"✓ 录音完成")
        print(f"✓ 录音状态: {recorder.is_recording()}")
        print(f"✓ 录制的音频数据大小: {len(audio_data)} 字节")
        
        # 编码为WAV格式
        print("\n编码为WAV格式...")
        encoder = AudioEncoder()
        wav_data = encoder.encode_to_wav(audio_data)
        print(f"✓ WAV数据大小: {len(wav_data)} 字节")
        
        # 保存到文件
        filename = "demo_recording.wav"
        with open(filename, 'wb') as f:
            f.write(wav_data)
        print(f"✓ 音频已保存到: {filename}")
        
    except AudioDeviceError as e:
        print(f"\n✗ 音频设备错误: {e}")
        print("\n提示：")
        print("  1. 请确保已安装pyaudio")
        print("  2. 请确保麦克风已连接并启用")
        print("  3. 请检查系统音频设备设置")
    except Exception as e:
        print(f"\n✗ 发生错误: {e}")


def demo_max_duration():
    """演示最大时长限制（使用短时长测试）"""
    print("\n\n=== AudioRecorder最大时长限制演示 ===\n")
    print("注意：实际最大时长为60秒，此演示仅显示概念\n")
    
    try:
        recorder = AudioRecorder()
        
        print("开始录音...")
        recorder.start_recording()
        
        # 录制5秒来演示
        print("录制中... (5秒)")
        for i in range(5):
            time.sleep(1)
            print(f"  {i+1}秒... 录音状态: {recorder.is_recording()}")
        
        # 停止录音
        audio_data = recorder.stop_recording()
        print(f"\n✓ 录音完成，数据大小: {len(audio_data)} 字节")
        
    except AudioDeviceError as e:
        print(f"\n✗ 音频设备错误: {e}")


if __name__ == '__main__':
    print("AudioRecorder演示程序")
    print("=" * 50)
    
    # 运行演示
    demo_basic_recording()
    demo_max_duration()
    
    print("\n" + "=" * 50)
    print("演示完成！")
