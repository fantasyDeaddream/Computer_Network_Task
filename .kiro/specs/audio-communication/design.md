# Design Document: Audio Communication System

## Overview

本设计文档描述了基于TCP的网络音频通信系统的技术实现方案。系统在任务1的文本聊天室基础上扩展，添加音频录制、传输和播放功能。

系统采用客户端-服务器架构，使用pyaudio库进行音频采集和播放，使用wave库处理WAV格式音频文件，通过TCP连接传输JSON封装的音频消息。音频数据使用base64编码进行文本化传输。

核心设计原则：
- 模块化设计：音频功能独立封装，便于维护和测试
- 复用现有代码：基于任务1的TCP通信框架
- 标准格式：使用WAV格式确保兼容性
- 错误容错：优雅处理各类异常情况

## Architecture

系统采用分层架构设计：

```
┌─────────────────────────────────────────┐
│         命令行用户界面层                  │
│    (用户交互、命令解析、状态显示)          │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│          音频处理层                       │
│  ┌──────────────┐  ┌──────────────┐    │
│  │ AudioRecorder│  │ AudioPlayer  │    │
│  └──────────────┘  └──────────────┘    │
│  ┌──────────────┐  ┌──────────────┐    │
│  │ AudioEncoder │  │ AudioDecoder │    │
│  └──────────────┘  └──────────────┘    │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│         协议封装层                        │
│      (AudioProtocol - JSON封装)         │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│         网络传输层                        │
│    (TCP Socket - 复用任务1代码)          │
└─────────────────────────────────────────┘
```

客户端流程：
1. 用户输入录音命令
2. AudioRecorder采集音频数据
3. AudioEncoder编码为WAV格式
4. AudioProtocol封装为JSON消息
5. 通过TCP连接发送到服务器
6. 接收服务器转发的音频消息
7. AudioProtocol解析消息
8. AudioDecoder解码音频数据
9. AudioPlayer播放音频

服务器流程：
1. 监听TCP连接（端口8880）
2. 接收客户端发送的音频消息
3. 解析并验证消息格式
4. 转发音频消息给所有连接的客户端
5. 维护客户端连接列表

## Components and Interfaces

### AudioRecorder

负责音频采集功能。

```python
class AudioRecorder:
    """音频录制器"""
    
    def __init__(self, 
                 sample_rate: int = 16000,
                 channels: int = 1,
                 chunk_size: int = 1024,
                 format: int = pyaudio.paInt16):
        """
        初始化录音器
        
        Args:
            sample_rate: 采样率（Hz）
            channels: 声道数
            chunk_size: 每次读取的帧数
            format: 音频格式
        """
        
    def start_recording(self) -> None:
        """开始录音，初始化pyaudio流"""
        
    def stop_recording(self) -> bytes:
        """
        停止录音并返回音频数据
        
        Returns:
            原始音频帧数据（bytes）
            
        Raises:
            AudioDeviceError: 音频设备不可用
        """
        
    def is_recording(self) -> bool:
        """返回当前是否正在录音"""
```

### AudioEncoder

负责将原始音频数据编码为WAV格式。

```python
class AudioEncoder:
    """音频编码器"""
    
    @staticmethod
    def encode_to_wav(audio_data: bytes,
                      sample_rate: int = 16000,
                      channels: int = 1,
                      sample_width: int = 2) -> bytes:
        """
        将原始音频数据编码为WAV格式
        
        Args:
            audio_data: 原始音频帧数据
            sample_rate: 采样率
            channels: 声道数
            sample_width: 采样宽度（字节）
            
        Returns:
            完整的WAV格式二进制数据（包含头部）
            
        Raises:
            EncodingError: 编码失败
        """
```

### AudioDecoder

负责解析WAV格式音频数据。

```python
class AudioDecoder:
    """音频解码器"""
    
    @staticmethod
    def decode_wav(wav_data: bytes) -> tuple[bytes, int, int, int]:
        """
        解码WAV格式数据
        
        Args:
            wav_data: WAV格式的二进制数据
            
        Returns:
            (audio_frames, sample_rate, channels, sample_width)
            
        Raises:
            DecodingError: 解码失败或格式不匹配
        """
```

### AudioPlayer

负责音频播放功能。

```python
class AudioPlayer:
    """音频播放器"""
    
    def __init__(self,
                 sample_rate: int = 16000,
                 channels: int = 1,
                 format: int = pyaudio.paInt16):
        """
        初始化播放器
        
        Args:
            sample_rate: 采样率
            channels: 声道数
            format: 音频格式
        """
        
    def play(self, audio_data: bytes) -> None:
        """
        播放音频数据
        
        Args:
            audio_data: 原始音频帧数据
            
        Raises:
            AudioDeviceError: 音频设备不可用
            PlaybackError: 播放过程中发生错误
        """
```

