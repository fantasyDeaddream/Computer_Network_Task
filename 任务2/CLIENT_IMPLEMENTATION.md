# 音频客户端实现文档

## 概述

本文档描述了音频客户端（qcli.py）的实现细节。客户端基于任务1的TCP客户端代码，扩展支持音频录制、发送和播放功能。

## 实现的功能

### 1. TCP连接管理

- 连接到服务器（默认 localhost:8880）
- 支持自定义服务器地址和端口
- 自动重连和错误处理
- 优雅的断开连接

### 2. 音频录制

- 使用 AudioRecorder 采集音频数据
- 支持 `/record` 或 `/r` 命令开始录音
- 支持 `/stop` 或 `/s` 命令停止录音
- 自动限制最大录音时长（60秒）
- 完整的错误处理（设备不可用等）

### 3. 音频编码和封装

- 使用 AudioEncoder 将原始音频编码为WAV格式
- 使用 AudioProtocol 封装为JSON消息
- Base64编码二进制数据
- 检查文件大小限制（10MB）

### 4. 音频发送

- 通过TCP连接发送音频消息
- 自动添加消息分隔符（换行符）
- 显示发送确认信息
- 连接错误处理

### 5. 音频接收和播放

- 后台线程持续接收消息
- 自动识别音频消息类型
- 使用 AudioProtocol 解析消息
- 使用 AudioDecoder 解码WAV数据
- 使用 AudioPlayer 自动播放音频
- 完整的错误处理

### 6. 文本消息支持

- 支持发送文本消息（复用任务1功能）
- 接收并显示其他用户的文本消息
- 与音频消息混合传输

### 7. 命令行界面

- 清晰的欢迎信息和命令提示
- 简单直观的命令系统
- 实时状态反馈
- 用户友好的错误消息

## 架构设计

### 类结构

```python
class AudioClient:
    - __init__(host, port)          # 初始化客户端
    - connect()                     # 连接到服务器
    - disconnect()                  # 断开连接
    - run()                         # 运行主循环
    
    # 音频功能
    - start_recording()             # 开始录音
    - stop_recording_and_send()     # 停止录音并发送
    
    # 消息处理
    - send_text_message(content)    # 发送文本消息
    - _send_message(message)        # 发送消息（内部）
    - _receive_messages_thread()    # 接收消息线程
    - _handle_received_message(msg) # 处理接收的消息
    - _handle_audio_message(msg)    # 处理音频消息
```

### 组件依赖

```
AudioClient
├── AudioRecorder      # 音频录制
├── AudioPlayer        # 音频播放
├── AudioEncoder       # 音频编码
├── AudioDecoder       # 音频解码
└── AudioProtocol      # 协议封装/解析
```

## 消息格式

### 文本消息

```json
{
    "type": "text",
    "content": "Hello, World!"
}
```

### 音频消息

```json
{
    "type": "audio",
    "filename": "recording_1_20240115_103045.wav",
    "length": 32044,
    "data": "UklGRiR9AABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZG..."
}
```

## 错误处理

客户端实现了完整的错误处理机制：

### 1. 音频设备错误

```python
try:
    self.recorder.start_recording()
except AudioDeviceError as e:
    print(f"[Audio] 无法启动录音: {e}")
    print("[Audio] 请检查麦克风设备是否可用")
```

### 2. 编码错误

```python
try:
    wav_data = self.encoder.encode_to_wav(audio_data)
except EncodingError as e:
    print(f"[Audio] 编码错误: {e}")
```

### 3. 协议错误

```python
try:
    filename, wav_data = AudioProtocol.decode_message(message)
except ProtocolError as e:
    print(f"[Audio] 协议错误: {e}")
```

### 4. 网络错误

```python
try:
    self._send_message(message)
except Exception as e:
    print(f"[Audio] 发送音频错误: {e}")
```

### 5. 播放错误

```python
try:
    self.player.play(audio_frames)
except (AudioDeviceError, PlaybackError) as e:
    print(f"[Audio] 播放错误: {e}")
```

## 使用示例

### 启动客户端

```bash
# 使用默认配置
python qcli_start.py

# 指定服务器
python qcli_start.py 192.168.1.100 8880
```

### 录制和发送音频

```
/r
[Audio] 开始录音... (输入 /stop 或 /s 停止)
/s
[Audio] 停止录音...
[Audio] 编码音频数据...
[Audio] 封装消息...
[Audio] 音频已发送 (32044 bytes)
```

