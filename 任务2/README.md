# 基于TCP的网络音频通信系统

## 项目概述

本项目实现了基于TCP的网络音频通信系统，在任务1文本聊天室的基础上扩展了音频录制、传输和播放功能。系统采用客户端-服务器架构，支持多用户实时音频和文本通信。

### 核心特性

- **音频录制**: 使用pyaudio采集16kHz单声道音频
- **音频播放**: 自动播放接收到的音频消息
- **音频传输**: 基于TCP的可靠音频数据传输
- **文本消息**: 支持文本聊天功能
- **多用户支持**: 服务器支持多客户端并发连接
- **错误容错**: 优雅处理各类异常情况

## 项目结构

```
任务2/
├── __init__.py              # 包初始化文件
│
├── 核心音频模块
│   ├── audio_config.py      # 音频配置常量（采样率、声道数等）
│   ├── audio_encoder.py     # 音频编码器（原始数据→WAV格式）
│   ├── audio_decoder.py     # 音频解码器（WAV格式→原始数据）
│   ├── audio_protocol.py    # 音频传输协议（JSON封装/解析）
│   ├── audio_recorder.py    # 音频录制器（pyaudio采集）
│   └── audio_player.py      # 音频播放器（pyaudio播放）
│
├── 异常处理
│   └── custom_exceptions.py # 自定义异常类
│
├── 客户端和服务器
│   ├── qcli.py              # 客户端程序（音频+文本通信）
│   ├── qcli_start.py        # 客户端启动脚本
│   ├── qser.py              # 服务器程序（多线程处理）
│   └── qser_start.py        # 服务器启动脚本
│
├── 测试
│   ├── pytest.ini           # pytest配置文件
│   └── tests/               # 测试目录
│       ├── __init__.py
│       ├── test_audio_encoder.py
│       ├── test_audio_decoder.py
│       ├── test_audio_protocol.py
│       ├── test_audio_recorder.py
│       ├── test_audio_player.py
│       └── test_audio_server.py
│
├── 演示脚本
│   ├── demo_recorder.py     # 录音功能演示
│   ├── demo_player.py       # 播放功能演示
│   ├── demo_protocol.py     # 协议封装演示
│   ├── demo_decoder.py      # 解码功能演示
│   └── demo_client.py       # 客户端功能演示
│
└── 文档
    ├── README.md            # 项目文档（本文件）
    ├── INSTALL_PYAUDIO.md   # PyAudio详细安装指南
    ├── requirements.txt     # Python依赖列表
    ├── CLIENT_IMPLEMENTATION.md  # 客户端实现文档
    ├── SERVER_IMPLEMENTATION.md  # 服务器实现文档
    └── TASK9_COMPLETION_SUMMARY.md  # 任务完成总结
```

### 文件组织说明

- **核心音频模块**: 独立的音频处理组件，可复用
- **客户端和服务器**: 主程序入口，集成所有功能
- **测试**: 单元测试和属性测试，确保代码质量
- **演示脚本**: 独立的功能演示，便于理解各模块
- **文档**: 完整的项目文档和安装指南

## 安装依赖

### 快速安装

```bash
# 进入项目目录
cd 任务2

# 安装所有依赖（除PyAudio外）
pip install pytest pytest-cov hypothesis flake8

# 安装PyAudio（可选，见下方详细说明）
pip install pyaudio
```

### 依赖说明

| 依赖包 | 版本要求 | 用途 | 是否必需 |
|--------|---------|------|---------|
| pyaudio | >=0.2.11 | 音频采集和播放 | 运行时必需* |
| pytest | >=7.0.0 | 测试框架 | 开发时必需 |
| pytest-cov | >=4.0.0 | 测试覆盖率报告 | 开发时可选 |
| hypothesis | >=6.0.0 | 基于属性的测试 | 开发时可选 |
| flake8 | >=6.0.0 | 代码质量检查 | 开发时可选 |

*注：PyAudio仅在实际使用音频功能时必需，测试和开发可暂不安装。

### PyAudio 安装详细指南

PyAudio需要C++编译工具，安装可能遇到问题。我们提供了多种解决方案：

#### Windows系统

**方案1：使用预编译wheel文件（推荐）**