### AudioProtocol

负责音频消息的封装和解析。

```python
class AudioProtocol:
    """音频传输协议"""
    
    @staticmethod
    def encode_message(filename: str, audio_data: bytes) -> str:
        """
        封装音频消息为JSON格式
        
        Args:
            filename: 音频文件名
            audio_data: WAV格式的音频二进制数据
            
        Returns:
            JSON格式的消息字符串
            
        Message Format:
            {
                "type": "audio",
                "filename": "recording_001.wav",
                "length": 12345,
                "data": "base64_encoded_audio_data..."
            }
        """
        
    @staticmethod
    def decode_message(message: str) -> tuple[str, bytes]:
        """
        解析音频消息
        
        Args:
            message: JSON格式的消息字符串
            
        Returns:
            (filename, audio_data)
            
        Raises:
            ProtocolError: 消息格式无效或缺少必需字段
        """
        
    @staticmethod
    def validate_message(message: dict) -> bool:
        """
        验证消息格式
        
        Args:
            message: 解析后的消息字典
            
        Returns:
            消息是否有效
        """
```

### Client (qcli.py)

客户端程序，扩展任务1的客户端功能。

```python
class AudioClient:
    """音频客户端"""
    
    def __init__(self, host: str = 'localhost', port: int = 8880):
        """初始化客户端，建立TCP连接"""
        
    def start_recording(self) -> None:
        """启动录音功能"""
        
    def stop_recording_and_send(self) -> None:
        """停止录音并发送音频"""
        
    def handle_received_audio(self, message: str) -> None:
        """处理接收到的音频消息并播放"""
        
    def run(self) -> None:
        """运行客户端主循环"""
```

命令行界面：
- `/record` 或 `/r`: 开始录音
- `/stop` 或 `/s`: 停止录音并发送
- `/quit` 或 `/q`: 退出程序
- 其他文本: 发送文本消息（复用任务1功能）

### Server (qser.py)

服务器程序，扩展任务1的服务器功能。

```python
class AudioServer:
    """音频服务器"""
    
    def __init__(self, host: str = '0.0.0.0', port: int = 8880):
        """初始化服务器"""
        
    def handle_client(self, client_socket: socket.socket, address: tuple) -> None:
        """处理单个客户端连接"""
        
    def broadcast_message(self, message: str, sender_socket: socket.socket) -> None:
        """广播消息给所有客户端（除发送者）"""
        
    def start(self) -> None:
        """启动服务器"""
```

## Data Models

### Audio Parameters

系统使用统一的音频参数：

```python
AUDIO_CONFIG = {
    'sample_rate': 16000,      # 采样率：16kHz
    'channels': 1,             # 声道数：单声道
    'sample_width': 2,         # 采样宽度：2字节（16位）
    'format': pyaudio.paInt16, # pyaudio格式：16位整数
    'chunk_size': 1024,        # 缓冲区大小：1024帧
    'max_duration': 60         # 最大录音时长：60秒
}
```

### Message Format

音频消息JSON格式：

```python
{
    "type": "audio",                    # 消息类型
    "filename": str,                    # 文件名（最多255字符）
    "length": int,                      # 数据长度（字节）
    "data": str                         # base64编码的音频数据
}
```

文本消息格式（复用任务1）：

```python
{
    "type": "text",                     # 消息类型
    "content": str                      # 文本内容
}
```

### Error Types

自定义异常类型：

```python
class AudioDeviceError(Exception):
    """音频设备错误"""
    pass

class EncodingError(Exception):
    """编码错误"""
    pass

class DecodingError(Exception):
    """解码错误"""
    pass

class ProtocolError(Exception):
    """协议错误"""
    pass

class PlaybackError(Exception):
    """播放错误"""
    pass
```


## Correctness Properties

属性（Property）是关于系统行为的特征或规则，应该在所有有效执行中保持为真。属性是人类可读规范和机器可验证正确性保证之间的桥梁。通过属性测试，我们可以验证系统在大量随机生成的输入下的正确性。

### Property 1: 音频编解码Round-Trip一致性

*对于任意*有效的原始音频数据（bytes），使用AudioEncoder编码为WAV格式，然后使用AudioDecoder解码，应该得到与原始数据等价的音频帧数据。

**Validates: Requirements 2.1, 2.3, 6.1, 6.3**

### Property 2: 协议封装Round-Trip一致性

*对于任意*有效的文件名和WAV格式音频数据，使用AudioProtocol.encode_message封装为JSON消息，然后使用AudioProtocol.decode_message解析，应该得到相同的文件名和音频数据。

**Validates: Requirements 3.1, 3.2**

### Property 3: Base64编码可逆性

*对于任意*二进制音频数据，编码为base64字符串后解码，应该得到完全相同的二进制数据。