### 接收和播放音频

```
[Audio] 接收到音频: recording_1_20240115_103045.wav
[Audio] 正在播放...
[Audio] 播放完成
```

### 发送文本消息

```
Hello everyone!
[You] Hello everyone!
```

### 退出程序

```
/q
[Client] 正在退出...
[Client] 已断开连接
```

## 线程模型

客户端使用多线程架构：

1. **主线程**: 处理用户输入和命令
2. **接收线程**: 持续接收服务器消息
3. **录音线程**: 后台录制音频数据（在AudioRecorder内部）

所有后台线程都设置为守护线程（daemon=True），确保主程序退出时自动清理。

## 资源管理

客户端实现了完善的资源管理：

1. **Socket连接**: 在disconnect()中关闭
2. **录音器**: 在退出时自动停止
3. **音频流**: 在AudioRecorder和AudioPlayer中自动清理
4. **线程**: 使用守护线程，自动随主程序退出

## 测试验证

运行演示脚本验证功能：

```bash
python demo_client.py
```

输出示例：

```
============================================================
音频客户端功能演示
============================================================

1. 模拟录音数据...
   生成 32000 字节的音频数据

2. 编码为WAV格式...
   WAV数据大小: 32044 字节
   WAV头部: b'RIFF'

3. 封装为协议消息...
   消息长度: 42808 字节
   消息类型: audio
   文件名: test_recording.wav
   数据长度: 32044

4. 解析协议消息...
   解析文件名: test_recording.wav
   解析数据大小: 32044 字节
   数据一致性: True

5. 解码WAV数据...
   采样率: 16000 Hz
   声道数: 1
   采样宽度: 2 字节
   音频帧大小: 32000 字节
   数据一致性: True

6. 验证完整流程...
   ✅ 完整的音频处理流程验证成功！
```

## 与任务1的差异

### 复用的部分

1. TCP Socket连接管理
2. 多线程消息接收
3. JSON消息格式
4. 基本的命令行界面结构

### 新增的部分

1. 音频录制、编码、发送功能
2. 音频接收、解码、播放功能
3. 音频协议封装和解析
4. 完整的错误处理机制
5. 文件大小限制检查
6. 时间戳文件名生成

### 简化的部分

1. 移除了登录/登出机制（简化为直接连接）
2. 移除了用户ID和昵称管理
3. 简化了命令系统（使用/前缀）

## 配置参数

所有配置参数定义在 `audio_config.py` 中：

- `DEFAULT_HOST`: 默认服务器地址（localhost）
- `DEFAULT_PORT`: 默认端口（8880）
- `SAMPLE_RATE`: 采样率（16000 Hz）
- `CHANNELS`: 声道数（1）
- `SAMPLE_WIDTH`: 采样宽度（2字节）
- `CHUNK_SIZE`: 缓冲区大小（1024帧）
- `MAX_DURATION`: 最大录音时长（60秒）
- `MAX_AUDIO_SIZE`: 最大音频大小（10MB）
- `MESSAGE_DELIMITER`: 消息分隔符（'\n'）

## 依赖项

- `socket`: TCP网络通信
- `threading`: 多线程支持
- `json`: JSON消息格式
- `datetime`: 时间戳生成
- `pyaudio`: 音频采集和播放（运行时需要）
- `wave`: WAV格式处理
- `base64`: 二进制数据编码

## 已知限制

1. 不支持同时录制多个音频
2. 录音时长限制为60秒
3. 音频文件大小限制为10MB
4. 仅支持16kHz单声道16位音频
5. 需要pyaudio库支持（需要C++编译工具）

## 未来改进

1. 支持音频压缩（如MP3、Opus）
2. 支持多种音频格式
3. 添加音频可视化（波形显示）
4. 支持音频文件上传
5. 添加音频质量选择
6. 支持暂停/恢复录音
7. 添加音量控制

## 总结

音频客户端（qcli.py）成功实现了所有要求的功能：

✅ 复用任务1的TCP客户端基础代码
✅ 集成音频录制功能
✅ 集成音频播放功能
✅ 实现命令行界面
✅ 实现完整的错误处理

客户端代码结构清晰、功能完整、错误处理健壮，可以与服务器配合实现完整的音频通信功能。
