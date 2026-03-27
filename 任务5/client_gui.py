"""
任务5 - 多方语音会议系统客户端图形界面

复用任务4的登录界面和联系人界面（不做修改），
修改IP电话界面（删除用户输入框，呼叫改为创建聊天室），
新增聊天室界面。
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Optional
from pathlib import Path
import sys
import math


def _ensure_task2_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task2_dir = base / "任务2"
    if str(task2_dir) not in sys.path:
        sys.path.insert(0, str(task2_dir))


_ensure_task2_on_path()
from audio_config import DEFAULT_HOST

from conference_client import ConferenceClient, RoomState
from conference_protocol import MAX_ROOM_SIZE


# ============================================================
# LoginFrame - 复用任务4的登录界面（不做修改）
# ============================================================
class LoginFrame(ttk.Frame):

    def __init__(self, parent, on_login_success):
        super().__init__(parent)
        self._on_login_success = on_login_success
        self._build_ui()

    def _build_ui(self):
        title = ttk.Label(self, text="IP电话系统 - 登录", font=("微软雅黑", 16))
        title.pack(pady=20)

        server_box = ttk.Labelframe(self, text="服务器配置", padding=10)
        server_box.pack(fill="x", padx=20, pady=5)

        ttk.Label(server_box, text="服务器地址").grid(row=0, column=0, sticky="w")
        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        ttk.Entry(server_box, textvariable=self.host_var, width=20).grid(
            row=0, column=1, padx=(8, 0), sticky="w"
        )

        ttk.Label(server_box, text="端口").grid(
            row=0, column=2, sticky="w", padx=(20, 0)
        )
        self.port_var = tk.StringVar(value="8882")
        ttk.Entry(server_box, textvariable=self.port_var, width=8).grid(
            row=0, column=3, padx=(8, 0), sticky="w"
        )

        auth_box = ttk.Labelframe(self, text="用户登录", padding=10)
        auth_box.pack(fill="x", padx=20, pady=10)

        ttk.Label(auth_box, text="用户名").grid(row=0, column=0, sticky="w")
        self.username_var = tk.StringVar()
        self.username_entry = ttk.Entry(
            auth_box, textvariable=self.username_var, width=20
        )
        self.username_entry.grid(row=0, column=1, padx=(8, 0), sticky="w")
        self.username_entry.bind("<Return>", lambda e: self._on_login())

        btn_box = ttk.Frame(auth_box)
        btn_box.grid(row=1, column=0, columnspan=2, pady=(15, 0))
        self.btn_login = ttk.Button(btn_box, text="登录", command=self._on_login)
        self.btn_login.pack(side="left", padx=5)

        hint = ttk.Label(self, text="提示：输入用户名即可登录", foreground="gray")
        hint.pack(pady=10)

    def _on_login(self):
        username = self.username_var.get().strip()
        if not username:
            messagebox.showwarning("输入错误", "请输入用户名")
            return
        host = self.host_var.get().strip() or DEFAULT_HOST
        port = int(self.port_var.get().strip() or 8882)
        self.btn_login.config(state="disabled")

        def do_login():
            client = ConferenceClient(host=host, port=port)
            success, msg = client.login(username)
            self.after(0, lambda: self._handle_result(client, success, msg))

        threading.Thread(target=do_login, daemon=True).start()

    def _handle_result(self, client, success, msg):
        self.btn_login.config(state="normal")
        if success:
            self._on_login_success(client, self.username_var.get().strip())
        else:
            messagebox.showerror("登录失败", msg)


# ============================================================
# ContactFrame - 复用任务4的联系人界面（撤销双击呼叫功能）
# 新增：显示联系人在线/离线状态
# ============================================================
class ContactFrame(ttk.Frame):

    # 在线/离线状态前缀
    _ONLINE_PREFIX = "🟢 "
    _OFFLINE_PREFIX = "⚪ "

    def __init__(self, parent, client: ConferenceClient, username: str):
        super().__init__(parent)
        self._client = client
        self._username = username
        self._raw_contacts: list[str] = []  # 存储不带状态前缀的原始联系人名
        self._build_ui()
        self._refresh_contacts()

    def _build_ui(self):
        title = ttk.Label(
            self, text=f"电话本 - {self._username}", font=("微软雅黑", 14)
        )
        title.pack(pady=10)

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=10, pady=5)
        ttk.Button(toolbar, text="添加联系人", command=self._on_add).pack(
            side="left", padx=2
        )
        ttk.Button(toolbar, text="删除联系人", command=self._on_delete).pack(
            side="left", padx=2
        )
        ttk.Button(toolbar, text="修改联系人", command=self._on_update).pack(
            side="left", padx=2
        )
        ttk.Button(toolbar, text="刷新", command=self._refresh_contacts).pack(
            side="left", padx=2
        )

        search_frame = ttk.Frame(self)
        search_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(search_frame, text="搜索:").pack(side="left")
        self.search_var = tk.StringVar()
        ttk.Entry(search_frame, textvariable=self.search_var, width=20).pack(
            side="left", padx=5
        )
        ttk.Button(search_frame, text="查找", command=self._on_search).pack(
            side="left", padx=2
        )
        ttk.Button(search_frame, text="显示全部", command=self._refresh_contacts).pack(
            side="left", padx=2
        )

        # 图例说明
        legend_frame = ttk.Frame(self)
        legend_frame.pack(fill="x", padx=10, pady=(0, 2))
        ttk.Label(
            legend_frame,
            text="🟢 在线  ⚪ 离线",
            foreground="gray",
            font=("微软雅黑", 9),
        ).pack(side="left")

        list_box = ttk.Labelframe(self, text="联系人列表", padding=5)
        list_box.pack(fill="both", expand=True, padx=10, pady=5)
        self.contact_listbox = tk.Listbox(list_box, height=15)
        self.contact_listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(
            list_box, orient="vertical", command=self.contact_listbox.yview
        )
        scrollbar.pack(side="right", fill="y")
        self.contact_listbox.config(yscrollcommand=scrollbar.set)
        # 注意：不绑定双击呼叫事件（任务5撤销此功能）

    def _get_raw_name(self, display_text: str) -> str:
        """从列表显示文本中提取原始用户名（去掉在线状态前缀）"""
        for prefix in (self._ONLINE_PREFIX, self._OFFLINE_PREFIX):
            if display_text.startswith(prefix):
                return display_text[len(prefix) :]
        return display_text

    def _display_contacts(self, contacts: list[str]) -> None:
        """在列表中显示联系人并标注在线状态"""
        self._raw_contacts = list(contacts)

        # 在后台线程中查询在线用户，避免阻塞UI
        def query_and_display():
            online_users = self._client.get_online_users()
            online_set = set(online_users)
            self.after(0, lambda: self._fill_listbox(contacts, online_set))

        threading.Thread(target=query_and_display, daemon=True).start()

    def _fill_listbox(self, contacts: list[str], online_set: set) -> None:
        """用在线状态信息填充联系人列表"""
        self.contact_listbox.delete(0, tk.END)
        for c in contacts:
            if c in online_set:
                display = f"{self._ONLINE_PREFIX}{c}"
            else:
                display = f"{self._OFFLINE_PREFIX}{c}"
            self.contact_listbox.insert(tk.END, display)

    def _refresh_contacts(self):
        contacts = self._client.get_contacts()
        self._display_contacts(contacts)

    def _on_add(self):
        name = simpledialog.askstring("添加联系人", "请输入联系人用户名:")
        if not name:
            return
        ok, msg = self._client.add_contact(name)
        if ok:
            messagebox.showinfo("成功", msg)
            self._refresh_contacts()
        else:
            messagebox.showerror("失败", msg)

    def _on_delete(self):
        sel = self.contact_listbox.curselection()
        if not sel:
            messagebox.showwarning("未选择", "请先选择一个联系人")
            return
        raw_display = self.contact_listbox.get(sel[0])
        name = self._get_raw_name(raw_display)
        if messagebox.askyesno("确认", f"确定要删除联系人 {name} 吗?"):
            ok, msg = self._client.delete_contact(name)
            if ok:
                messagebox.showinfo("成功", msg)
                self._refresh_contacts()
            else:
                messagebox.showerror("失败", msg)

    def _on_update(self):
        sel = self.contact_listbox.curselection()
        if not sel:
            messagebox.showwarning("未选择", "请先选择一个联系人")
            return
        raw_display = self.contact_listbox.get(sel[0])
        old = self._get_raw_name(raw_display)
        new = simpledialog.askstring(
            "修改联系人", "请输入新的用户名:", initialvalue=old
        )
        if not new or new == old:
            return
        ok, msg = self._client.update_contact(old, new)
        if ok:
            messagebox.showinfo("成功", msg)
            self._refresh_contacts()
        else:
            messagebox.showerror("失败", msg)

    def _on_search(self):
        kw = self.search_var.get().strip()
        if not kw:
            self._refresh_contacts()
            return
        contacts = self._client.search_contacts(kw)
        self._display_contacts(contacts)


# ============================================================
# TelephoneFrame - 修改后的IP电话界面
# 删除"用户"输入框，"呼叫"改为"创建聊天室"，保留接听按钮
# ============================================================
class TelephoneFrame(ttk.Frame):

    def __init__(self, parent, client: ConferenceClient, on_enter_room):
        super().__init__(parent)
        self._client = client
        self._on_enter_room = on_enter_room
        self._pending_invite_room_id = ""
        self._pending_invite_from = ""
        self._build_ui()

        # 注册回调
        self._client._on_room_invite = self._on_invite_received

    def _build_ui(self):
        title = ttk.Label(self, text="IP电话", font=("微软雅黑", 14))
        title.pack(pady=10)

        # 状态显示
        status_box = ttk.Labelframe(self, text="状态", padding=10)
        status_box.pack(fill="x", padx=10, pady=5)

        self._lamp_canvas = tk.Canvas(
            status_box, width=20, height=20, highlightthickness=0
        )
        self._lamp_canvas.pack(side="left", padx=(0, 10))
        self._lamp = self._lamp_canvas.create_oval(
            2, 2, 18, 18, fill="#b0b0b0", outline="#808080"
        )

        self.status_var = tk.StringVar(value="空闲")
        ttk.Label(status_box, textvariable=self.status_var, font=("微软雅黑", 12)).pack(
            side="left"
        )

        # 操作区域（删除了"用户"输入框）
        action_box = ttk.Labelframe(self, text="操作", padding=10)
        action_box.pack(fill="x", padx=10, pady=5)

        self.btn_create = ttk.Button(
            action_box, text="创建聊天室", command=self._on_create_room
        )
        self.btn_create.pack(side="left", padx=5)

        self.btn_accept = ttk.Button(
            action_box, text="接听", command=self._on_accept_invite, state="disabled"
        )
        self.btn_accept.pack(side="left", padx=5)

        self.btn_reject = ttk.Button(
            action_box, text="拒绝", command=self._on_reject_invite, state="disabled"
        )
        self.btn_reject.pack(side="left", padx=5)

        # 提示信息
        hint_box = ttk.Labelframe(self, text="操作提示", padding=10)
        hint_box.pack(fill="both", expand=True, padx=10, pady=5)

        self.hint_text = tk.Text(hint_box, height=10, wrap="word", state="disabled")
        self.hint_text.pack(fill="both", expand=True)

        self._update_hint(
            "空闲状态\n\n"
            "1. 点击「创建聊天室」创建一个新的语音会议室\n"
            "2. 收到邀请时，点击「接听」加入聊天室，或点击「拒绝」\n"
        )

    def _update_hint(self, text):
        self.hint_text.config(state="normal")
        self.hint_text.delete("1.0", tk.END)
        self.hint_text.insert("1.0", text)
        self.hint_text.config(state="disabled")

    def _on_create_room(self):
        self.btn_create.config(state="disabled")

        def do_create():
            ok, msg, data = self._client.create_room()
            self.after(0, lambda: self._handle_create_result(ok, msg, data))

        threading.Thread(target=do_create, daemon=True).start()

    def _handle_create_result(self, ok, msg, data):
        self.btn_create.config(state="normal")
        if ok:
            room_id = data.get("room_id", "")
            self._on_enter_room(room_id)
        else:
            messagebox.showerror("创建失败", msg)

    def _on_invite_received(self, room_id, inviter):
        """收到聊天室邀请"""
        self._pending_invite_room_id = room_id
        self._pending_invite_from = inviter
        self.after(0, self._show_invite)

    def _show_invite(self):
        self._lamp_canvas.itemconfig(self._lamp, fill="#30a0f0")
        self.status_var.set(f"收到 {self._pending_invite_from} 的邀请")
        self._update_hint(
            f"收到来自 {self._pending_invite_from} 的聊天室邀请\n"
            f"聊天室ID: {self._pending_invite_room_id}\n\n"
            f"点击「接听」加入聊天室，或点击「拒绝」忽略"
        )
        self.btn_create.config(state="disabled")
        self.btn_accept.config(state="normal")
        self.btn_reject.config(state="normal")

    def _on_accept_invite(self):
        if not self._pending_invite_room_id:
            return
        rid = self._pending_invite_room_id
        self.btn_accept.config(state="disabled")
        self.btn_reject.config(state="disabled")

        def do_join():
            ok, msg, data = self._client.join_room(rid)
            self.after(0, lambda: self._handle_join_result(ok, msg, data))

        threading.Thread(target=do_join, daemon=True).start()

    def _handle_join_result(self, ok, msg, data):
        if ok:
            self._reset_ui()
            self._on_enter_room(data.get("room_id", self._pending_invite_room_id))
        else:
            messagebox.showerror("加入失败", msg)
            self._reset_ui()

    def _on_reject_invite(self):
        self._pending_invite_room_id = ""
        self._pending_invite_from = ""
        self._reset_ui()

    def _reset_ui(self):
        self._lamp_canvas.itemconfig(self._lamp, fill="#b0b0b0")
        self.status_var.set("空闲")
        self._update_hint(
            "空闲状态\n\n"
            "1. 点击「创建聊天室」创建一个新的语音会议室\n"
            "2. 收到邀请时，点击「接听」加入聊天室，或点击「拒绝」\n"
        )
        self.btn_create.config(state="normal")
        self.btn_accept.config(state="disabled")
        self.btn_reject.config(state="disabled")
        self._pending_invite_room_id = ""
        self._pending_invite_from = ""


# ============================================================
# ChatRoomFrame - 聊天室界面
# 20个用户位置，纯白头像，邀请/退出/解散功能
# ============================================================
class ChatRoomFrame(ttk.Frame):

    AVATAR_SIZE = 50
    COLS = 5
    ROWS = 4

    # 头像文件目录
    _AVATARS_DIR = Path(__file__).resolve().parent / "data" / "avatars"

    def __init__(self, parent, client: ConferenceClient, room_id: str, on_exit_room):
        super().__init__(parent)
        self._client = client
        self._room_id = room_id
        self._on_exit_room = on_exit_room
        self._members = {}  # position -> username
        self._avatar_widgets = {}  # position -> (canvas, label)
        self._avatar_images = {}  # position -> PhotoImage (prevent GC)
        self._build_ui()

        # 注册回调
        self._client._on_room_member_update = self._on_member_update
        self._client._on_room_dismissed = self._on_dismissed

        # 从客户端缓存中立即刷新成员显示（解决加入时成员更新消息先于回调注册的问题）
        if self._client._cached_members:
            self._refresh_members(
                self._client._cached_members, self._client._cached_positions
            )

    def _build_ui(self):
        # 顶部信息栏
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=5)

        ttk.Label(
            top, text=f"聊天室: {self._room_id}", font=("微软雅黑", 12, "bold")
        ).pack(side="left")

        role_text = "创建者" if self._client.is_creator else "成员"
        ttk.Label(top, text=f"  ({role_text})", foreground="gray").pack(side="left")

        # 操作按钮
        btn_frame = ttk.Frame(top)
        btn_frame.pack(side="right")

        ttk.Button(btn_frame, text="邀请用户", command=self._on_invite).pack(
            side="left", padx=3
        )

        if self._client.is_creator:
            ttk.Button(btn_frame, text="解散聊天室", command=self._on_dismiss).pack(
                side="left", padx=3
            )
        else:
            ttk.Button(btn_frame, text="退出聊天室", command=self._on_leave).pack(
                side="left", padx=3
            )

        # 成员网格区域（5列 x 4行 = 20个位置）
        grid_frame = ttk.Labelframe(self, text="成员 (最多20人)", padding=10)
        grid_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # 创建内部canvas用于滚动
        inner = ttk.Frame(grid_frame)
        inner.pack(fill="both", expand=True)

        for pos in range(MAX_ROOM_SIZE):
            row = pos // self.COLS
            col = pos % self.COLS

            cell = ttk.Frame(inner, relief="groove", borderwidth=1)
            cell.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")

            # 头像（纯白圆形）
            canvas = tk.Canvas(
                cell,
                width=self.AVATAR_SIZE,
                height=self.AVATAR_SIZE,
                highlightthickness=0,
                bg="#f0f0f0",
            )
            canvas.pack(padx=5, pady=(5, 2))
            # 画纯白圆形头像
            canvas.create_oval(
                2,
                2,
                self.AVATAR_SIZE - 2,
                self.AVATAR_SIZE - 2,
                fill="white",
                outline="#cccccc",
                width=1,
            )

            # 用户名标签
            label = ttk.Label(
                cell,
                text="空位",
                foreground="#aaaaaa",
                font=("微软雅黑", 8),
                anchor="center",
                width=8,
            )
            label.pack(pady=(0, 5))

            self._avatar_widgets[pos] = (canvas, label)

        # 配置列权重使其均匀分布
        for c in range(self.COLS):
            inner.columnconfigure(c, weight=1)
        for r in range(self.ROWS):
            inner.rowconfigure(r, weight=1)

        # 底部状态栏
        status_bar = ttk.Frame(self)
        status_bar.pack(fill="x", padx=10, pady=5)

        self._status_label = ttk.Label(
            status_bar, text="语音通话中...", foreground="green"
        )
        self._status_label.pack(side="left")

        self._member_count_label = ttk.Label(status_bar, text="成员: 0/20")
        self._member_count_label.pack(side="right")

    def _on_invite(self):
        target = simpledialog.askstring("邀请用户", "请输入要邀请的用户名:")
        if not target:
            return

        def do_invite():
            ok, msg = self._client.invite_to_room(target)
            self.after(0, lambda: self._show_invite_result(ok, msg, target))

        threading.Thread(target=do_invite, daemon=True).start()

    def _show_invite_result(self, ok, msg, target):
        if ok:
            messagebox.showinfo("邀请成功", f"已向 {target} 发送邀请")
        else:
            messagebox.showerror("邀请失败", msg)

    def _on_leave(self):
        if messagebox.askyesno("确认", "确定要退出聊天室吗?"):
            self._client.leave_room()
            self._on_exit_room()

    def _on_dismiss(self):
        if messagebox.askyesno("确认", "确定要解散聊天室吗?\n所有成员将被移出。"):
            self._client.dismiss_room()
            self._on_exit_room()

    def _on_member_update(self, room_id, members, positions):
        """成员变更回调"""
        self.after(0, lambda: self._refresh_members(members, positions))

    def _load_avatar(self, username: str) -> "tk.PhotoImage | None":
        """尝试从 data/avatars 目录加载用户头像。

        如果 data/avatars/{username}.png 存在则返回缩放后的 PhotoImage，
        否则返回 None（使用默认头像）。
        """
        avatar_path = self._AVATARS_DIR / f"{username}.png"
        if not avatar_path.is_file():
            return None
        try:
            img = tk.PhotoImage(file=str(avatar_path))
            # 缩放到 AVATAR_SIZE x AVATAR_SIZE
            orig_w = img.width()
            orig_h = img.height()
            if orig_w > 0 and orig_h > 0:
                # subsample 只支持整数缩小倍数，先尝试缩小
                factor_w = max(1, orig_w // self.AVATAR_SIZE)
                factor_h = max(1, orig_h // self.AVATAR_SIZE)
                factor = max(factor_w, factor_h)
                if factor > 1:
                    img = img.subsample(factor, factor)
            return img
        except Exception:
            return None

    def _draw_default_avatar(self, canvas: tk.Canvas) -> None:
        """在 canvas 上绘制默认的人形头像图标。"""
        canvas.create_oval(
            2,
            2,
            self.AVATAR_SIZE - 2,
            self.AVATAR_SIZE - 2,
            fill="white",
            outline="#4a90d9",
            width=2,
        )
        cx = self.AVATAR_SIZE // 2
        cy = self.AVATAR_SIZE // 2
        # 头
        canvas.create_oval(cx - 6, cy - 12, cx + 6, cy - 0, fill="#ddd", outline="#999")
        # 身体
        canvas.create_arc(
            cx - 12,
            cy + 2,
            cx + 12,
            cy + 20,
            start=0,
            extent=180,
            fill="#ddd",
            outline="#999",
        )

    def _refresh_members(self, members, positions):
        # 清空所有位置
        self._members.clear()
        self._avatar_images.clear()
        for pos in range(MAX_ROOM_SIZE):
            canvas, label = self._avatar_widgets[pos]
            canvas.delete("all")
            canvas.create_oval(
                2,
                2,
                self.AVATAR_SIZE - 2,
                self.AVATAR_SIZE - 2,
                fill="white",
                outline="#cccccc",
                width=1,
            )
            label.config(text="空位", foreground="#aaaaaa")

        # 填充成员
        for m in members:
            uname = m["username"]
            pos = m["position"]
            self._members[pos] = uname

            canvas, label = self._avatar_widgets[pos]
            canvas.delete("all")

            # 尝试加载用户自定义头像
            avatar_img = self._load_avatar(uname)
            if avatar_img is not None:
                # 使用自定义头像
                self._avatar_images[pos] = avatar_img  # 防止被GC回收
                canvas.create_image(
                    self.AVATAR_SIZE // 2,
                    self.AVATAR_SIZE // 2,
                    image=avatar_img,
                    anchor="center",
                )
            else:
                # 使用默认头像
                self._draw_default_avatar(canvas)

            display_name = uname if len(uname) <= 8 else uname[:7] + ".."
            label.config(text=display_name, foreground="#333333")

        count = len(members)
        self._member_count_label.config(text=f"成员: {count}/{MAX_ROOM_SIZE}")

    def _on_dismissed(self, room_id):
        """聊天室被解散"""
        self.after(0, lambda: self._handle_dismissed())

    def _handle_dismissed(self):
        messagebox.showinfo("聊天室已解散", "聊天室已被创建者解散")
        self._on_exit_room()


# ============================================================
# ClientGUI - 主窗口
# ============================================================
class ClientGUI(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("任务5 - 多方语音会议系统")
        self.geometry("700x600")
        self.minsize(600, 500)

        self._client: Optional[ConferenceClient] = None
        self._username = ""

        self._login_frame = LoginFrame(self, self._on_login_success)
        self._login_frame.pack(fill="both", expand=True)

        self._main_frame: Optional[ttk.Frame] = None
        self._notebook: Optional[ttk.Notebook] = None
        self._chatroom_frame: Optional[ChatRoomFrame] = None

    def _on_login_success(self, client: ConferenceClient, username: str):
        self._client = client
        self._username = username
        self._login_frame.pack_forget()
        self._build_main_ui()

    def _build_main_ui(self):
        self._main_frame = ttk.Frame(self)
        self._main_frame.pack(fill="both", expand=True)

        top_bar = ttk.Frame(self._main_frame)
        top_bar.pack(fill="x", padx=10, pady=5)
        ttk.Label(
            top_bar, text=f"当前用户: {self._username}", font=("微软雅黑", 11)
        ).pack(side="left")
        ttk.Button(top_bar, text="登出", command=self._on_logout).pack(side="right")

        self._notebook = ttk.Notebook(self._main_frame)
        self._notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # 联系人页面
        self._contact_frame = ContactFrame(self._notebook, self._client, self._username)
        self._notebook.add(self._contact_frame, text="电话本")

        # IP电话页面（修改后）
        self._telephone_frame = TelephoneFrame(
            self._notebook, self._client, self._enter_room
        )
        self._notebook.add(self._telephone_frame, text="IP电话")

    def _enter_room(self, room_id: str):
        """进入聊天室"""
        if self._chatroom_frame:
            self._chatroom_frame.destroy()

        self._chatroom_frame = ChatRoomFrame(
            self._notebook, self._client, room_id, self._exit_room
        )
        self._notebook.add(self._chatroom_frame, text="聊天室")
        self._notebook.select(self._chatroom_frame)

        # 禁用IP电话页面的创建按钮
        self._telephone_frame.btn_create.config(state="disabled")

    def _exit_room(self):
        """退出聊天室"""
        if self._chatroom_frame:
            idx = self._notebook.index(self._chatroom_frame)
            self._notebook.forget(idx)
            self._chatroom_frame.destroy()
            self._chatroom_frame = None

        # 恢复IP电话页面
        self._telephone_frame._reset_ui()
        self._notebook.select(self._telephone_frame)

    def _on_logout(self):
        if messagebox.askyesno("确认", "确定要登出吗?"):
            if self._client:
                self._client.logout()
            if self._main_frame:
                self._main_frame.pack_forget()
                self._main_frame.destroy()
                self._main_frame = None
            self._chatroom_frame = None
            self._notebook = None
            self._login_frame = LoginFrame(self, self._on_login_success)
            self._login_frame.pack(fill="both", expand=True)


def run_app():
    app = ClientGUI()
    app.mainloop()


if __name__ == "__main__":
    run_app()
