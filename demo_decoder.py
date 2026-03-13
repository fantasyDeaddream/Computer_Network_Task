"""
AudioDecoder演示脚本

演示音频编码和解码的完整流程。
"""

from audio_encoder import AudioEncoder
from audio_decoder import AudioDecoder
from custom_exceptions import DecodingError


def demo_basic_decode():
    """演示基本的编码-解码流程"""
    print("=== 演示1: 基本编码-解码流程 ===")
    
    # 创建一些测试音频数据
    original_data = bytes(range(256)) * 4  # 1024字节
    print(f"原始数据大小: {len(original_data)} 字节")
    
    # 编码为WAV格式
    wav_data = AudioEncoder.encode_to_wav(original_data)
    print(f"WAV数据大小: {len(wav_data)} 字节")
    
    # 解码WAV数据
    decoded_frames, sample_rate, channels, sample_width = AudioDecoder.decode_wav(wav_data)
    print(f"解码后数据大小: {len(decoded_frames)} 字节")
    print(f"音频参数: {sample_rate}Hz, {channels}声道, {sample_width}字节采样宽度")
    
    # 验证数据一致性
    if decoded_frames == original_data:
        print("✓ 数据完整性验证通过！")
    else:
        print("✗ 数据不一致！")
    
    print()


def demo_format_validation():
    """演示格式验证功能"""
    print("=== 演示2: 格式验证 ===")
    
    # 创建一个不匹配的WAV数据（双声道）
    audio_data = b'\x00' * 1024
    wav_data = AudioEncoder.encode_to_wav(audio_data, channels=2)
    
    try:
        AudioDecoder.decode_wav(wav_data)
        print("✗ 应该抛出异常但没有")
    except DecodingError as e:
        print(f"✓ 正确捕获格式错误: {e}")
    
    print()


def demo_error_handling():
    """演示错误处理"""
    print("=== 演示3: 错误处理 ===")
    
    # 测试空数据
    try:
        AudioDecoder.decode_wav(b'')
    except DecodingError as e:
        print(f"✓ 空数据错误: {e}")
    
    # 测试无效格式
    try:
        AudioDecoder.decode_wav(b'This is not a WAV file')
    except DecodingError as e:
        print(f"✓ 无效格式错误: {e}")
    
    print()


def demo_large_data():
    """演示处理大数据"""
    print("=== 演示4: 处理大数据 ===")
    
    # 创建1MB的音频数据
    large_data = b'\x00' * (1024 * 1024)
    print(f"原始数据大小: {len(large_data) / 1024 / 1024:.2f} MB")
    
    # 编码
    wav_data = AudioEncoder.encode_to_wav(large_data)
    print(f"WAV数据大小: {len(wav_data) / 1024 / 1024:.2f} MB")
    
    # 解码
    decoded_frames, _, _, _ = AudioDecoder.decode_wav(wav_data)
    print(f"解码后数据大小: {len(decoded_frames) / 1024 / 1024:.2f} MB")
    
    # 验证
    if decoded_frames == large_data:
        print("✓ 大数据处理成功！")
    else:
        print("✗ 大数据处理失败！")
    
    print()


if __name__ == '__main__':
    print("AudioDecoder功能演示\n")
    
    demo_basic_decode()
    demo_format_validation()
    demo_error_handling()
    demo_large_data()
    
    print("演示完成！")
