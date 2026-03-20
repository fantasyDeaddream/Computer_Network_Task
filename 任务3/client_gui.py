import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from pathlib import Path
import sys

from tk_utils import redirect_stdout_to_queue, safe_int, safe_str
from stream_client import CallState, StreamClient


_base = Path(__file__).resolve().parents[1]
_task2_dir = _base / "任务2"
if str(_task2_dir) not in sys.path:
    sys.path.insert(0, str(_task2_dir))

from audio_config import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402


class ClientGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("任务3 - UDP 实时语音客户端")
        self.geometry("980x680")
        self.minsize(920, 620)

        self._log_q: "queue.Queue[str]" = queue.Queue()
        self._redirect = redirect_stdout_to_queue(self._log_q)

        self._client: StreamClient | None = None
        self._ui_poll_job: str | None = None

        self._build_ui()
        self._set_connected(False)
        self._update_call_ui(CallState.IDLE, "")
        self._start_log_pump()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")

        conn_box = ttk.Labelframe(top, text="连接参数", padding=10)
        conn_box.pack(side="left", fill="x", expand=True)

        ttk.Label(conn_box, text="IP/Host").grid(row=0, column=0, sticky="w")
        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        ttk.Entry(conn_box, textvariable=self.host_var, width=22).grid(
            row=0, column=1, padx=(8, 16), sticky="w"
        )

        ttk.Label(conn_box, text="端口").grid(row=0, column=2, sticky="w")
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(conn_box, textvariable=self.port_var, width=10).grid(
            row=0, column=3, padx=(8, 16), sticky="w"
        )

        ttk.Label(conn_box, text="昵称").grid(row=0, column=4, sticky="w")
        self.nick_var = tk.StringVar(value="Alice")
        ttk.Entry(conn_box, textvariable=self.nick_var, width=16).grid(
            row=0, column=5, padx=(8, 0), sticky="w"
        )

        conn_box.columnconfigure(6, weight=1)

        ctrl_box = ttk.Labelframe(top, text="连接控制", padding=10)
        ctrl_box.pack(side="right", fill="y", padx=(12, 0))

        self.btn_connect = ttk.Button(ctrl_box, text="连接", command=self._on_connect)
        self.btn_connect.grid(row=0, column=0, sticky="ew")

        self.btn_disconnect = ttk.Button(
            ctrl_box, text="断开", command=self._on_disconnect
        )
        self.btn_disconnect.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.btn_clear = ttk.Button(ctrl_box, text="清空日志", command=self._on_clear_log)
        self.btn_clear.grid(row=2, column=0, sticky="ew", pady=(16, 0))

        mid = ttk.Frame(outer)
        mid.pack(fill="both", expand=True, pady=(12, 0))

        left = ttk.Frame(mid)
        left.pack(side="left", fill="both", expand=True)

        status_box = ttk.Labelframe(left, text="状态", padding=10)
        status_box.pack(fill="x")

        self._lamp_canvas = tk.Canvas(status_box, width=18, height=18, highlightthickness=0)
        self._lamp_canvas.grid(row=0, column=0, padx=(0, 8))
        self._lamp = self._lamp_canvas.create_oval(
            2, 2, 16, 16, fill="#b0b0b0", outline="#808080"
        )

        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(status_box, textvariable=self.status_var).grid(row=0, column=1, sticky="w")

        self.route_var = tk.StringVar(value="当前路由: 未建立")
        ttk.Label(status_box, textvariable=self.route_var).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        call_box = ttk.Labelframe(left, text="通话对象", padding=10)
        call_box.pack(fill="x", pady=(12, 0))

        ttk.Label(call_box, text="目标昵称").grid(row=0, column=0, sticky="w")
        self.target_var = tk.StringVar(value="Bob")
        self.target_entry = ttk.Entry(call_box, textvariable=self.target_var, width=20)
        self.target_entry.grid(row=0, column=1, padx=(8, 12), sticky="w")

        self.btn_call = ttk.Button(call_box, text="呼叫", command=self._on_call)
        self.btn_call.grid(row=0, column=2, sticky="ew")

        self.btn_accept = ttk.Button(call_box, text="接听", command=self._on_accept)
        self.btn_accept.grid(row=0, column=3, padx=(8, 0), sticky="ew")

        self.btn_reject = ttk.Button(call_box, text="拒绝", command=self._on_reject)
        self.btn_reject.grid(row=0, column=4, padx=(8, 0), sticky="ew")

        self.btn_hangup = ttk.Button(call_box, text="挂断", command=self._on_hangup)
        self.btn_hangup.grid(row=0, column=5, padx=(8, 0), sticky="ew")

        voice_box = ttk.Labelframe(left, text="语音发送", padding=10)
        voice_box.pack(fill="x", pady=(12, 0))

        self.btn_record = ttk.Button(
            voice_box, text="开始实时语音", command=self._on_record
        )
        self.btn_record.grid(row=0, column=0, sticky="ew")

        self.btn_stop_send = ttk.Button(
            voice_box, text="停止发送", command=self._on_stop_send
        )
        self.btn_stop_send.grid(row=0, column=1, padx=(8, 0), sticky="ew")

        chat_box = ttk.Labelframe(left, text="文本消息", padding=10)
        chat_box.pack(fill="x", pady=(12, 0))

        self.msg_var = tk.StringVar()
        self.msg_entry = ttk.Entry(chat_box, textvariable=self.msg_var)
        self.msg_entry.grid(row=0, column=0, sticky="ew")
        self.btn_send = ttk.Button(chat_box, text="发送", command=self._on_send_text)
        self.btn_send.grid(row=0, column=1, padx=(8, 0))
        chat_box.columnconfigure(0, weight=1)

        log_box = ttk.Labelframe(left, text="日志 / 收发消息", padding=10)
        log_box.pack(fill="both", expand=True, pady=(12, 0))

        self.log_text = tk.Text(log_box, wrap="word", height=18)
        self.log_text.pack(fill="both", expand=True)

        right = ttk.Labelframe(mid, text="说明", padding=10)
        right.pack(side="right", fill="y", padx=(12, 0))

        tips = (
            "1) 两端都先连接同一台服务器。\n"
            "2) 输入目标昵称后点击“呼叫”，对端点击“接听”。\n"
            "3) 建立通话后再点击“开始实时语音”。\n"
            "4) 同子网时日志会显示 P2P；不同子网时显示服务器 UDP 中转。\n"
            "5) 接收端会把 UDP 包按序号放入短缓冲，再按顺序播放，并对丢包做简单补偿。"
        )
        ttk.Label(right, text=tips, justify="left").pack(anchor="nw")

    def _set_lamp(self, color: str) -> None:
        self._lamp_canvas.itemconfig(self._lamp, fill=color)

    def _set_connected(self, connected: bool) -> None:
        if connected:
            self._set_lamp("#3ad04a")
        else:
            self._set_lamp("#b0b0b0")
            self.status_var.set("未连接")
            self.route_var.set("当前路由: 未建立")

        self.btn_connect.config(state=("disabled" if connected else "normal"))
        self.btn_disconnect.config(state=("normal" if connected else "disabled"))
        self.msg_entry.config(state=("normal" if connected else "disabled"))
        self.btn_send.config(state=("normal" if connected else "disabled"))

        if not connected:
            self.btn_call.config(state="disabled")
            self.btn_accept.config(state="disabled")
            self.btn_reject.config(state="disabled")
            self.btn_hangup.config(state="disabled")
            self.btn_record.config(state="disabled")
            self.btn_stop_send.config(state="disabled")
            self.target_entry.config(state="disabled")
        else:
            self.target_entry.config(state="normal")

    def _update_call_ui(self, state: str, target: str) -> None:
        if not self._client or not self._client.is_connected:
            return

        route_text = "当前路由: 未建立"
        if self._client.session_mode == "p2p":
            route_text = "当前路由: P2P UDP 直连"
        elif self._client.session_mode == "relay":
            route_text = "当前路由: 服务器 UDP 中转"
        self.route_var.set(route_text)

        if state == CallState.IDLE:
            self.status_var.set("已连接，空闲")
            self.btn_call.config(state="normal")
            self.btn_accept.config(state="disabled")
            self.btn_reject.config(state="disabled")
            self.btn_hangup.config(state="disabled")
            self.btn_record.config(state="disabled")
            self.btn_stop_send.config(state="disabled")
            self.target_entry.config(state="normal")
        elif state == CallState.CALLING:
            self.status_var.set(f"正在呼叫 {target}")
            self.btn_call.config(state="disabled")
            self.btn_accept.config(state="disabled")
            self.btn_reject.config(state="disabled")
            self.btn_hangup.config(state="normal")
            self.btn_record.config(state="disabled")
            self.btn_stop_send.config(state="disabled")
            self.target_entry.config(state="disabled")
        elif state == CallState.RINGING:
            self.status_var.set(f"收到来自 {target} 的呼叫")
            self.target_var.set(target)
            self.btn_call.config(state="disabled")
            self.btn_accept.config(state="normal")
            self.btn_reject.config(state="normal")
            self.btn_hangup.config(state="disabled")
            self.btn_record.config(state="disabled")
            self.btn_stop_send.config(state="disabled")
            self.target_entry.config(state="disabled")
        elif state == CallState.CONNECTING:
            self.status_var.set(f"正在与 {target} 协商链路")
            self.btn_call.config(state="disabled")
            self.btn_accept.config(state="disabled")
            self.btn_reject.config(state="disabled")
            self.btn_hangup.config(state="normal")
            self.btn_record.config(state="disabled")
            self.btn_stop_send.config(state="disabled")
            self.target_entry.config(state="disabled")
        elif state == CallState.IN_CALL:
            self.status_var.set(f"与 {target} 通话中")
            self.btn_call.config(state="disabled")
            self.btn_accept.config(state="disabled")
            self.btn_reject.config(state="disabled")
            self.btn_hangup.config(state="normal")
            self.btn_record.config(state="normal")
            self.btn_stop_send.config(state="normal")
            self.target_entry.config(state="disabled")
        elif state == CallState.ENDED:
            self.status_var.set("通话结束")
            self.btn_call.config(state="disabled")
            self.btn_accept.config(state="disabled")
            self.btn_reject.config(state="disabled")
            self.btn_hangup.config(state="disabled")
            self.btn_record.config(state="disabled")
            self.btn_stop_send.config(state="disabled")
            self.target_entry.config(state="normal")

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

    def _on_connect(self) -> None:
        host = safe_str(self.host_var.get(), DEFAULT_HOST)
        port = safe_int(self.port_var.get(), DEFAULT_PORT)
        nick = safe_str(self.nick_var.get(), "User")

        if self._client:
            return

        def on_text(msg: str) -> None:
            print(msg)

        def on_state_change(state: str, target: str) -> None:
            self.after(0, lambda: self._update_call_ui(state, target))

        self._client = StreamClient(
            host,
            port,
            nick,
            on_text=on_text,
            on_call_state_change=on_state_change,
        )

        def do_connect() -> None:
            ok = self._client.connect() if self._client else False
            if not ok:
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "连接失败",
                        "无法连接到服务器，请检查 IP/端口 和服务端状态。",
                    ),
                )
                self.after(0, lambda: self._cleanup_client(False))
                return

            self.after(0, lambda: self._set_connected(True))
            self.after(0, lambda: self._update_call_ui(CallState.IDLE, ""))

        threading.Thread(target=do_connect, daemon=True).start()

    def _cleanup_client(self, connected: bool) -> None:
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._set_connected(connected)

    def _on_disconnect(self) -> None:
        self._cleanup_client(False)

    def _on_send_text(self) -> None:
        content = (self.msg_var.get() or "").strip()
        if not content:
            return
        if not self._client:
            messagebox.showwarning("未连接", "请先连接服务器。")
            return
        target = (self.target_var.get() or "").strip()
        self.msg_var.set("")
        try:
            self._client.send_text(content, target=target)
        except Exception as exc:
            messagebox.showerror("发送失败", str(exc))

    def _on_call(self) -> None:
        if not self._client:
            return
        target = (self.target_var.get() or "").strip()
        if not target:
            messagebox.showwarning("目标为空", "请输入要呼叫的目标昵称。")
            return
        if not self._client.call(target):
            messagebox.showwarning("无法呼叫", "当前状态下不能发起呼叫。")

    def _on_accept(self) -> None:
        if not self._client:
            return
        target = self._client.in_call_with or (self.target_var.get() or "").strip()
        if target:
            self._client.accept_call(target)

    def _on_reject(self) -> None:
        if not self._client:
            return
        target = self._client.in_call_with or (self.target_var.get() or "").strip()
        if target:
            self._client.reject_call(target)

    def _on_hangup(self) -> None:
        if self._client:
            self._client.hangup()

    def _on_record(self) -> None:
        if not self._client:
            return
        try:
            self._client.start_streaming()
        except Exception as exc:
            messagebox.showerror("开始发送失败", str(exc))

    def _on_stop_send(self) -> None:
        if not self._client:
            return
        try:
            self._client.stop_streaming()
        except Exception as exc:
            messagebox.showerror("停止发送失败", str(exc))

    def _on_close(self) -> None:
        try:
            if self._client:
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
