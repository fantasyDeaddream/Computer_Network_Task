# Requirements Document

## Introduction

本文档定义了基于TCP的网络音频通信系统的需求。该系统在任务1（文本聊天室）的基础上，扩展实现音频录制、传输和播放功能。系统使用pyaudio进行音频采集和播放，使用wave处理音频文件格式，通过TCP连接传输封装的音频数据。

## Glossary

- **Audio_System**: 音频通信系统，负责音频的录制、传输和播放
- **Audio_Recorder**: 音频录制器，使用pyaudio采集音频数据
- **Audio_Player**: 音频播放器，使用pyaudio播放接收到的音频
- **Audio_Encoder**: 音频编码器，将音频数据封装为wave格式
- **Audio_Decoder**: 音频解码器，解析wave格式的音频数据
- **Audio_Protocol**: 音频传输协议，封装文件名、数据长度和二进制数据
- **TCP_Connection**: TCP连接，复用任务1的网络通信基础
- **Audio_Message**: 音频消息，包含元数据和音频二进制数据的完整消息
- **Client**: 客户端程序，位于任务2文件夹中的qcli.py
- **Server**: 服务器程序，位于任务2文件夹中的qser.py

## Requirements

### Requirement 1: 音频录制

**User Story:** 作为用户，我希望能够录制音频，以便将语音消息发送给其他用户。

#### Acceptance Criteria

1. WHEN 用户启动录音功能 THEN THE Audio_Recorder SHALL 使用pyaudio初始化音频输入流
2. WHEN 录音进行中 THEN THE Audio_Recorder SHALL 以16位深度、16000Hz采样率、单声道格式采集音频数据
3. WHEN 用户停止录音 THEN THE Audio_Recorder SHALL 关闭音频流并返回完整的音频数据
4. WHEN 录音时长超过60秒 THEN THE Audio_Recorder SHALL 自动停止录音
5. IF 音频设备不可用 THEN THE Audio_Recorder SHALL 返回错误信息

### Requirement 2: 音频编码

**User Story:** 作为开发者，我希望将录制的音频数据编码为标准格式，以便进行网络传输和存储。

#### Acceptance Criteria

1. WHEN 接收到原始音频数据 THEN THE Audio_Encoder SHALL 使用wave库将数据编码为WAV格式
2. THE Audio_Encoder SHALL 设置音频参数为：1声道、2字节采样宽度、16000Hz采样率
3. WHEN 编码完成 THEN THE Audio_Encoder SHALL 返回包含完整WAV头部的二进制数据
4. WHEN 编码过程中发生错误 THEN THE Audio_Encoder SHALL 返回描述性错误信息

### Requirement 3: 音频传输协议

**User Story:** 作为开发者，我希望定义清晰的音频传输协议，以便客户端和服务器能够正确解析音频消息。

#### Acceptance Criteria

1. THE Audio_Protocol SHALL 封装音频消息为JSON格式，包含type、filename、length和data字段
2. WHEN 封装音频数据 THEN THE Audio_Protocol SHALL 将二进制数据编码为base64字符串
3. WHEN 解析音频消息 THEN THE Audio_Protocol SHALL 验证必需字段的存在性
4. WHEN 消息格式无效 THEN THE Audio_Protocol SHALL 返回解析错误
5. THE Audio_Protocol SHALL 支持文件名长度最多255个字符

### Requirement 4: 网络发送

**User Story:** 作为用户，我希望能够通过网络发送录制的音频，以便其他用户接收。

#### Acceptance Criteria

1. WHEN 用户发送音频 THEN THE Client SHALL 通过现有TCP连接发送封装的音频消息
2. WHEN 发送音频消息 THEN THE Client SHALL 在消息末尾添加换行符作为分隔符
3. WHEN TCP连接断开 THEN THE Client SHALL 返回连接错误
4. WHEN 发送成功 THEN THE Client SHALL 显示发送确认信息
5. IF 音频数据大于10MB THEN THE Client SHALL 拒绝发送并提示用户

