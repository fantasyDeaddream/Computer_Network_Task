"""
Tkinter GUI for the audio chat client.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from audio_config import DEFAULT_HOST, DEFAULT_PORT
from qcli import AudioClient


class StatusLight(ttk.Frame):
    def __init__(self, master: tk.Misc, label: str) -> None:
        super().__init__(master)
        self._canvas = tk.Canvas(self, width=18, height=18, highlightthickness=0)
        self._canvas.grid(row=0, column=0, padx=(0, 6))
        self._indicator = self._canvas.create_oval(
            2, 2, 16, 16, fill="#8F8F8F", outline=""
        )
        ttk.Label(self, text=label, width=10).grid(row=0, column=1, sticky="w")

    def set_state(self, active: bool, active_color: str = "#1F9D55") -> None:
        self._canvas.itemconfigure(
            self._indicator, fill=active_color if active else "#8F8F8F"
        )


class AudioClientGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("音频通信客户端")
        self.root.geometry("980x640")
        self.root.minsize(860, 560)

        self.event_queue: queue.Queue[dict] = queue.Queue()
        self.client: AudioClient | None = None

        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.nickname_var = tk.StringVar(value="TkUser")
        self.recipient_var = tk.StringVar()
        self.message_var = tk.StringVar()
        self.connection_text = tk.StringVar(value="未连接")
        self.target_hint = tk.StringVar(value="留空为广播；填写昵称或从右侧列表选择为单播")

        self._build_ui()
        self._refresh_controls()
        self.root.after(150, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        connection_frame = ttk.LabelFrame(self.root, text="连接配置", padding=12)
        connection_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        for index in range(8):
            connection_frame.columnconfigure(
                index, weight=1 if index in {1, 3, 5, 7} else 0
            )

        ttk.Label(connection_frame, text="服务器 IP").grid(row=0, column=0, sticky="w")
        ttk.Entry(connection_frame, textvariable=self.host_var).grid(
            row=0, column=1, sticky="ew", padx=(6, 14)
        )
        ttk.Label(connection_frame, text="端口").grid(row=0, column=2, sticky="w")
        ttk.Entry(connection_frame, textvariable=self.port_var, width=10).grid(
            row=0, column=3, sticky="ew", padx=(6, 14)
        )
        ttk.Label(connection_frame, text="昵称").grid(row=0, column=4, sticky="w")
        ttk.Entry(connection_frame, textvariable=self.nickname_var).grid(
            row=0, column=5, sticky="ew", padx=(6, 14)
        )
        ttk.Label(connection_frame, text="接收方").grid(row=0, column=6, sticky="w")
        ttk.Entry(connection_frame, textvariable=self.recipient_var).grid(
            row=0, column=7, sticky="ew", padx=(6, 0)
        )
        ttk.Label(connection_frame, textvariable=self.target_hint).grid(
            row=1, column=0, columnspan=8, sticky="w", pady=(8, 0)
        )

        status_frame = ttk.LabelFrame(self.root, text="状态指示", padding=12)
        status_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        for index in range(5):
            status_frame.columnconfigure(index, weight=1)

        self.server_light = StatusLight(status_frame, "服务器")
        self.server_light.grid(row=0, column=0, sticky="w")
        self.client_light = StatusLight(status_frame, "客户端")
        self.client_light.grid(row=0, column=1, sticky="w")
        self.record_light = StatusLight(status_frame, "录音中")
        self.record_light.grid(row=0, column=2, sticky="w")
        ttk.Label(status_frame, text="连接状态").grid(row=0, column=3, sticky="e", padx=(0, 8))
        ttk.Label(status_frame, textvariable=self.connection_text).grid(
            row=0, column=4, sticky="w"
        )

        main_frame = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        main_frame.grid(row=2, column=0, sticky="nsew")
        main_frame.columnconfigure(0, weight=2)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)

        control_frame = ttk.LabelFrame(main_frame, text="操作面板", padding=12)
        control_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for index in range(7):
            control_frame.columnconfigure(index, weight=1)

        self.connect_button = ttk.Button(control_frame, text="连接服务器", command=self._connect)
        self.connect_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.disconnect_button = ttk.Button(
            control_frame, text="断开连接", command=self._disconnect
        )
        self.disconnect_button.grid(row=0, column=1, sticky="ew", padx=8)
        self.record_button = ttk.Button(control_frame, text="开始录音", command=self._start_recording)
        self.record_button.grid(row=0, column=2, sticky="ew", padx=8)
        self.stop_button = ttk.Button(control_frame, text="停止并发送", command=self._stop_recording)
        self.stop_button.grid(row=0, column=3, sticky="ew", padx=8)
        self.send_wav_button = ttk.Button(control_frame, text="发送 WAV 文件", command=self._send_wav_file)
        self.send_wav_button.grid(row=0, column=4, sticky="ew", padx=8)
        self.refresh_users_button = ttk.Button(
            control_frame, text="刷新用户列表", command=self._refresh_users
        )
        self.refresh_users_button.grid(row=0, column=5, sticky="ew", padx=8)
        self.clear_button = ttk.Button(control_frame, text="清空日志", command=self._clear_log)
        self.clear_button.grid(row=0, column=6, sticky="ew", padx=(8, 0))

        log_frame = ttk.LabelFrame(main_frame, text="终端输出", padding=12)
        log_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        users_frame = ttk.LabelFrame(main_frame, text="在线用户", padding=12)
        users_frame.grid(row=1, column=1, sticky="nsew")
        users_frame.columnconfigure(0, weight=1)
        users_frame.rowconfigure(0, weight=1)

        self.user_list = ttk.Treeview(
            users_frame,
            columns=("seq", "nickname", "actual_id"),
            displaycolumns=("seq", "nickname"),
            show="headings",
            height=10,
        )
        self.user_list.heading("seq", text="序号")
        self.user_list.heading("nickname", text="昵称")
        self.user_list.column("seq", width=60, anchor="center")
        self.user_list.column("nickname", width=170, anchor="w")
        self.user_list.column("actual_id", width=0, stretch=False)
        self.user_list.grid(row=0, column=0, sticky="nsew")
        self.user_list.bind("<<TreeviewSelect>>", self._on_user_select)

        users_scrollbar = ttk.Scrollbar(
            users_frame, orient="vertical", command=self.user_list.yview
        )
        users_scrollbar.grid(row=0, column=1, sticky="ns")
        self.user_list.configure(yscrollcommand=users_scrollbar.set)

        message_frame = ttk.LabelFrame(main_frame, text="文本消息", padding=12)
        message_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        message_frame.columnconfigure(0, weight=1)

        self.message_entry = ttk.Entry(message_frame, textvariable=self.message_var)
        self.message_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.send_button = ttk.Button(message_frame, text="发送消息", command=self._send_text)
        self.send_button.grid(row=0, column=1, sticky="ew")
        self.message_entry.bind("<Return>", lambda _event: self._send_text())

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def _current_recipient(self) -> str | None:
        value = self.recipient_var.get().strip()
        return value or None

    def _refresh_controls(self) -> None:
        connected = bool(self.client and self.client.is_connected)
        recording = bool(self.client and self.client.recorder.is_recording())

        self.server_light.set_state(connected, "#198754")
        self.client_light.set_state(self.client is not None, "#0D6EFD")
        self.record_light.set_state(recording, "#DC3545")

        if connected and self.client:
            self.connection_text.set(
                f"已连接 {self.client.host}:{self.client.port} ({self.client.nickname})"
            )
        else:
            self.connection_text.set("未连接")

        self.connect_button.configure(state="disabled" if connected else "normal")
        self.disconnect_button.configure(state="normal" if connected else "disabled")
        self.record_button.configure(state="normal" if connected and not recording else "disabled")
        self.stop_button.configure(state="normal" if connected and recording else "disabled")
        self.send_button.configure(state="normal" if connected else "disabled")
        self.send_wav_button.configure(state="normal" if connected else "disabled")
        self.refresh_users_button.configure(state="normal" if connected else "disabled")

    def _create_client(self) -> AudioClient | None:
        host = self.host_var.get().strip() or DEFAULT_HOST
        nickname = self.nickname_var.get().strip() or None

        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("端口错误", "端口必须是整数。")
            return None

        return AudioClient(
            host=host,
            port=port,
            nickname=nickname,
            event_callback=self.event_queue.put,
        )

    def _connect(self) -> None:
        if self.client and self.client.is_connected:
            return

        client = self._create_client()
        if client is None:
            return

        self.client = client
        self._append_log("[GUI] 正在连接服务器...")
        self._refresh_controls()
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self) -> None:
        if self.client:
            self.client.start_background_receive()

    def _disconnect(self) -> None:
        if self.client:
            threading.Thread(target=self.client.disconnect, daemon=True).start()

    def _start_recording(self) -> None:
        if self.client:
            threading.Thread(target=self.client.start_recording, daemon=True).start()

    def _stop_recording(self) -> None:
        if self.client:
            threading.Thread(
                target=self.client.stop_recording_and_send,
                args=(self._current_recipient(),),
                daemon=True,
            ).start()

    def _send_wav_file(self) -> None:
        if not self.client:
            return

        file_path = filedialog.askopenfilename(
            title="选择 WAV 文件",
            filetypes=[("WAV 文件", "*.wav")],
        )
        if not file_path:
            return

        threading.Thread(
            target=self.client.send_wav_file,
            args=(file_path, self._current_recipient()),
            daemon=True,
        ).start()

    def _refresh_users(self) -> None:
        if self.client:
            threading.Thread(target=self.client.request_user_list, daemon=True).start()

    def _send_text(self) -> None:
        if not self.client:
            return

        content = self.message_var.get().strip()
        if not content:
            return

        self.message_var.set("")
        threading.Thread(
            target=self.client.send_text_message,
            args=(content, self._current_recipient()),
            daemon=True,
        ).start()

    def _on_user_select(self, _event: tk.Event) -> None:
        selection = self.user_list.selection()
        if not selection or not self.client:
            return

        _seq, nickname, actual_id = self.user_list.item(selection[0], "values")
        if str(actual_id) == str(self.client.user_id):
            self.recipient_var.set("")
            self._append_log("[GUI] 选中了自己，已自动切回广播模式。")
            return

        self.recipient_var.set(str(nickname).replace(" (我)", ""))

    def _update_user_list(self, users: list[dict]) -> None:
        selected_recipient = self.recipient_var.get().strip()
        self.user_list.delete(*self.user_list.get_children())

        for seq, user in enumerate(users, start=1):
            nickname = str(user.get("nickname", "Unknown"))
            actual_id = str(user.get("id", ""))
            if self.client and str(user.get("id")) == str(self.client.user_id):
                nickname = f"{nickname} (我)"
            self.user_list.insert("", tk.END, values=(seq, nickname, actual_id))

        if selected_recipient and not any(
            str(user.get("id")) == selected_recipient or user.get("nickname") == selected_recipient
            for user in users
        ):
            self.recipient_var.set("")

    def _process_events(self) -> None:
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break

            event_type = event.get("type")
            if event_type == "log":
                self._append_log(event.get("message", ""))
            elif event_type == "status":
                self._refresh_controls()
            elif event_type == "connected":
                self._refresh_controls()
            elif event_type == "recording_started":
                pass
            elif event_type == "user_list":
                users = event.get("users", [])
                self._update_user_list(users)

        self._refresh_controls()
        self.root.after(150, self._process_events)

    def _on_close(self) -> None:
        if self.client:
            try:
                if self.client.recorder.is_recording():
                    self.client.recorder.stop_recording()
                self.client.disconnect()
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    root.option_add("*Font", "{Microsoft YaHei UI} 10")
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = AudioClientGUI(root)
    app._append_log("[GUI] 图形客户端已启动。")
    root.mainloop()


if __name__ == "__main__":
    main()
