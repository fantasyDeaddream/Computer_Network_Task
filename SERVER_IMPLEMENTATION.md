# 音频服务器实现文档

## 概述

音频服务器（`qser.py`）是基于TCP的多线程服务器，支持文本和音频消息的接收与广播。服务器复用了任务1的TCP通信基础，并扩展了音频消息处理功能。

## 实现细节

### 类结构

```python
class AudioServer:
    """音频服务器"""
    
    def __init__(self, host: str = '0.0.0.0', port: int = 8880)
    def start(self)
    
    # 私有方法
    def __log(self, message: str, level: str = 'INFO')
    def __user_thread(self, user_id: int)
    def __handle_message(self, user_id: int, message: str)
    def __handle_audio_message(self, user_id: int, message: str)
    def __broadcast(self, user_id: int = 0, message: str = '')
    def __broadcast_audio(self, sender_id: int, audio_message: str)
    def __cleanup_connection(self, user_id: int, nickname: str)
    def __wait_for_login(self, connection: socket.socket)
```

### 核心功能实现

#### 1. 服务器初始化

```python
def __init__(self, host: str = '0.0.0.0', port: int = DEFAULT_PORT):
    self.__socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.__host = host
    self.__port = port
    self.__connections = list()  # 客户端连接列表
    self.__nicknames = list()    # 客户端昵称列表
    self.__lock = threading.Lock()  # 线程锁
```

#### 2. 消息接收和解析（子任务 9.2）

服务器使用缓冲区机制处理TCP流数据：

```python
buffer = ''
while True:
    data = connection.recv(4096).decode('utf-8')
    buffer += data
    
    # 处理完整消息（以换行符分隔）
    while '\n' in buffer:
        message, buffer = buffer.split('\n', 1)
        if message.strip():
            self.__handle_message(user_id, message.strip())
```

消息类型识别：

```python
def __handle_message(self, user_id: int, message: str):
    obj = json.loads(message)
    message_type = obj.get('type', '')
    
    if message_type == 'audio':
        self.__handle_audio_message(user_id, message)
    elif message_type == 'broadcast':
        self.__broadcast(user_id, obj.get('message', ''))
    elif message_type == 'logout':
        # 处理登出
```

音频消息解析：

```python
def __handle_audio_message(self, user_id: int, message: str):
    try:
        # 使用AudioProtocol验证消息格式
        filename, audio_data = AudioProtocol.decode_message(message)
        
        # 记录日志
        nickname = self.__nicknames[user_id]
        self.__log(f'接收到用户 {user_id} {nickname} 的音频消息: {filename}')
        
        # 广播给其他客户端
        self.__broadcast_audio(user_id, message)
        
    except ProtocolError as e:
        self.__log(f'音频消息格式错误: {str(e)}', 'ERROR')
```

#### 3. 音频消息广播（子任务 9.3）

广播功能排除发送者本身（满足 Property 9）：

```python
def __broadcast_audio(self, sender_id: int, audio_message: str):
    with self.__lock:  # 线程安全
        for i in range(1, len(self.__connections)):
            # 排除发送者
            if i != sender_id and self.__connections[i]:
                try:
                    # 添加换行符作为消息分隔符
                    self.__connections[i].send(
                        audio_message.encode('utf-8') + b'\n'
                    )
                except Exception as e:
                    self.__log(f'向用户 {i} 广播失败: {str(e)}', 'ERROR')
                    self.__connections[i] = None
```

文本消息广播：

```python
def __broadcast(self, user_id: int = 0, message: str = ''):
    with self.__lock:
        for i in range(1, len(self.__connections)):
            if user_id != i and self.__connections[i]:
                try:
                    self.__connections[i].send(json.dumps({
                        'sender_id': user_id,
                        'sender_nickname': self.__nicknames[user_id],
                        'message': message
                    }).encode() + b'\n')
                except Exception as e:
                    self.__log(f'向用户 {i} 发送失败: {str(e)}', 'ERROR')
```

#### 4. 错误处理（子任务 9.4）

服务器实现了多层错误处理机制（满足 Property 14）：

**协议错误处理**：
```python
try:
    filename, audio_data = AudioProtocol.decode_message(message)
    # 处理音频消息
except ProtocolError as e:
    # 记录错误但不中断服务
    self.__log(f'音频消息格式错误: {str(e)}', 'ERROR')
```

**JSON解析错误处理**：
```python
try:
    obj = json.loads(message)
    # 处理消息
except json.JSONDecodeError as e:
    self.__log(f'无法解析JSON数据: {str(e)}', 'ERROR')
```

**连接错误处理**：
```python
try:
    connection.send(data)
except Exception as e:
    self.__log(f'发送失败: {str(e)}', 'ERROR')
    # 标记连接为失效
    self.__connections[i] = None
```