```bash
# 访问以下网站下载对应Python版本的wheel文件
# https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio

# 例如，Python 3.11 64位：
# 下载 PyAudio‑0.2.14‑cp311‑cp311‑win_amd64.whl

# 安装下载的文件
pip install PyAudio‑0.2.14‑cp311‑cp311‑win_amd64.whl
```

**方案2：安装Microsoft C++ Build Tools**

```bash
# 1. 下载并安装 Build Tools for Visual Studio
#    https://visualstudio.microsoft.com/visual-cpp-build-tools/
# 2. 选择 "C++ build tools" 工作负载
# 3. 安装完成后运行：
pip install pyaudio
```

**方案3：使用pipwin**

```bash
pip install pipwin
pipwin install pyaudio
```

#### macOS系统

```bash
# 使用Homebrew安装PortAudio
brew install portaudio

# 安装PyAudio
pip install pyaudio
```

#### Linux系统

```bash
# Ubuntu/Debian
sudo apt-get install portaudio19-dev python3-pyaudio
pip install pyaudio

# Fedora/CentOS
sudo dnf install portaudio-devel
pip install pyaudio
```

#### 使用Anaconda

```bash
conda install -c anaconda pyaudio
```

### 验证安装

```bash
# 验证PyAudio安装
python -c "import pyaudio; print('PyAudio version:', pyaudio.__version__)"

# 验证测试框架
pytest --version

# 验证所有依赖
pip list | grep -E "pyaudio|pytest|hypothesis|flake8"
```

### 安装问题排查

如果PyAudio安装失败：

1. **检查Python版本**: 确保使用Python 3.7+
2. **更新pip**: `pip install --upgrade pip`
3. **查看详细错误**: `pip install pyaudio -v`
4. **参考详细指南**: 查看 [INSTALL_PYAUDIO.md](INSTALL_PYAUDIO.md)

**临时解决方案**: 项目已配置为在PyAudio未安装时仍可运行测试，实际使用音频功能时再安装。

## 音频参数配置

系统使用统一的音频参数（定义在 `audio_config.py`）：

- 采样率：16000 Hz
- 声道数：1（单声道）
- 采样宽度：2字节（16位）
- 缓冲区大小：1024帧
- 最大录音时长：60秒
- 最大音频大小：10MB

## 服务器架构

音频服务器（`qser.py`）基于任务1的TCP服务器实现，主要特性：

### 核心功能
- **多线程处理**: 为每个客户端连接创建独立线程
- **消息广播**: 支持文本和音频消息的广播
- **协议解析**: 自动识别和处理不同类型的消息（文本/音频/登录/登出）
- **错误处理**: 优雅处理各类异常，不影响其他客户端

### 消息处理流程
1. 客户端连接后发送登录消息
2. 服务器分配用户ID并创建处理线程
3. 接收客户端消息（使用换行符分隔）
4. 解析消息类型并相应处理
5. 广播消息给其他客户端（排除发送者）

### 错误处理策略
- **ProtocolError**: 记录错误，继续处理其他消息
- **ConnectionError**: 清理失效连接，通知其他用户
- **JSONDecodeError**: 记录错误，跳过无效消息
- 所有错误都记录到控制台，包含时间戳和详细信息

### 线程安全
- 使用 `threading.Lock` 保护共享的连接列表
- 所有广播操作都在锁保护下进行
- 避免竞态条件和数据不一致

## 运行测试

### 测试框架

项目使用pytest作为测试框架，支持单元测试和基于属性的测试（Hypothesis）。

### 运行所有测试

```bash
# 基本测试运行
pytest

# 详细输出
pytest -v

# 显示测试覆盖率
pytest --cov=. --cov-report=term-missing

# 生成HTML覆盖率报告
pytest --cov=. --cov-report=html
# 报告生成在 htmlcov/index.html
```

### 运行特定测试

```bash
# 运行单元测试
pytest -m unit

# 运行属性测试
pytest -m property

# 运行特定文件的测试
pytest tests/test_audio_encoder.py

# 运行特定测试函数
pytest tests/test_audio_encoder.py::test_encode_to_wav

# 运行包含特定关键字的测试
pytest -k "encoder"
```

### 测试输出示例

