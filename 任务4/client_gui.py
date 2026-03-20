"""
任务4：IP电话系统客户端图形界面

包含登录界面、联系人管理界面和IP电话界面。
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Optional

from telephone_client import TelephoneClient, CallState
from pathlib import Path
import sys

# 添加任务2路径
def _ensure_task2_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task2_dir = base / "任务2"
    if str(task2_dir) not in sys.path:
        sys.path.insert(0, str(task2_dir))


_ensure_task2_on_path()

from audio_config import DEFAULT_HOST


class LoginFrame(ttk.Frame):
    """登录Frame"""
    
    def __init__(self, parent, on_login_success):
        super().__init__(parent)
        self._on_login_success = on_login_success
        self._client: Optional[TelephoneClient] = None
        self._build_ui()
    
    def _build_ui(self) -> None:
        # 标题
        title = ttk.Label(self, text="IP电话系统 - 登录", font=("微软雅黑", 16))
        title.pack(pady=20)
        
        # 服务器配置
        server_box = ttk.Labelframe(self, text="服务器配置", padding=10)
        server_box.pack(fill="x", padx=20, pady=5)
        
        ttk.Label(server_box, text="服务器地址").grid(row=0, column=0, sticky="w")
        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        ttk.Entry(server_box, textvariable=self.host_var, width=20).grid(row=0, column=1, padx=(8, 0), sticky="w")
        
        ttk.Label(server_box, text="端口").grid(row=0, column=2, sticky="w", padx=(20, 0))
        self.port_var = tk.StringVar(value="8881")
        ttk.Entry(server_box, textvariable=self.port_var, width=8).grid(row=0, column=3, padx=(8, 0), sticky="w")
        
        # 登录区域
        auth_box = ttk.Labelframe(self, text="用户登录", padding=10)
        auth_box.pack(fill="x", padx=20, pady=10)
        
        ttk.Label(auth_box, text="用户名").grid(row=0, column=0, sticky="w")
        self.username_var = tk.StringVar()
        self.username_entry = ttk.Entry(auth_box, textvariable=self.username_var, width=20)
        self.username_entry.grid(row=0, column=1, padx=(8, 0), sticky="w")
        self.username_entry.bind("<Return>", lambda e: self._on_login())
        
        # 按钮
        btn_box = ttk.Frame(auth_box)
        btn_box.grid(row=1, column=0, columnspan=2, pady=(15, 0))
        
        self.btn_login = ttk.Button(btn_box, text="登录", command=self._on_login)
        self.btn_login.pack(side="left", padx=5)
        
        # 提示
        hint = ttk.Label(self, text="提示：输入用户名即可登录", foreground="gray")
        hint.pack(pady=10)
    
    def _on_login(self) -> None:
        username = self.username_var.get().strip()
        
        if not username:
            messagebox.showwarning("输入错误", "请输入用户名")
            return
        
        host = self.host_var.get().strip() or DEFAULT_HOST
        port = int(self.port_var.get().strip() or 8881)
        
        self.btn_login.config(state="disabled")
        
        def do_login():
            client = TelephoneClient(host=host, port=port)
            success, msg = client.login(username)
            
            self.after(0, lambda: self._handle_login_result(client, success, msg))
        
        threading.Thread(target=do_login, daemon=True).start()
    
    def _handle_login_result(self, client: TelephoneClient, success: bool, msg: str) -> None:
        self.btn_login.config(state="normal")
        
        if success:
            self._on_login_success(client, self.username_var.get().strip())
        else:
            messagebox.showerror("登录失败", msg)


class ContactFrame(ttk.Frame):
    """联系人管理Frame"""
    
    def __init__(self, parent, client: TelephoneClient, username: str):
        super().__init__(parent)
        self._client = client
        self._username = username
        self._build_ui()
        self._refresh_contacts()
    
    def _build_ui(self) -> None:
        # 标题
        title = ttk.Label(self, text=f"电话本 - {self._username}", font=("微软雅黑", 14))
        title.pack(pady=10)
        
        # 工具栏
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=10, pady=5)
        
        ttk.Button(toolbar, text="添加联系人", command=self._on_add).pack(side="left", padx=2)
        ttk.Button(toolbar, text="删除联系人", command=self._on_delete).pack(side="left", padx=2)
        ttk.Button(toolbar, text="修改联系人", command=self._on_update).pack(side="left", padx=2)
        ttk.Button(toolbar, text="刷新", command=self._refresh_contacts).pack(side="left", padx=2)
        
        # 搜索
        search_frame = ttk.Frame(self)
        search_frame.pack(fill="x", padx=10, pady=5)
        
        ttk.Label(search_frame, text="搜索:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=20)
        self.search_entry.pack(side="left", padx=5)
        ttk.Button(search_frame, text="查找", command=self._on_search).pack(side="left", padx=2)
        ttk.Button(search_frame, text="显示全部", command=self._refresh_contacts).pack(side="left", padx=2)
        
        # 联系人列表
        list_box = ttk.Labelframe(self, text="联系人列表", padding=5)
        list_box.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.contact_listbox = tk.Listbox(list_box, height=15)
        self.contact_listbox.pack(side="left", fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(list_box, orient="vertical", command=self.contact_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.contact_listbox.config(yscrollcommand=scrollbar.set)
        
        # 绑定双击事件
        self.contact_listbox.bind("<Double-Button-1>", self._on_double_click)
    
    def _refresh_contacts(self) -> None:
        contacts = self._client.get_contacts()
        self._update_list(contacts)
    
    def _update_list(self, contacts: list) -> None:
        self.contact_listbox.delete(0, tk.END)
        for contact in contacts:
            self.contact_listbox.insert(tk.END, contact)
    
    def _on_add(self) -> None:
        contact_name = simpledialog.askstring("添加联系人", "请输入联系人用户名:")
        if not contact_name:
            return
        
        success, msg = self._client.add_contact(contact_name)
        if success:
            messagebox.showinfo("成功", msg)
            self._refresh_contacts()
        else:
            messagebox.showerror("失败", msg)
    
    def _on_delete(self) -> None:
        selection = self.contact_listbox.curselection()
        if not selection:
            messagebox.showwarning("未选择", "请先选择一个联系人")
            return
        
        contact_name = self.contact_listbox.get(selection[0])
        if messagebox.askyesno("确认", f"确定要删除联系人 {contact_name} 吗?"):
            success, msg = self._client.delete_contact(contact_name)
            if success:
                messagebox.showinfo("成功", msg)
                self._refresh_contacts()
            else:
                messagebox.showerror("失败", msg)
    
    def _on_update(self) -> None:
        selection = self.contact_listbox.curselection()
        if not selection:
            messagebox.showwarning("未选择", "请先选择一个联系人")
            return
        
        old_name = self.contact_listbox.get(selection[0])
        new_name = simpledialog.askstring("修改联系人", f"请输入新的用户名:", initialvalue=old_name)
        
        if not new_name or new_name == old_name:
            return
        
        success, msg = self._client.update_contact(old_name, new_name)
        if success:
            messagebox.showinfo("成功", msg)
            self._refresh_contacts()
        else:
            messagebox.showerror("失败", msg)
    
    def _on_search(self) -> None:
        keyword = self.search_var.get().strip()
        if not keyword:
            self._refresh_contacts()
            return
        
        contacts = self._client.search_contacts(keyword)
        self._update_list(contacts)
    
    def _on_double_click(self, event) -> None:
        """双击联系人发起呼叫"""
        selection = self.contact_listbox.curselection()
        if not selection:
            return
        
        contact_name = self.contact_listbox.get(selection[0])
        # 触发主窗口的呼叫事件
        self.event_generate("<<CallContact>>", x=0, y=0, when="tail")
        # 保存选中的联系人
        self._selected_contact = contact_name


class TelephoneFrame(ttk.Frame):
    """IP电话Frame"""
    
    def __init__(self, parent, client: TelephoneClient):
        super().__init__(parent)
        self._client = client
        self._build_ui()
        
        # 设置状态回调
        self._client._on_call_state_change = self._on_state_change
    
    def _build_ui(self) -> None:
        # 标题
        title = ttk.Label(self, text="IP电话", font=("微软雅黑", 14))
        title.pack(pady=10)
        
        # 状态显示
        status_box = ttk.Labelframe(self, text="通话状态", padding=10)
        status_box.pack(fill="x", padx=10, pady=5)
        
        # 状态指示灯
        self._lamp_canvas = tk.Canvas(status_box, width=20, height=20, highlightthickness=0)
        self._lamp_canvas.pack(side="left", padx=(0, 10))
        self._lamp = self._lamp_canvas.create_oval(2, 2, 18, 18, fill="#b0b0b0", outline="#808080")
        
        self.status_var = tk.StringVar(value="空闲")
        ttk.Label(status_box, textvariable=self.status_var, font=("微软雅黑", 12)).pack(side="left")
        
        # 通话对象
        call_target_box = ttk.Labelframe(self, text="通话对象", padding=10)
        call_target_box.pack(fill="x", padx=10, pady=5)
        
        ttk.Label(call_target_box, text="用户:").pack(side="left")
        self.target_var = tk.StringVar()
        self.target_entry = ttk.Entry(call_target_box, textvariable=self.target_var, width=15)
        self.target_entry.pack(side="left", padx=5)
        
        self.btn_call = ttk.Button(call_target_box, text="呼叫", command=self._on_call)
        self.btn_call.pack(side="left", padx=5)
        
        self.btn_accept = ttk.Button(call_target_box, text="接听", command=self._on_accept, state="disabled")
        self.btn_accept.pack(side="left", padx=5)
        
        self.btn_reject = ttk.Button(call_target_box, text="拒绝", command=self._on_reject, state="disabled")
        self.btn_reject.pack(side="left", padx=5)
        
        self.btn_hangup = ttk.Button(call_target_box, text="挂断", command=self._on_hangup, state="disabled")
        self.btn_hangup.pack(side="left", padx=5)
        
        # 提示信息
        hint_box = ttk.Labelframe(self, text="操作提示", padding=10)
        hint_box.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.hint_text = tk.Text(hint_box, height=10, wrap="word", state="disabled")
        self.hint_text.pack(fill="both", expand=True)
        
        self._update_hint("空闲状态\n\n1. 在上方输入用户名，点击「呼叫」发起通话\n2. 收到呼叫时，点击「接听」或「拒绝」\n3. 通话中可随时点击「挂断」结束")
    
    def _update_hint(self, text: str) -> None:
        self.hint_text.config(state="normal")
        self.hint_text.delete("1.0", tk.END)
        self.hint_text.insert("1.0", text)
        self.hint_text.config(state="disabled")
    
    def _on_state_change(self, state: str, target: str) -> None:
        self.after(0, lambda: self._update_state_ui(state, target))
    
    def _update_state_ui(self, state: str, target: str) -> None:
        # 更新状态灯
        if state == CallState.IDLE:
            self._lamp_canvas.itemconfig(self._lamp, fill="#b0b0b0")
            self.status_var.set("空闲")
            self._update_hint("空闲状态\n\n1. 在上方输入用户名，点击「呼叫」发起通话\n2. 收到呼叫时，点击「接听」或「拒绝」\n3. 通话中可随时点击「挂断」结束")
            self.btn_call.config(state="normal")
            self.btn_accept.config(state="disabled")
            self.btn_reject.config(state="disabled")
            self.btn_hangup.config(state="disabled")
            self.target_entry.config(state="normal")
        
        elif state == CallState.CALLING:
            self._lamp_canvas.itemconfig(self._lamp, fill="#f0a030")
            self.status_var.set(f"正在呼叫 {target}...")
            self._update_hint(f"正在呼叫 {target}...\n\n等待对方接听...")
            self.btn_call.config(state="disabled")
            self.btn_hangup.config(state="normal")
            self.target_entry.config(state="disabled")
        
        elif state == CallState.RINGING:
            self._lamp_canvas.itemconfig(self._lamp, fill="#30a0f0")
            self.status_var.set(f"来自 {target} 的呼叫")
            self._update_hint(f"来自 {target} 的呼叫\n\n点击「接听」开始通话，或点击「拒绝」拒绝接听")
            self.target_var.set(target)
            self.btn_call.config(state="disabled")
            self.btn_accept.config(state="normal")
            self.btn_reject.config(state="normal")
            self.btn_hangup.config(state="disabled")
        
        elif state == CallState.IN_CALL:
            self._lamp_canvas.itemconfig(self._lamp, fill="#3ad04a")
            self.status_var.set(f"与 {target} 通话中")
            self._update_hint(f"与 {target} 通话中\n\n音频设备已开启，可以开始通话\n点击「挂断」结束通话")
            self.btn_call.config(state="disabled")
            self.btn_accept.config(state="disabled")
            self.btn_reject.config(state="disabled")
            self.btn_hangup.config(state="normal")
            self.target_entry.config(state="disabled")
        
        elif state == CallState.ENDED:
            self._lamp_canvas.itemconfig(self._lamp, fill="#b0b0b0")
            self.status_var.set("通话结束")
            self._update_hint("通话已结束")
            self.btn_call.config(state="normal")
            self.btn_accept.config(state="disabled")
            self.btn_reject.config(state="disabled")
            self.btn_hangup.config(state="disabled")
            self.target_entry.config(state="normal")
    
    def _on_call(self) -> None:
        target = self.target_var.get().strip()
        if not target:
            messagebox.showwarning("输入错误", "请输入要呼叫的用户名")
            return
        
        self._client.call(target)
    
    def _on_accept(self) -> None:
        target = self._client._in_call_with or self.target_var.get().strip()
        if target:
            self._client.accept_call(target)
    
    def _on_reject(self) -> None:
        target = self._client._in_call_with or self.target_var.get().strip()
        if target:
            self._client.reject_call(target)
    
    def _on_hangup(self) -> None:
        self._client.hangup()


class ClientGUI(tk.Tk):
    """主窗口"""
    
    def __init__(self) -> None:
        super().__init__()
        self.title("任务4 - IP电话系统客户端")
        self.geometry("600x500")
        self.minsize(500, 400)
        
        self._client: Optional[TelephoneClient] = None
        self._username: str = ""
        
        # 登录Frame
        self._login_frame = LoginFrame(self, self._on_login_success)
        self._login_frame.pack(fill="both", expand=True)
        
        # 登录成功后切换
        self._main_frame: Optional[ttk.Frame] = None
    
    def _on_login_success(self, client: TelephoneClient, username: str) -> None:
        self._client = client
        self._username = username
        
        # 隐藏登录Frame
        self._login_frame.pack_forget()
        
        # 创建主界面
        self._build_main_ui()
    
    def _build_main_ui(self) -> None:
        self._main_frame = ttk.Frame(self)
        self._main_frame.pack(fill="both", expand=True)
        
        # 顶部栏
        top_bar = ttk.Frame(self._main_frame)
        top_bar.pack(fill="x", padx=10, pady=5)
        
        ttk.Label(top_bar, text=f"当前用户: {self._username}", font=("微软雅黑", 11)).pack(side="left")
        ttk.Button(top_bar, text="登出", command=self._on_logout).pack(side="right")
        
        # 标签页
        notebook = ttk.Notebook(self._main_frame)
        notebook.pack(fill="both", expand=True, padx=10, pady=5)
        
        # 联系人页面
        self._contact_frame = ContactFrame(notebook, self._client, self._username)
        self._contact_frame.bind("<<CallContact>>", self._on_call_from_contact)
        notebook.add(self._contact_frame, text="电话本")
        
        # 电话页面
        self._telephone_frame = TelephoneFrame(notebook, self._client)
        notebook.add(self._telephone_frame, text="IP电话")
    
    def _on_call_from_contact(self, event) -> None:
        # 从联系人列表发起呼叫
        if hasattr(self._contact_frame, '_selected_contact'):
            target = self._contact_frame._selected_contact
            self._telephone_frame.target_var.set(target)
            self._client.call(target)
    
    def _on_logout(self) -> None:
        if messagebox.askyesno("确认", "确定要登出吗?"):
            if self._client:
                self._client.logout()
            
            # 清理主界面
            if self._main_frame:
                self._main_frame.pack_forget()
            
            # 重新显示登录界面
            self._login_frame = LoginFrame(self, self._on_login_success)
            self._login_frame.pack(fill="both", expand=True)


def run_app() -> None:
    app = ClientGUI()
    app.mainloop()


if __name__ == "__main__":
    run_app()
