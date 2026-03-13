"""
AudioProtocol演示脚本

演示如何使用AudioProtocol封装音频消息。
"""

import json
import base64
from audio_encoder import AudioEncoder
from audio_protocol import AudioProtocol


def main():
    print("=== AudioProtocol演示 ===\n")
    
    # 1. 创建一些示例音频数据
    print("1. 创建示例音频数据...")
    sample_audio_data = b'\x00\x01' * 1000  # 2000字节的示例数据
    print(f"   原始音频数据大小: {len(sample_audio_data)} 字节\n")
    
    # 2. 使用AudioEncoder编码为WAV格式
    print("2. 编码为WAV格式...")
    wav_data = AudioEncoder.encode_to_wav(sample_audio_data)
    print(f"   WAV数据大小: {len(wav_data)} 字节")
    print(f"   WAV头部: {wav_data[:12].hex()}\n")
    
    # 3. 使用AudioProtocol封装消息
    print("3. 封装为JSON消息...")
    filename = "demo_recording.wav"
    json_message = AudioProtocol.encode_message(filename, wav_data)
    print(f"   JSON消息长度: {len(json_message)} 字符\n")
    
    # 4. 解析并显示消息内容
    print("4. 解析消息内容...")
    message = json.loads(json_message)
    print(f"   消息类型: {message['type']}")
    print(f"   文件名: {message['filename']}")
    print(f"   数据长度: {message['length']} 字节")
    print(f"   Base64数据前50字符: {message['data'][:50]}...\n")
    
    # 5. 验证base64解码
    print("5. 验证base64解码...")
    decoded_data = base64.b64decode(message['data'])
    print(f"   解码后数据大小: {len(decoded_data)} 字节")
    print(f"   数据匹配: {decoded_data == wav_data}\n")
    
    print("=== 演示完成 ===")


if __name__ == "__main__":
    main()
