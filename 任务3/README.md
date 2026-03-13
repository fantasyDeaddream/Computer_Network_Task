## 任务3（Tkinter 图形界面版）

本目录基于`任务2`的实现提供图形界面（GUI），**不修改**`任务2`中的任何文件。

### 运行方式

- 启动服务端 GUI：

```bash
python server_gui_start.py
```

- 启动客户端 GUI：

```bash
python client_gui_start.py
```

### 说明

- 服务端：由于`任务2`的`AudioServer.start()`为阻塞循环且没有公开“停止”接口，GUI 通过**子进程**启动/停止服务端，保证“停止服务器”按钮可用。
- 客户端：直接复用`任务2`的`AudioClient`（连接/收发文本/录音发送/播放接收音频），并把控制台输出重定向到 GUI 日志窗口。