**Validates: Requirements 3.2**

### Property 4: 消息格式验证完整性

*对于任意*缺少必需字段（type、filename、length、data中的任一字段）的JSON消息，AudioProtocol.decode_message应该抛出ProtocolError异常。

**Validates: Requirements 3.3, 3.4**


### Property 5: WAV格式头部有效性

*对于任意*有效的音频数据，AudioEncoder.encode_to_wav的输出应该以"RIFF"标识开头，并在偏移8字节处包含"WAVE"标识，符合WAV文件格式规范。

**Validates: Requirements 2.3**

### Property 6: 录音器初始化幂等性

*对于任意*AudioRecorder实例，多次调用start_recording应该正确初始化pyaudio输入流，且配置参数保持一致（16000Hz、单声道、16位）。

**Validates: Requirements 1.1, 1.2**

### Property 7: 录音数据非空性

*对于任意*成功的录音会话（录音时长>0秒），调用stop_recording应该返回非空的音频数据（len(data) > 0）。

**Validates: Requirements 1.3**

### Property 8: 网络消息分隔符一致性

*对于任意*通过客户端发送的音频消息，发送到TCP连接的数据应该以换行符('\n')结尾。

**Validates: Requirements 4.2**

### Property 9: 服务器广播排除发送者

*对于任意*服务器接收到的音频消息，广播时应该发送给所有连接的客户端，但排除消息的原始发送者。

**Validates: Requirements 5.5**


### Property 10: 音频设备错误处理

*对于任意*pyaudio初始化失败的情况（设备不可用），AudioRecorder.start_recording和AudioPlayer.play应该抛出AudioDeviceError异常而不是崩溃。

**Validates: Requirements 1.5, 7.4, 10.1**

### Property 11: 编码错误处理

*对于任意*无效的输入数据（如空数据或格式错误），AudioEncoder.encode_to_wav应该抛出EncodingError异常。

**Validates: Requirements 2.4, 10.3**

### Property 12: 解码错误处理

*对于任意*非WAV格式或参数不匹配的数据，AudioDecoder.decode_wav应该抛出DecodingError异常。

**Validates: Requirements 6.4, 10.3**

### Property 13: 网络错误容错性

*对于任意*TCP连接断开的情况，客户端发送操作应该抛出连接错误，但程序应该继续运行而不崩溃。

**Validates: Requirements 4.3, 10.2**

### Property 14: 服务器错误恢复

*对于任意*接收到的格式错误消息，服务器应该记录错误并继续监听其他消息，不应该终止服务。

**Validates: Requirements 5.4, 10.2**


### Property 15: 端到端音频传输完整性

*对于任意*有效的音频数据，从客户端录制、编码、封装、发送，到服务器接收、转发，再到另一客户端接收、解析、解码、播放的完整流程，最终播放的音频数据应该与原始录制的数据等价。

**Validates: Requirements 8.3**

## Error Handling

系统采用分层错误处理策略：

### 音频层错误

1. **AudioDeviceError**: pyaudio设备初始化失败
   - 捕获位置：AudioRecorder.start_recording, AudioPlayer.play
   - 处理方式：向用户显示友好错误消息，提示检查音频设备
   - 恢复策略：允许用户重试或继续使用文本功能

2. **EncodingError**: WAV编码失败
   - 捕获位置：AudioEncoder.encode_to_wav
   - 处理方式：记录错误详情，通知用户编码失败
   - 恢复策略：丢弃当前录音，允许重新录制

3. **DecodingError**: WAV解码失败或格式不匹配
   - 捕获位置：AudioDecoder.decode_wav
   - 处理方式：记录错误详情，跳过该音频消息
   - 恢复策略：继续处理其他消息

4. **PlaybackError**: 播放过程中发生错误
   - 捕获位置：AudioPlayer.play
   - 处理方式：停止播放，清理资源，通知用户
   - 恢复策略：继续监听其他音频消息

### 协议层错误

1. **ProtocolError**: 消息格式无效或缺少必需字段
   - 捕获位置：AudioProtocol.decode_message
   - 处理方式：记录错误消息内容，返回解析失败
   - 恢复策略：跳过该消息，继续处理后续消息


### 网络层错误

1. **ConnectionError**: TCP连接断开或发送失败
   - 捕获位置：客户端发送操作
   - 处理方式：显示连接错误，提示用户检查网络
   - 恢复策略：尝试重新连接或退出程序

2. **SocketError**: Socket操作失败
   - 捕获位置：服务器接收和广播操作
   - 处理方式：记录错误，移除失效的客户端连接
   - 恢复策略：继续服务其他客户端

### 错误日志

所有错误都应该记录到控制台，包含：
- 时间戳
- 错误类型
- 错误描述
- 相关上下文（如文件名、客户端地址）

