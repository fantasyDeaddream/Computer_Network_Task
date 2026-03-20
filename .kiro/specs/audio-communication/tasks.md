# Implementation Plan: Audio Communication System

## Overview

本实现计划将音频通信功能分解为离散的编码步骤。实现将在"任务2"文件夹中进行，复用任务1的TCP通信基础代码，并添加音频录制、编码、传输、解码和播放功能。

实现策略：
1. 首先搭建项目结构和核心音频组件
2. 实现音频编解码功能并进行测试
3. 实现协议封装层
4. 扩展客户端和服务器以支持音频功能
5. 集成所有组件并进行端到端测试

## Tasks

- [x] 1. 搭建项目结构和音频配置
  - 创建"任务2"文件夹结构
  - 创建audio_config.py定义统一的音频参数常量
  - 创建custom_exceptions.py定义所有自定义异常类
  - 设置测试框架（pytest和hypothesis）
  - _Requirements: 9.1, 9.3_

- [x] 2. 实现音频编码器（AudioEncoder）
  - [x] 2.1 实现AudioEncoder类的encode_to_wav方法
    - 使用wave库将原始音频数据编码为WAV格式
    - 设置音频参数：1声道、2字节采样宽度、16000Hz采样率
    - 使用BytesIO处理内存中的WAV数据
    - 添加错误处理，抛出EncodingError
    - _Requirements: 2.1, 2.2, 2.4_
  
  - [ ]* 2.2 编写AudioEncoder的属性测试
    - **Property 1: 音频编解码Round-Trip一致性**
    - **Property 5: WAV格式头部有效性**
    - **Property 11: 编码错误处理**
    - **Validates: Requirements 2.1, 2.3, 2.4**

- [x] 3. 实现音频解码器（AudioDecoder）
  - [x] 3.1 实现AudioDecoder类的decode_wav方法
    - 使用wave库解析WAV格式数据
    - 验证音频格式参数（1声道、2字节、16000Hz）
    - 提取原始音频帧数据
    - 添加错误处理，抛出DecodingError
    - _Requirements: 6.1, 6.2, 6.4_
  
  - [ ]* 3.2 编写AudioDecoder的属性测试
    - **Property 1: 音频编解码Round-Trip一致性**（与2.2共同验证）
    - **Property 12: 解码错误处理**
    - **Validates: Requirements 6.1, 6.3, 6.4**

- [x] 4. 实现音频传输协议（AudioProtocol）
  - [x] 4.1 实现AudioProtocol类的encode_message方法
    - 封装音频消息为JSON格式
    - 包含type、filename、length和data字段
    - 使用base64编码二进制数据
    - 验证文件名长度限制（最多255字符）
    - _Requirements: 3.1, 3.2, 3.5_
  
  - [x] 4.2 实现AudioProtocol类的decode_message方法
    - 解析JSON格式的音频消息
    - 验证必需字段的存在性
    - 解码base64数据为二进制
    - 添加错误处理，抛出ProtocolError
    - _Requirements: 3.3, 3.4_
  
  - [x] 4.3 实现AudioProtocol类的validate_message方法
    - 验证消息包含所有必需字段
    - 验证字段类型正确性
    - _Requirements: 3.3_
  
  - [ ]* 4.4 编写AudioProtocol的属性测试
    - **Property 2: 协议封装Round-Trip一致性**
    - **Property 3: Base64编码可逆性**
    - **Property 4: 消息格式验证完整性**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4**


- [x] 5. 实现音频录制器（AudioRecorder）
  - [x] 5.1 实现AudioRecorder类的初始化和start_recording方法
    - 使用pyaudio初始化音频输入流
    - 配置参数：16位、16000Hz、单声道
    - 设置chunk_size为1024
    - 添加设备错误处理，抛出AudioDeviceError
    - _Requirements: 1.1, 1.2, 1.5_
  
  - [x] 5.2 实现AudioRecorder类的stop_recording方法
    - 停止音频流并收集所有录制的帧
    - 关闭音频流并释放资源
    - 返回完整的原始音频数据
    - 实现60秒最大时长限制
    - _Requirements: 1.3, 1.4_
  
  - [x] 5.3 实现AudioRecorder类的is_recording方法
    - 返回当前录音状态
    - _Requirements: 1.1_
  
  - [ ]* 5.4 编写AudioRecorder的单元测试
    - 测试正常录音流程（使用mock）
    - 测试60秒最大时长限制
    - 测试设备不可用情况
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_
  
  - [ ]* 5.5 编写AudioRecorder的属性测试
    - **Property 6: 录音器初始化幂等性**
    - **Property 7: 录音数据非空性**
    - **Property 10: 音频设备错误处理**
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.5**

- [x] 6. 实现音频播放器（AudioPlayer）
  - [x] 6.1 实现AudioPlayer类的初始化和play方法
    - 使用pyaudio初始化音频输出流
    - 配置参数与录制相同（16位、16000Hz、单声道）
    - 播放音频数据
    - 播放完成后关闭流并释放资源
    - 添加错误处理，抛出AudioDeviceError和PlaybackError
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
  
  - [ ]* 6.2 编写AudioPlayer的单元测试
    - 测试正常播放流程（使用mock）
    - 测试设备不可用情况
    - 测试播放中断处理
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
  
  - [ ]* 6.3 编写AudioPlayer的属性测试
    - **Property 10: 音频设备错误处理**（与5.5共同验证）
    - **Validates: Requirements 7.4**

- [x] 7. Checkpoint - 确保所有核心组件测试通过
  - 运行所有单元测试和属性测试
  - 验证音频编解码、协议封装、录制和播放功能正常
  - 如有问题请询问用户