```
======================== test session starts ========================
platform win32 -- Python 3.11.0, pytest-7.4.0, pluggy-1.3.0
rootdir: /path/to/任务2
plugins: cov-4.1.0, hypothesis-6.92.0
collected 45 items

tests/test_audio_encoder.py ....                              [  8%]
tests/test_audio_decoder.py ....                              [ 17%]
tests/test_audio_protocol.py .......                          [ 32%]
tests/test_audio_recorder.py ...                              [ 39%]
tests/test_audio_player.py ...                                [ 46%]
tests/test_audio_server.py ........................           [100%]

======================== 45 passed in 2.34s =========================
```

### 代码质量检查

```bash
# 运行flake8检查代码风格
flake8 *.py tests/

# 检查特定文件
flake8 qcli.py qser.py
```

## 自定义异常

系统定义了以下自定义异常（在 `custom_exceptions.py` 中）：

- **AudioDeviceError**: 音频设备错误
- **EncodingError**: 编码错误
- **DecodingError**: 解码错误
- **ProtocolError**: 协议错误
- **PlaybackError**: 播放错误

## 使用说明

### 快速开始

1. **启动服务器**（在一个终端窗口）

```bash
cd 任务2
python qser_start.py
```

2. **启动客户端**（在另一个终端窗口）

```bash
cd 任务2
python qcli_start.py
```

3. **开始通信**

```
# 发送文本消息
Hello everyone!

# 录制并发送音频
/record
（说话...）
/stop
```

### 启动服务器

服务器默认监听所有网络接口的8880端口。

```bash
# 基本启动
python qser_start.py

# 或直接运行
python qser.py
```

**服务器启动输出示例**：

```
==================================================
音频通信服务器
==================================================

服务器将在端口 8880 上监听连接...
按 Ctrl+C 停止服务器

[2024-01-15 10:30:45] INFO: 服务器正在运行，监听 0.0.0.0:8880
[2024-01-15 10:31:20] INFO: 新客户端连接: 192.168.1.100:54321
[2024-01-15 10:31:21] INFO: 用户 user_1 已登录
```

**服务器配置**：

- **监听地址**: 0.0.0.0（所有网络接口）
- **监听端口**: 8880
- **最大连接数**: 无限制（受系统资源限制）
- **线程模型**: 每个客户端一个独立线程

**停止服务器**：

- 按 `Ctrl+C` 优雅关闭
- 服务器会通知所有客户端并关闭连接

### 启动客户端

客户端支持多种启动方式：

```bash
# 方式1：使用默认配置（localhost:8880）
python qcli_start.py

# 方式2：指定服务器地址
python qcli_start.py 192.168.1.100

# 方式3：指定服务器地址和端口
python qcli_start.py 192.168.1.100 8880

# 方式4：直接运行（使用默认配置）
python qcli.py
```

**客户端启动输出示例**：

```
==================================================
音频通信客户端
==================================================

正在连接到服务器 localhost:8880...
连接成功！

欢迎使用音频通信客户端
命令:
  /record 或 /r  - 开始录音
  /stop 或 /s    - 停止录音并发送
  /quit 或 /q    - 退出程序
  其他文本       - 发送文本消息

开始聊天吧！
==================================================
```

### 命令行命令

客户端支持以下命令：

| 命令 | 简写 | 功能 | 说明 |
|------|------|------|------|
| `/record` | `/r` | 开始录音 | 启动音频录制，最长60秒 |
| `/stop` | `/s` | 停止录音并发送 | 结束录制并自动发送音频 |
| `/quit` | `/q` | 退出程序 | 断开连接并关闭客户端 |
| 其他文本 | - | 发送文本消息 | 直接输入文本并回车发送 |

### 使用示例

#### 示例1：发送文本消息

```
> Hello everyone!
[You] Hello everyone!

[user_2] Hi there!
```

#### 示例2：录制并发送音频

```
> /record
[Audio] 开始录音... (输入 /stop 或 /s 停止)

> /stop
[Audio] 停止录音...
[Audio] 编码音频数据...
[Audio] 封装消息...
[Audio] 音频已发送 (12345 bytes)
```

#### 示例3：接收并播放音频

```
[Audio] 接收到音频: recording_1_20240115_103045.wav
[Audio] 正在播放...
[Audio] 播放完成
```

#### 示例4：混合使用文本和音频

```
> /r
[Audio] 开始录音...

> /s
[Audio] 音频已发送 (15678 bytes)

> Did you hear that?
[You] Did you hear that?

[user_2] Yes, loud and clear!

[Audio] 接收到音频: recording_2_20240115_103120.wav
[Audio] 播放完成
```

### 多客户端场景

