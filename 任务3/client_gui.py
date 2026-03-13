import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from tk_utils import redirect_stdout_to_queue, safe_int, safe_str
from stream_client import StreamClient
from pathlib import Path
import sys

from pathlib import Path as _Path

_base = _Path(__file__).resolve().parents[1]
_task2_dir = _base / "任务2"
if str(_task2_dir) not in sys.path:
    sys.path.insert(0, str(_task2_dir))

from audio_config import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402


class ClientGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("任务3 - 音频通信客户端（Tkinter）")
        self.geometry("920x620")
        self.minsize(860, 560)

        self._log_q: "queue.Queue[str]" = queue.Queue()
        self._redirect = redirect_stdout_to_queue(self._log_q)

        self._client: StreamClient | None = None
        self._ui_poll_job: str | None = None

        self._build_ui()
        self._set_connected(False)
        self._start_log_pump()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")

        conn_box = ttk.Labelframe(top, text="连接参数", padding=10)
        conn_box.pack(side="left", fill="x", expand=True)

        ttk.Label(conn_box, text="IP/Host").grid(row=0, column=0, sticky="w")
        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        ttk.Entry(conn_box, textvariable=self.host_var, width=22).grid(row=0, column=1, padx=(8, 16), sticky="w")

        ttk.Label(conn_box, text="端口").grid(row=0, column=2, sticky="w")
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(conn_box, textvariable=self.port_var, width=10).grid(row=0, column=3, padx=(8, 16), sticky="w")

        ttk.Label(conn_box, text="昵称").grid(row=0, column=4, sticky="w")
        self.nick_var = tk.StringVar(value="Alice")
        ttk.Entry(conn_box, textvariable=self.nick_var, width=16).grid(row=0, column=5, padx=(8, 0), sticky="w")

        conn_box.columnconfigure(6, weight=1)

        btn_box = ttk.Labelframe(top, text="控制", padding=10)
        btn_box.pack(side="right", fill="y", padx=(12, 0))

        self.btn_connect = ttk.Button(btn_box, text="连接", command=self._on_connect)
        self.btn_connect.grid(row=0, column=0, sticky="ew")

        self.btn_disconnect = ttk.Button(btn_box, text="断开", command=self._on_disconnect)
        self.btn_disconnect.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.btn_record = ttk.Button(btn_box, text="开始实时语音", command=self._on_record)
        self.btn_record.grid(row=2, column=0, sticky="ew", pady=(16, 0))

        self.btn_stop_send = ttk.Button(btn_box, text="停止发送", command=self._on_stop_send)
        self.btn_stop_send.grid(row=3, column=0, sticky="ew", pady=(8, 0))

        self.btn_clear = ttk.Button(btn_box, text="清空日志", command=self._on_clear_log)
        self.btn_clear.grid(row=4, column=0, sticky="ew", pady=(16, 0))

        btn_box.columnconfigure(0, weight=1)

        mid = ttk.Frame(outer)
        mid.pack(fill="both", expand=True, pady=(12, 0))

        left = ttk.Frame(mid)
        left.pack(side="left", fill="both", expand=True)

        status = ttk.Labelframe(left, text="状态指示", padding=10)
        status.pack(fill="x")

        self._lamp_canvas = tk.Canvas(status, width=18, height=18, highlightthickness=0)
        self._lamp_canvas.grid(row=0, column=0, padx=(0, 8))
        self._lamp = self._lamp_canvas.create_oval(2, 2, 16, 16, fill="#b0b0b0", outline="#808080")

        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=1, sticky="w")

        chat = ttk.Labelframe(left, text="文本消息", padding=10)
        chat.pack(fill="x", pady=(12, 0))

        self.msg_var = tk.StringVar()
        self.msg_entry = ttk.Entry(chat, textvariable=self.msg_var)
        self.msg_entry.grid(row=0, column=0, sticky="ew")
        self.btn_send = ttk.Button(chat, text="发送", command=self._on_send_text)
        self.btn_send.grid(row=0, column=1, padx=(8, 0))
        chat.columnconfigure(0, weight=1)

        log = ttk.Labelframe(left, text="日志 / 收发信息", padding=10)
        log.pack(fill="both", expand=True, pady=(12, 0))

        self.log_text = tk.Text(log, wrap="word", height=18)
        self.log_text.pack(fill="both", expand=True)

        right = ttk.Labelframe(mid, text="提示", padding=10)
        right.pack(side="right", fill="y", padx=(12, 0))

        tips = (
            "1) 先启动任务3的“实时流”服务端。\n"
            "2) 连接后可发送文本。\n"
            "3) 点击“开始实时语音”后讲话，再点“停止发送”。\n"
            "4) 接收到的语音块会立即播放，实现近实时对讲。\n"
        )
        ttk.Label(right, text=tips, justify="left").pack(anchor="nw")

    # ---------------- State ----------------
    def _set_lamp(self, color: str) -> None:
        self._lamp_canvas.itemconfig(self._lamp, fill=color)

    def _set_connected(self, connected: bool) -> None:
        if connected:
            self._set_lamp("#3ad04a")
            self.status_var.set("已连接")
        else:
            self._set_lamp("#b0b0b0")
            self.status_var.set("未连接")

        self.btn_connect.config(state=("disabled" if connected else "normal"))
        self.btn_disconnect.config(state=("normal" if connected else "disabled"))
        self.btn_record.config(state=("normal" if connected else "disabled"))
        self.btn_stop_send.config(state=("normal" if connected else "disabled"))
        self.btn_send.config(state=("normal" if connected else "disabled"))
        self.msg_entry.config(state=("normal" if connected else "disabled"))

    # ---------------- Logging ----------------
    def _start_log_pump(self) -> None:
        def pump() -> None:
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

    def _on_clear_log(self) -> None:
        self.log_text.delete("1.0", "end")

    # ---------------- Actions ----------------
    def _on_connect(self) -> None:
        host = safe_str(self.host_var.get(), DEFAULT_HOST)
        port = safe_int(self.port_var.get(), DEFAULT_PORT)
        nick = safe_str(self.nick_var.get(), "User")

        if self._client:
            return

        def on_text(msg: str) -> None:
            print(msg)

        self._client = StreamClient(host, port, nick, on_text=on_text)

        def do_connect() -> None:
            ok = self._client.connect() if self._client else False
            if not ok:
                self.after(0, lambda: messagebox.showerror("连接失败", "无法连接到服务器，请检查IP/端口与服务端状态。"))
                self.after(0, lambda: self._set_connected(False))
                return

            self.after(0, lambda: self._set_connected(True))

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_disconnect(self) -> None:
        if not self._client:
            self._set_connected(False)
            return
        try:
            self._client.disconnect()
        except Exception:
            pass
        self._set_connected(False)

    def _on_send_text(self) -> None:
        content = (self.msg_var.get() or "").strip()
        if not content:
            return
        if not self._client:
            messagebox.showwarning("未连接", "请先连接服务器。")
            return
        self.msg_var.set("")
        try:
            self._client.send_text(content)
        except Exception as e:
            messagebox.showerror("发送失败", str(e))

    def _on_record(self) -> None:
        if not self._client:
            return
        try:
            self._client.start_streaming()
        except Exception as e:
            messagebox.showerror("录音失败", str(e))

    def _on_stop_send(self) -> None:
        if not self._client:
            return
        try:
            self._client.stop_streaming()
        except Exception as e:
            messagebox.showerror("停止失败", str(e))

    def _on_close(self) -> None:
        try:
            if self._client and self._client.is_connected:
                self._client.disconnect()
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


def run_app() -> None:
    app = ClientGUI()
    app.mainloop()

