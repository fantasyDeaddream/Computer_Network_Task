"""
任务3 实时音频流服务器（无图形界面）

说明：
- 封装 `stream_server.StreamServer`，提供命令行可直接运行的服务器入口。
- 与 `server_gui.py` 启动的是同一个底层服务器实现，只是去掉了 Tkinter 图形界面。
"""

from stream_server import StreamServer


def main() -> None:
    """主函数：在命令行下启动实时音频流服务器。"""
    server = StreamServer()
    try:
        print("=" * 50)
        print("任务3 - 实时音频流服务器（命令行版）")
        print("=" * 50)
        print()
        print("服务器正在启动...")
        print("使用端口: 默认配置中的端口（通常为 8880）")
        print("按 Ctrl+C 停止服务器。")
        print()

        server.start()
    except KeyboardInterrupt:
        print("\n[Server] 收到中断信号，正在关闭服务器...")
        server.stop()


if __name__ == "__main__":
    main()