- [x] 8. 实现音频客户端（qcli.py）
  - [x] 8.1 复用任务1的TCP客户端基础代码
    - 从任务1复制客户端的TCP连接和消息接收逻辑
    - 保持JSON消息格式兼容性
    - _Requirements: 9.2, 9.4_
  
  - [x] 8.2 集成音频录制功能到客户端
    - 创建AudioClient类，包含AudioRecorder实例
    - 实现start_recording方法响应录音命令
    - 实现stop_recording_and_send方法
    - 在stop时调用AudioEncoder和AudioProtocol封装消息
    - 通过TCP连接发送音频消息（添加换行符分隔）
    - _Requirements: 4.1, 4.2, 8.1, 8.2_
  
  - [x] 8.3 集成音频播放功能到客户端
    - 创建AudioPlayer实例
    - 实现handle_received_audio方法
    - 解析接收到的音频消息
    - 使用AudioDecoder解码音频数据
    - 自动播放接收到的音频
    - _Requirements: 8.3_
  
  - [x] 8.4 实现命令行界面
    - 添加命令解析：/record, /stop, /quit
    - 显示录音状态提示
    - 显示发送确认信息
    - 实现10MB大小限制检查
    - 添加用户友好的错误消息显示
    - _Requirements: 4.5, 8.1, 8.2, 8.4, 8.5_
  
  - [x] 8.5 实现客户端错误处理
    - 捕获并处理AudioDeviceError
    - 捕获并处理ConnectionError
    - 捕获并处理EncodingError和DecodingError
    - 确保错误不会导致程序崩溃
    - 记录所有错误到控制台
    - _Requirements: 10.1, 10.2, 10.3, 10.5_
  
  - [ ]* 8.6 编写客户端的单元测试
    - 测试命令解析功能
    - 测试消息发送（使用mock socket）
    - 测试10MB大小限制
    - 测试错误处理路径
    - _Requirements: 4.1, 4.2, 4.5, 8.1, 8.2_
  
  - [ ]* 8.7 编写客户端的属性测试
    - **Property 8: 网络消息分隔符一致性**
    - **Property 13: 网络错误容错性**
    - **Validates: Requirements 4.2, 4.3, 10.2**


- [x] 9. 实现音频服务器（qser.py）
  - [x] 9.1 复用任务1的TCP服务器基础代码
    - 从任务1复制服务器的TCP监听和多线程处理逻辑
    - 保持端口8880和JSON消息格式
    - _Requirements: 9.2, 9.4_
  
  - [x] 9.2 实现音频消息接收和解析
    - 在handle_client中识别音频消息类型
    - 使用AudioProtocol.decode_message解析消息
    - 验证消息格式有效性
    - _Requirements: 5.1, 5.2_
  
  - [x] 9.3 实现音频消息广播功能
    - 修改broadcast_message方法支持音频消息
    - 转发音频消息给所有连接的客户端（排除发送者）
    - 维护客户端连接列表
    - _Requirements: 5.5_
  
  - [x] 9.4 实现服务器错误处理
    - 捕获并处理ProtocolError（消息格式错误）
    - 捕获并处理SocketError（连接失败）
    - 记录错误但继续服务其他客户端
    - 移除失效的客户端连接
    - 记录所有错误到控制台
    - _Requirements: 5.4, 10.2, 10.4, 10.5_
  
  - [ ]* 9.5 编写服务器的单元测试
    - 测试消息接收和解析
    - 测试广播功能（使用mock socket）
    - 测试客户端连接管理
    - 测试错误处理路径
    - _Requirements: 5.1, 5.4, 5.5_
  
  - [ ]* 9.6 编写服务器的属性测试
    - **Property 9: 服务器广播排除发送者**
    - **Property 14: 服务器错误恢复**
    - **Validates: Requirements 5.4, 5.5, 10.2**

- [x] 10. Checkpoint - 确保客户端和服务器集成正常
  - 运行所有客户端和服务器测试
  - 手动测试客户端-服务器连接
  - 验证消息收发功能正常
  - 如有问题请询问用户


- [ ] 11. 端到端集成测试
  - [ ]* 11.1 编写端到端属性测试
    - **Property 15: 端到端音频传输完整性**
    - 使用mock模拟完整的录制-传输-播放流程
    - 验证音频数据在整个流程中保持一致
    - **Validates: Requirements 8.3**
  
  - [ ]* 11.2 编写集成测试套件
    - 测试多客户端连接场景
    - 测试并发音频消息传输
    - 测试文本和音频消息混合传输
    - 测试客户端断开重连场景
    - _Requirements: 5.5, 8.3, 10.2_

- [x] 12. 创建项目文档和使用说明
  - 创建README.md文档
  - 说明项目结构和文件组织
  - 提供安装依赖的说明（pyaudio, wave, pytest, hypothesis）
  - 提供运行服务器和客户端的说明
  - 列出可用的命令行命令
  - 提供故障排除指南
  - _Requirements: 9.1, 9.4_

- [x] 13. 最终验证和清理
  - 运行完整的测试套件
  - 验证代码覆盖率达到>80%
  - 检查所有错误处理路径
  - 验证所有文件位于"任务2"文件夹中
  - 清理调试代码和注释
  - 确保代码符合PEP 8规范
  - _Requirements: 9.1, 9.3, 9.5_

## Notes

- 标记为`*`的任务是可选的测试任务，可以跳过以加快MVP开发
- 每个任务都引用了具体的需求条款以确保可追溯性
- Checkpoint任务确保增量验证
- 属性测试验证通用正确性属性
- 单元测试验证具体示例和边界条件
- 使用mock对象测试音频和网络功能，避免依赖实际硬件
- 所有代码应该独立可运行，存放在"任务2"文件夹中