**连接清理**：
```python
def __cleanup_connection(self, user_id: int, nickname: str):
    with self.__lock:
        if self.__connections[user_id]:
            try:
                self.__connections[user_id].close()
            except Exception:
                pass
            self.__connections[user_id] = None
            self.__nicknames[user_id] = None
    
    self.__log(f'用户 {user_id} {nickname} 连接已清理')
    self.__broadcast(message=f'用户 {nickname}({user_id}) 离开聊天室')
```

### 日志系统

服务器实现了统一的日志记录：

```python
def __log(self, message: str, level: str = 'INFO'):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{timestamp}] {level}: {message}')
```

日志级别：
- **INFO**: 正常操作（用户加入/离开、消息接收）
- **WARNING**: 警告信息（未知消息类型、连接重置）
- **ERROR**: 错误信息（解析失败、发送失败）

### 线程安全

服务器使用 `threading.Lock` 确保线程安全：

1. **连接列表保护**: 所有对 `__connections` 和 `__nicknames` 的修改都在锁保护下
2. **广播操作**: 所有广播操作都在锁保护下进行
3. **连接清理**: 清理操作使用锁避免竞态条件

## 满足的需求

### Requirements 9.2（复用任务1代码）
- ✅ 复用TCP服务器基础架构
- ✅ 保持端口8880和JSON消息格式
- ✅ 保持多线程处理模型

### Requirements 5.1, 5.2（音频接收和解析）
- ✅ 解析JSON格式的音频消息
- ✅ 提取filename、length和data字段
- ✅ 解码base64编码的数据

### Requirements 5.5（音频广播）
- ✅ 转发音频消息给所有连接的客户端
- ✅ 排除发送者本身（Property 9）

### Requirements 5.4, 10.2, 10.5（错误处理）
- ✅ 捕获并处理ProtocolError
- ✅ 捕获并处理SocketError
- ✅ 记录错误但继续服务其他客户端
- ✅ 移除失效的客户端连接
- ✅ 记录所有错误到控制台

## 验证的属性

### Property 9: 服务器广播排除发送者
```python
# 测试验证：发送者不会收到自己发送的消息
def test_server_broadcast_excludes_sender():
    server._AudioServer__broadcast_audio(1, audio_message)
    mock_conn1.send.assert_not_called()  # 发送者
    mock_conn2.send.assert_called_once()  # 其他用户
```

### Property 14: 服务器错误恢复
```python
# 测试验证：格式错误不会导致服务器崩溃
def test_server_handles_protocol_error():
    invalid_message = '{"type":"audio","filename":"test.wav"}'
    server._AudioServer__handle_audio_message(1, invalid_message)
    # 不抛出异常，继续运行
```

### Property 8: 网络消息分隔符一致性
```python
# 所有发送的消息都以换行符结尾
self.__connections[i].send(message.encode('utf-8') + b'\n')
```

## 使用示例

### 启动服务器

```bash
python qser_start.py
```

输出：
```
==================================================
音频通信服务器
==================================================

服务器将在端口 8880 上监听连接...
按 Ctrl+C 停止服务器

[2024-01-15 10:30:45] INFO: 服务器正在运行，监听 0.0.0.0:8880
[2024-01-15 10:31:20] INFO: 收到新连接: ('192.168.1.100', 54321)
[2024-01-15 10:31:21] INFO: 新用户登录: Alice (ID: 1)
[2024-01-15 10:31:21] INFO: 用户 1 Alice 加入聊天室
[2024-01-15 10:32:10] INFO: 接收到用户 1 Alice 的音频消息: recording_001.wav (12345 bytes)
```

### 测试服务器

```bash
cd 任务2
python -m pytest tests/test_audio_server.py -v
```

## 性能考虑

1. **缓冲区大小**: 使用4096字节接收缓冲区，平衡性能和内存使用
2. **线程模型**: 每个客户端一个线程，适合中小规模应用
3. **锁粒度**: 使用单一锁保护共享数据，简单但可能成为瓶颈
4. **消息分隔**: 使用换行符分隔消息，简单高效

## 改进建议

1. **连接池**: 限制最大连接数，防止资源耗尽
2. **心跳机制**: 定期检测客户端存活状态
3. **消息队列**: 使用队列解耦接收和广播，提高并发性能
4. **异步IO**: 使用asyncio替代多线程，提高可扩展性
5. **消息确认**: 添加消息确认机制，确保可靠传输

## 总结

音频服务器成功实现了所有子任务要求：

- ✅ 9.1: 复用任务1的TCP服务器基础代码
- ✅ 9.2: 实现音频消息接收和解析
- ✅ 9.3: 实现音频消息广播功能
- ✅ 9.4: 实现服务器错误处理

服务器具有良好的错误处理能力，能够优雅地处理各种异常情况，确保服务的稳定性和可靠性。