### Requirement 5: 音频接收

**User Story:** 作为用户，我希望能够接收其他用户发送的音频消息，以便播放收听。

#### Acceptance Criteria

1. WHEN 接收到音频消息 THEN THE Server SHALL 解析JSON格式的消息内容
2. WHEN 解析成功 THEN THE Server SHALL 提取filename、length和data字段
3. WHEN 接收到base64编码的数据 THEN THE Server SHALL 解码为二进制音频数据
4. WHEN 消息格式错误 THEN THE Server SHALL 记录错误并继续监听
5. THE Server SHALL 将接收到的音频消息转发给所有连接的客户端

### Requirement 6: 音频解码

**User Story:** 作为开发者，我希望能够解码接收到的音频数据，以便播放器能够正确播放。

#### Acceptance Criteria

1. WHEN 接收到WAV格式的二进制数据 THEN THE Audio_Decoder SHALL 使用wave库解析音频参数
2. THE Audio_Decoder SHALL 验证音频格式为：1声道、2字节采样宽度、16000Hz采样率
3. WHEN 解码成功 THEN THE Audio_Decoder SHALL 返回原始音频帧数据
4. IF 音频格式不匹配 THEN THE Audio_Decoder SHALL 返回格式错误信息

### Requirement 7: 音频播放

**User Story:** 作为用户，我希望能够播放接收到的音频消息，以便收听其他用户的语音。

#### Acceptance Criteria

1. WHEN 接收到音频数据 THEN THE Audio_Player SHALL 使用pyaudio初始化音频输出流
2. WHEN 播放音频 THEN THE Audio_Player SHALL 使用与录制相同的音频参数（16位、16000Hz、单声道）
3. WHEN 播放完成 THEN THE Audio_Player SHALL 关闭音频流并释放资源
4. IF 音频设备不可用 THEN THE Audio_Player SHALL 返回错误信息
5. WHEN 播放过程中发生错误 THEN THE Audio_Player SHALL 停止播放并报告错误

### Requirement 8: 命令行界面

**User Story:** 作为用户，我希望通过简单的命令行指令控制音频功能，以便方便地使用系统。

#### Acceptance Criteria

1. WHEN 用户输入录音命令 THEN THE Client SHALL 启动录音功能并显示录音状态
2. WHEN 用户输入停止命令 THEN THE Client SHALL 停止录音并自动发送音频
3. WHEN 客户端接收到音频消息 THEN THE Client SHALL 自动播放音频
4. THE Client SHALL 显示清晰的操作提示信息
5. WHEN 发生错误 THEN THE Client SHALL 显示用户友好的错误消息

### Requirement 9: 代码组织

**User Story:** 作为开发者，我希望代码结构清晰且可复用，以便维护和扩展功能。

#### Acceptance Criteria

1. THE Audio_System SHALL 将所有代码存放在"任务2"文件夹中
2. THE Audio_System SHALL 复用任务1的TCP通信基础代码
3. THE Audio_System SHALL 将音频功能模块化为独立的类或函数
4. THE Audio_System SHALL 保持客户端（qcli.py）和服务器（qser.py）的独立可运行性
5. THE Audio_System SHALL 使用清晰的函数和变量命名

### Requirement 10: 错误处理

**User Story:** 作为用户，我希望系统能够优雅地处理各种错误情况，以便获得稳定的使用体验。

#### Acceptance Criteria

1. WHEN pyaudio初始化失败 THEN THE Audio_System SHALL 显示设备错误信息
2. WHEN 网络传输失败 THEN THE Audio_System SHALL 显示连接错误并保持程序运行
3. WHEN 音频编解码失败 THEN THE Audio_System SHALL 显示格式错误信息
4. WHEN 文件操作失败 THEN THE Audio_System SHALL 显示IO错误信息
5. THE Audio_System SHALL 记录所有错误到控制台以便调试
