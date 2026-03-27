"""
任务5 - 多方语音会议系统服务器图形界面
"""

import queue
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import sys
from pathlib import Path


def _ensure_task4_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task4_dir = base / "任务4"
    if str(task4_dir) not in sys.path:
        sys.path.insert(0, str(task4_dir))


_ensure_task4_on_path()
from tk_utils import redirect_stdout_to_queue

from conference_server import ConferenceServer, DEFAULT_PORT


class ServerGUI(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("任务5 - 多方语音会议系统服务器")
        self.geometry("860x560")
        self.minsize(820, 520)

        self._log_q: queue.Queue[str] = queue.Queue()
        self._redirect = redirect_stdout_to_queue(self._log_q)

        self._server = None
        self._server_thread = None
        self._ui_poll_job = None

        self._build_ui()
        self._set_running(False)
        self._start_log_pump()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")

        cfg = ttk.Labelframe(top, text="监听配置", padding=10)
        cfg.pack(side="left", fill="x", expand=True)

        ttk.Label(cfg, text="端口").grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(cfg, textvariable=self.port_var, width=10).grid(
            row=0, column=1, padx=(8, 16), sticky="w"
        )

        self._lamp_canvas = tk.Canvas(cfg, width=18, height=18, highlightthickness=0)
        self._lamp_canvas.grid(row=0, column=2, padx=(6, 8))
        self._lamp = self._lamp_canvas.create_oval(
            2, 2, 16, 16, fill="#b0b0b0", outline="#808080"
        )

        self.status_var = tk.StringVar(value="未运行")
        ttk.Label(cfg, textvariable=self.status_var).grid(row=0, column=3, sticky="w")
        cfg.columnconfigure(4, weight=1)

        ctrl = ttk.Labelframe(top, text="控制", padding=10)
        ctrl.pack(side="right", fill="y", padx=(12, 0))

        self.btn_start = ttk.Button(ctrl, text="启动服务器", command=self._on_start)
        self.btn_start.grid(row=0, column=0, sticky="ew")

        self.btn_stop = ttk.Button(ctrl, text="停止服务器", command=self._on_stop)
        self.btn_stop.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.btn_clear = ttk.Button(ctrl, text="清空日志", command=self._on_clear_log)
        self.btn_clear.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        ctrl.columnconfigure(0, weight=1)

        log = ttk.Labelframe(outer, text="服务端输出日志", padding=10)
        log.pack(fill="both", expand=True, pady=(12, 0))

        self.log_text = tk.Text(log, wrap="word", height=20)
        self.log_text.pack(fill="both", expand=True)

        hint = "说明：本窗口启动的是任务5实现的多方语音会议系统服务器。"
        ttk.Label(outer, text=hint, justify="left").pack(anchor="w", pady=(10, 0))

    def _set_lamp(self, color):
        self._lamp_canvas.itemconfig(self._lamp, fill=color)

    def _set_running(self, running):
        if running:
            self._set_lamp("#3ad04a")
            self.status_var.set("运行中")
        else:
            self._set_lamp("#b0b0b0")
            self.status_var.set("未运行")
        self.btn_start.config(state=("disabled" if running else "normal"))
        self.btn_stop.config(state=("normal" if running else "disabled"))

    def _start_log_pump(self):
        def pump():
            drained = False
            try:
                while True:
                    s = self._log_q.get_nowait()
                    self.log_text.insert("end", s)
                    drained = True
            except queue.Empty:
                pass
            if drained:
                self.log_text.see("end")
            self._ui_poll_job = self.after(60, pump)

        pump()

    def _on_clear_log(self):
        self.log_text.delete("1.0", "end")

    def _on_start(self):
        if self._server:
            return
        port = int(self.port_var.get() or DEFAULT_PORT)

        def spawn():
            try:
                self._server = ConferenceServer(port=port)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("启动失败", str(e)))
                self.after(0, lambda: self._set_running(False))
                return
            self.after(0, lambda: self._set_running(True))
            self._server.start()

        self._server_thread = threading.Thread(target=spawn, daemon=True)
        self._server_thread.start()

    def _on_stop(self):
        if self._server:
            try:
                self._server.stop()
            except Exception:
                pass
            self._server = None
        self._set_running(False)

    def _on_close(self):
        try:
            if self._server:
                self._server.stop()
        except Exception:
            pass
        try:
            if self._ui_poll_job:
                self.after_cancel(self._ui_poll_job)
        except Exception:
            pass
        try:
            self._redirect.restore()
        finally:
            self.destroy()


def run_app():
    app = ServerGUI()
    app.mainloop()


if __name__ == "__main__":
    run_app()