系统支持多个客户端同时连接：

```
终端1（服务器）:
$ python qser_start.py
[INFO] 服务器正在运行，监听 0.0.0.0:8880
[INFO] 用户 user_1 已登录
[INFO] 用户 user_2 已登录
[INFO] 用户 user_3 已登录

终端2（客户端1）:
$ python qcli_start.py
> Hello from client 1!
[You] Hello from client 1!
[user_2] Hi from client 2!

终端3（客户端2）:
$ python qcli_start.py
[user_1] Hello from client 1!
> Hi from client 2!
[You] Hi from client 2!

终端4（客户端3）:
$ python qcli_start.py
[user_1] Hello from client 1!
[user_2] Hi from client 2!
```

### 退出程序

**客户端退出**：

```bash
# 方式1：使用命令
> /quit

# 方式2：使用快捷键
Ctrl+C
```

**服务器退出**：

```bash
# 按 Ctrl+C
# 服务器会：
# 1. 停止接受新连接
# 2. 通知所有客户端
# 3. 关闭所有连接
# 4. 清理资源
```

## 故障排除指南

### 常见问题

#### 1. PyAudio安装失败

**问题**: `error: Microsoft Visual C++ 14.0 or greater is required`

**解决方案**:
```bash
# 方案A: 使用预编译wheel文件（推荐）
# 访问 https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio
# 下载对应版本的.whl文件并安装

# 方案B: 安装Microsoft C++ Build Tools
# 下载并安装 Build Tools for Visual Studio
# https://visualstudio.microsoft.com/visual-cpp-build-tools/

# 方案C: 使用pipwin
pip install pipwin
pipwin install pyaudio
```

**临时解决**: 项目可在PyAudio未安装时运行测试，实际使用音频功能时再安装。

#### 2. 服务器启动失败

**问题**: `OSError: [Errno 48] Address already in use`

**原因**: 端口8880已被占用

**解决方案**:
```bash
# 查找占用端口的进程
# Windows:
netstat -ano | findstr :8880

# Linux/macOS:
lsof -i :8880

# 终止占用进程或修改服务器端口
# 在qser.py中修改PORT常量
```

#### 3. 客户端连接失败

**问题**: `ConnectionRefusedError: [Errno 61] Connection refused`

**原因**: 服务器未启动或地址/端口错误

**解决方案**:
```bash
# 1. 确认服务器已启动
python qser_start.py

# 2. 检查服务器地址和端口
# 确保客户端连接的地址和端口正确

# 3. 检查防火墙设置
# 确保端口8880未被防火墙阻止

# 4. 测试网络连接
ping <服务器地址>
telnet <服务器地址> 8880
```

#### 4. 音频设备错误

**问题**: `AudioDeviceError: 无法初始化音频设备`

**原因**: 音频设备不可用或被其他程序占用

**解决方案**:
```bash
# 1. 检查音频设备
# Windows: 控制面板 -> 声音 -> 录制/播放
# macOS: 系统偏好设置 -> 声音
# Linux: alsamixer 或 pavucontrol

# 2. 关闭占用音频设备的其他程序

# 3. 重启音频服务
# Windows: 重启 Windows Audio 服务
# Linux: pulseaudio -k && pulseaudio --start

# 4. 测试PyAudio
python -c "import pyaudio; p = pyaudio.PyAudio(); print(p.get_device_count())"
```

#### 5. 录音无声音

**问题**: 录音完成但播放无声音

**解决方案**:
```bash
# 1. 检查麦克风权限
# Windows: 设置 -> 隐私 -> 麦克风
# macOS: 系统偏好设置 -> 安全性与隐私 -> 麦克风

# 2. 检查麦克风音量
# 确保麦克风未静音且音量适当

# 3. 测试麦克风
# 使用系统自带的录音工具测试

# 4. 运行演示脚本
python demo_recorder.py
```

#### 6. 音频播放失败

**问题**: `PlaybackError: 播放过程中发生错误`

**解决方案**:
```bash
# 1. 检查扬声器/耳机连接

# 2. 检查音频格式
# 确保音频数据格式正确（16kHz, 单声道, 16位）

# 3. 测试播放功能
python demo_player.py

# 4. 检查音频数据完整性
# 确保接收到的音频数据未损坏
```

#### 7. 消息格式错误

**问题**: `ProtocolError: 消息格式无效`

