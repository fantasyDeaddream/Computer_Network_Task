"""
音频服务器启动脚本

运行此脚本启动音频通信服务器。
"""

from qser import AudioServer


def main():
    """启动服务器"""
    print("=" * 50)
    print("音频通信服务器")
    print("=" * 50)
    print()
    print("服务器将在端口 8880 上监听连接...")
    print("按 Ctrl+C 停止服务器")
    print()
    
    server = AudioServer()
    server.start()


if __name__ == '__main__':
    main()