格式示例：
```
[2024-01-15 10:30:45] ERROR: AudioDeviceError - 无法初始化音频设备
[2024-01-15 10:31:20] ERROR: ProtocolError - 消息缺少必需字段 'data'
[2024-01-15 10:32:10] ERROR: ConnectionError - 客户端 192.168.1.100:5432 连接断开
```

## Testing Strategy

系统采用双重测试策略，结合单元测试和基于属性的测试（Property-Based Testing）以确保全面覆盖。

### 测试框架选择

- **单元测试框架**: pytest
- **属性测试库**: Hypothesis
- **Mock库**: unittest.mock（用于模拟pyaudio和socket）

### 单元测试

单元测试专注于具体示例、边界条件和集成点：


1. **AudioRecorder测试**
   - 测试正常录音流程
   - 测试60秒最大时长限制（边界条件）
   - 测试设备不可用情况（错误处理）

2. **AudioEncoder/Decoder测试**
   - 测试标准音频参数编解码
   - 测试空数据处理（边界条件）
   - 测试格式不匹配处理（错误处理）

3. **AudioProtocol测试**
   - 测试标准消息封装和解析
   - 测试255字符文件名限制（边界条件）
   - 测试缺少字段的消息（错误处理）
   - 测试无效JSON格式（错误处理）

4. **AudioPlayer测试**
   - 测试正常播放流程
   - 测试设备不可用情况（错误处理）
   - 测试播放中断处理（错误处理）

5. **集成测试**
   - 测试客户端-服务器连接
   - 测试消息广播功能
   - 测试10MB大小限制（边界条件）

### 基于属性的测试（Property-Based Testing）

属性测试验证系统在大量随机输入下的通用正确性规则。每个属性测试应该：

- 运行至少100次迭代（由于随机化）
- 使用注释标记对应的设计属性
- 标记格式：`# Feature: audio-communication, Property N: [property_text]`


**属性测试配置**：

```python
from hypothesis import given, strategies as st, settings

# 配置每个测试运行100次
@settings(max_examples=100)
```

**测试策略生成器**：

```python
# 生成随机音频数据
audio_data_strategy = st.binary(min_size=1024, max_size=1024*1024)

# 生成随机文件名
filename_strategy = st.text(
    alphabet=st.characters(blacklist_categories=('Cs',)),
    min_size=1,
    max_size=255
)

# 生成随机JSON消息
message_strategy = st.fixed_dictionaries({
    'type': st.just('audio'),
    'filename': filename_strategy,
    'length': st.integers(min_value=0),
    'data': st.text()
})
```

**属性测试映射**：

1. **Property 1-3**: 测试编解码和协议round-trip
   - 使用Hypothesis生成随机音频数据
   - 验证编码-解码循环的一致性

2. **Property 4**: 测试消息验证
   - 生成缺少字段的消息
   - 验证所有情况都抛出ProtocolError

3. **Property 5**: 测试WAV头部
   - 生成随机音频数据
   - 验证编码输出的头部格式

4. **Property 6-9**: 测试组件行为
   - 使用mock对象模拟pyaudio和socket
   - 验证接口调用的正确性

5. **Property 10-14**: 测试错误处理
   - 生成各种无效输入
   - 验证所有情况都正确抛出异常


### 测试覆盖目标

- 代码覆盖率：>80%
- 属性测试覆盖所有15个正确性属性
- 单元测试覆盖所有边界条件和错误路径
- 集成测试覆盖端到端流程

### 测试执行

```bash
# 运行所有测试
pytest tests/ -v

# 运行属性测试（带详细输出）
pytest tests/test_properties.py -v --hypothesis-show-statistics

# 运行单元测试
pytest tests/test_units.py -v

# 生成覆盖率报告
pytest tests/ --cov=任务2 --cov-report=html
```

### Mock策略

由于系统依赖外部硬件（音频设备）和网络，测试中需要使用mock：

1. **pyaudio mock**: 模拟音频流的打开、读取、写入和关闭
2. **socket mock**: 模拟TCP连接、发送和接收
3. **wave mock**: 在某些测试中模拟文件操作

示例：
```python
from unittest.mock import Mock, patch

@patch('pyaudio.PyAudio')
def test_audio_recorder(mock_pyaudio):
    mock_stream = Mock()
    mock_pyaudio.return_value.open.return_value = mock_stream
    mock_stream.read.return_value = b'\x00' * 1024
    
    recorder = AudioRecorder()
    recorder.start_recording()
    data = recorder.stop_recording()
    
    assert len(data) > 0
    mock_stream.close.assert_called_once()
```

这种双重测试策略确保：
- 单元测试捕获具体的bug和边界情况
- 属性测试验证通用的正确性规则
- 两者结合提供全面的质量保证