**原因**: JSON消息格式不正确或缺少必需字段

**解决方案**:
```bash
# 1. 检查消息格式
# 音频消息必须包含: type, filename, length, data

# 2. 验证JSON格式
python -c "import json; json.loads(your_message)"

# 3. 运行协议演示
python demo_protocol.py

# 4. 查看服务器日志
# 服务器会记录详细的错误信息
```

#### 8. 网络传输中断

**问题**: 音频传输过程中连接断开

**解决方案**:
```bash
# 1. 检查网络稳定性
ping <服务器地址> -t

# 2. 检查音频大小限制
# 单个音频文件不应超过10MB

# 3. 检查录音时长
# 录音时长限制为60秒

# 4. 查看错误日志
# 客户端和服务器都会记录错误信息
```

#### 9. 测试失败

**问题**: pytest运行失败

**解决方案**:
```bash
# 1. 更新测试依赖
pip install --upgrade pytest pytest-cov hypothesis

# 2. 清理缓存
pytest --cache-clear
rm -rf .pytest_cache __pycache__

# 3. 运行单个测试文件
pytest tests/test_audio_encoder.py -v

# 4. 查看详细错误
pytest -vv --tb=long
```

#### 10. 编码/解码错误

**问题**: `EncodingError` 或 `DecodingError`

**解决方案**:
```bash
# 1. 检查音频数据格式
# 确保使用正确的采样率、声道数和采样宽度

# 2. 验证WAV格式
# 使用音频编辑软件检查WAV文件

# 3. 运行编解码演示
python demo_decoder.py

# 4. 检查数据完整性
# 确保音频数据未被截断或损坏
```

### 调试技巧

#### 启用详细日志

在代码中添加调试输出：

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

#### 使用演示脚本

项目提供了多个演示脚本用于测试各个模块：

```bash
# 测试录音功能
python demo_recorder.py

# 测试播放功能
python demo_player.py

# 测试协议封装
python demo_protocol.py

# 测试解码功能
python demo_decoder.py

# 测试完整流程
python demo_client.py
```

#### 检查系统资源

```bash
# 检查端口占用
netstat -an | grep 8880

# 检查进程
ps aux | grep python

# 检查音频设备
python -c "import pyaudio; p = pyaudio.PyAudio(); [print(p.get_device_info_by_index(i)) for i in range(p.get_device_count())]"
```

### 获取帮助

如果问题仍未解决：

1. **查看详细文档**: 阅读 `CLIENT_IMPLEMENTATION.md` 和 `SERVER_IMPLEMENTATION.md`
2. **查看PyAudio安装指南**: 参考 `INSTALL_PYAUDIO.md`
3. **检查错误日志**: 服务器和客户端都会输出详细的错误信息
4. **运行测试**: `pytest -v` 可以帮助定位问题
5. **查看任务完成总结**: 参考 `TASK9_COMPLETION_SUMMARY.md`

## 开发状态

当前已完成：
- ✅ 项目结构搭建
- ✅ 音频配置定义
- ✅ 自定义异常类
- ✅ 测试框架设置
- ✅ 音频编码器（AudioEncoder）
- ✅ 音频解码器（AudioDecoder）
- ✅ 音频传输协议（AudioProtocol）
- ✅ 音频录制器（AudioRecorder）
- ✅ 音频播放器（AudioPlayer）
- ✅ 客户端程序（qcli.py）
- ✅ 服务器程序（qser.py）
- ✅ 服务器单元测试

待实现：
- ⏳ 完整的属性测试套件
- ⏳ 端到端集成测试

## 技术细节

### 音频参数配置

系统使用统一的音频参数（定义在 `audio_config.py`）：

| 参数 | 值 | 说明 |
|------|-----|------|
| 采样率 | 16000 Hz | 适合语音通信的采样率 |
| 声道数 | 1（单声道） | 减少数据量，适合语音 |
| 采样宽度 | 2字节（16位） | 标准音频质量 |
| 音频格式 | pyaudio.paInt16 | 16位整数格式 |
| 缓冲区大小 | 1024帧 | 平衡延迟和性能 |
| 最大录音时长 | 60秒 | 防止过长录音 |
| 最大音频大小 | 10MB | 网络传输限制 |

### 消息协议

#### 音频消息格式

