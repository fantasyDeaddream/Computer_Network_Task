"""
任务3 实时音频流服务器启动脚本（命令行）

运行此脚本即可在服务器上直接启动任务3的实时音频流服务器，
无需图形界面（Tkinter）。
"""

from server import main as run_server


def main() -> None:
    """启动服务器的简单包装。"""
    run_server()


if __name__ == "__main__":
    main()

