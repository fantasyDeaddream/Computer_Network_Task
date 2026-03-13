"""
客户端功能演示脚本

演示客户端的各个组件功能（不需要实际连接服务器）。
"""

import json
from audio_encoder import AudioEncoder
from audio_decoder import AudioDecoder
from audio_protocol import AudioProtocol


def demo_audio_pipeline():
    """演示完整的音频处理流程"""
    print("="*60)
    print("音频客户端功能演示")
    print("="*60)
    
    # 1. 模拟录音数据
    print("\n1. 模拟录音数据...")
    # 创建一些模拟的音频数据（1秒的静音）
    sample_rate = 16000
    duration = 1  # 秒
    audio_data = b'\x00\x00' * (sample_rate * duration)
    print(f"   生成 {len(audio_data)} 字节的音频数据")
    
    # 2. 编码为WAV格式
    print("\n2. 编码为WAV格式...")
    encoder = AudioEncoder()
    wav_data = encoder.encode_to_wav(audio_data)
    print(f"   WAV数据大小: {len(wav_data)} 字节")
    print(f"   WAV头部: {wav_data[:4]}")  # 应该是 b'RIFF'
    
    # 3. 封装为协议消息
    print("\n3. 封装为协议消息...")
    filename = "test_recording.wav"
    message = AudioProtocol.encode_message(filename, wav_data)
    print(f"   消息长度: {len(message)} 字节")
    
    # 解析消息查看结构
    msg_dict = json.loads(message)
    print(f"   消息类型: {msg_dict['type']}")
    print(f"   文件名: {msg_dict['filename']}")
    print(f"   数据长度: {msg_dict['length']}")
    print(f"   Base64数据前50字符: {msg_dict['data'][:50]}...")
    
    # 4. 解析协议消息
    print("\n4. 解析协议消息...")
    decoded_filename, decoded_wav_data = AudioProtocol.decode_message(message)
    print(f"   解析文件名: {decoded_filename}")
    print(f"   解析数据大小: {len(decoded_wav_data)} 字节")
    print(f"   数据一致性: {decoded_wav_data == wav_data}")
    
    # 5. 解码WAV数据
    print("\n5. 解码WAV数据...")
    decoder = AudioDecoder()
    audio_frames, sr, channels, width = decoder.decode_wav(decoded_wav_data)
    print(f"   采样率: {sr} Hz")
    print(f"   声道数: {channels}")
    print(f"   采样宽度: {width} 字节")
    print(f"   音频帧大小: {len(audio_frames)} 字节")
    print(f"   数据一致性: {audio_frames == audio_data}")
    
    # 6. 验证完整流程
    print("\n6. 验证完整流程...")
    if audio_frames == audio_data:
        print("   ✅ 完整的音频处理流程验证成功！")
        print("   原始数据 -> 编码 -> 封装 -> 解析 -> 解码 -> 原始数据")
    else:
        print("   ❌ 数据不一致")
    
    print("\n" + "="*60)
    print("演示完成")
    print("="*60)


def demo_error_handling():
    """演示错误处理"""
    print("\n" + "="*60)
    print("错误处理演示")
    print("="*60)
    
    # 测试空数据
    print("\n1. 测试空数据编码...")
    try:
        encoder = AudioEncoder()
        encoder.encode_to_wav(b'')
    except Exception as e:
        print(f"   ✅ 捕获异常: {type(e).__name__}: {e}")
    
    # 测试无效的协议消息
    print("\n2. 测试无效的协议消息...")
    try:
        AudioProtocol.decode_message('{"invalid": "message"}')
    except Exception as e:
        print(f"   ✅ 捕获异常: {type(e).__name__}: {e}")
    
    # 测试无效的WAV数据
    print("\n3. 测试无效的WAV数据...")
    try:
        decoder = AudioDecoder()
        decoder.decode_wav(b'not a wav file')
    except Exception as e:
        print(f"   ✅ 捕获异常: {type(e).__name__}: {e}")
    
    print("\n" + "="*60)


def demo_message_format():
    """演示消息格式"""
    print("\n" + "="*60)
    print("消息格式演示")
    print("="*60)
    
    # 文本消息
    print("\n1. 文本消息格式:")
    text_msg = json.dumps({
        'type': 'text',
        'content': 'Hello, World!'
    }, indent=2)
    print(text_msg)
    
    # 音频消息（简化版）
    print("\n2. 音频消息格式（简化）:")
    audio_msg = {
        'type': 'audio',
        'filename': 'recording_001.wav',
        'length': 12345,
        'data': 'base64_encoded_data...'
    }
    print(json.dumps(audio_msg, indent=2))
    
    print("\n" + "="*60)


if __name__ == '__main__':
    demo_audio_pipeline()
    demo_error_handling()
    demo_message_format()
    
    print("\n提示: 要测试完整的客户端功能，请先启动服务器，然后运行:")
    print("  python qcli_start.py")