```json
{
    "type": "audio",
    "filename": "recording_1_20240115_103045.wav",
    "length": 12345,
    "data": "UklGRiQAAABXQVZFZm10IBAAAAABAAEA..."
}
```

字段说明：
- `type`: 消息类型，固定为 "audio"
- `filename`: 音频文件名，最多255字符
- `length`: 音频数据长度（字节）
- `data`: base64编码的WAV格式音频数据

#### 文本消息格式

```json
{
    "type": "text",
    "content": "Hello everyone!"
}
```

#### 登录消息格式

```json
{
    "type": "login",
    "username": "user_1"
}
```

#### 登出消息格式

```json
{
    "type": "logout",
    "username": "user_1"
}
```

### 架构设计

系统采用分层架构：

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

### 服务器架构

#### 核心功能
- **多线程处理**: 为每个客户端连接创建独立线程
- **消息广播**: 支持文本和音频消息的广播
- **协议解析**: 自动识别和处理不同类型的消息
- **错误处理**: 优雅处理各类异常，不影响其他客户端

#### 消息处理流程
1. 客户端连接后发送登录消息
2. 服务器分配用户ID并创建处理线程
3. 接收客户端消息（使用换行符分隔）
4. 解析消息类型并相应处理
5. 广播消息给其他客户端（排除发送者）

#### 错误处理策略
- **ProtocolError**: 记录错误，继续处理其他消息
- **ConnectionError**: 清理失效连接，通知其他用户
- **JSONDecodeError**: 记录错误，跳过无效消息
- 所有错误都记录到控制台，包含时间戳和详细信息

#### 线程安全
- 使用 `threading.Lock` 保护共享的连接列表
- 所有广播操作都在锁保护下进行
- 避免竞态条件和数据不一致

### 客户端架构

#### 核心组件
- **AudioClient**: 主客户端类，管理连接和消息处理
- **AudioRecorder**: 音频录制管理
- **AudioPlayer**: 音频播放管理
- **消息接收线程**: 独立线程处理服务器消息

#### 工作流程

**录音并发送**:
1. 用户输入 `/record` 命令
2. AudioRecorder开始采集音频
3. 用户输入 `/stop` 命令
4. AudioEncoder编码为WAV格式
5. AudioProtocol封装为JSON消息
6. 通过TCP连接发送（添加换行符）

**接收并播放**:
1. 接收线程收到音频消息
2. AudioProtocol解析JSON消息
3. AudioDecoder解码WAV数据
4. AudioPlayer播放音频

### 自定义异常

系统定义了以下自定义异常（在 `custom_exceptions.py` 中）：

| 异常类 | 触发场景 | 处理方式 |
|--------|---------|---------|
| AudioDeviceError | 音频设备不可用 | 提示用户检查设备 |
| EncodingError | WAV编码失败 | 记录错误，丢弃录音 |
| DecodingError | WAV解码失败 | 记录错误，跳过消息 |
| ProtocolError | 消息格式无效 | 记录错误，继续处理 |
| PlaybackError | 播放过程错误 | 停止播放，清理资源 |

### 性能特性

- **低延迟**: 使用1024帧缓冲区，延迟约64ms
- **高效编码**: 使用标准WAV格式，无需复杂编解码
- **并发支持**: 多线程架构支持多客户端同时连接
- **资源管理**: 自动清理音频流和网络连接

### 安全考虑

- **大小限制**: 音频文件限制10MB，防止内存溢出
- **时长限制**: 录音限制60秒，防止过长录音
- **错误隔离**: 单个客户端错误不影响其他客户端
- **资源清理**: 异常情况下自动清理资源

## 演示脚本

项目提供了多个独立的演示脚本，用于测试和理解各个模块：

| 脚本 | 功能 | 用途 |
|------|------|------|
| demo_recorder.py | 录音功能演示 | 测试AudioRecorder |
| demo_player.py | 播放功能演示 | 测试AudioPlayer |
| demo_protocol.py | 协议封装演示 | 测试AudioProtocol |
| demo_decoder.py | 解码功能演示 | 测试AudioDecoder |
| demo_client.py | 客户端功能演示 | 测试完整流程 |

运行演示脚本：

```bash
# 录音演示
python demo_recorder.py

# 播放演示
python demo_player.py

# 协议演示
python demo_protocol.py

# 解码演示
python demo_decoder.py

# 客户端演示
python demo_client.py
```

## 许可证

本项目为教育用途。
