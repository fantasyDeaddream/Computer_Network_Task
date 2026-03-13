import tkinter as tk
from tkinter import ttk
import subprocess
import sys
from pathlib import Path


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("任务3 启动器")
        self.geometry("420x220")
        self.resizable(False, False)

        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="选择要启动的程序：").pack(anchor="w")

        btns = ttk.Frame(root)
        btns.pack(fill="x", pady=(14, 0))

        ttk.Button(btns, text="启动服务端 GUI", command=self._start_server).pack(fill="x")
        ttk.Button(btns, text="启动客户端 GUI", command=self._start_client).pack(fill="x", pady=(10, 0))

        ttk.Label(
            root,
            text="提示：服务端与客户端可分别在不同窗口运行。",
            justify="left",
        ).pack(anchor="w", pady=(16, 0))

    def _run_detached(self, script_name: str) -> None:
        here = Path(__file__).resolve().parent
        subprocess.Popen([sys.executable, str(here / script_name)])

    def _start_server(self) -> None:
        self._run_detached("server_gui_start.py")

    def _start_client(self) -> None:
        self._run_detached("client_gui_start.py")


def main() -> None:
    Launcher().mainloop()


if __name__ == "__main__":
    main()

