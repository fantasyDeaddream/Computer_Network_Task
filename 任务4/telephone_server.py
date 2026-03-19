"""
任务4：IP电话系统服务器

负责处理用户登录、联系人管理和呼叫信令。
"""

import json
import socket
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
import base64

from telephone_protocol import decode_message, encode_response
from telephone_protocol import (
    encode_call_accept, encode_call_reject, encode_call_hangup,
    encode_call_busy, encode_call_not_found, encode_text, encode_audio_chunk
)
from data_store import get_data_store


# 默认端口
DEFAULT_PORT = 8881  # 与任务3区分
MESSAGE_DELIMITER = '\n'


@dataclass
class ClientInfo:
    """客户端连接信息"""
    conn: socket.socket
    addr: Tuple[str, int]
    username: str = ""
    in_call_with: str = ""  # 当前通话的用户


class TelephoneServer:
    """IP电话服务器"""
    
    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._clients: Dict[int, ClientInfo] = {}  # conn_id -> ClientInfo
        self._username_map: Dict[str, int] = {}  # username -> conn_id
        self._lock = threading.RLock()  # 使用可重入锁，避免嵌套获取时死锁
        self._running = False
        self._data_store = get_data_store()
    
    # ========== 公共接口 ==========
    
    def start(self) -> None:
        """启动服务器"""
        self._sock.bind((self._host, self._port))
        self._sock.listen(10)
        self._running = True
        print(f"[Server] TelephoneServer 监听 {self._host}:{self._port}")
        
        try:
            while self._running:
                try:
                    conn, addr = self._sock.accept()
                except OSError:
                    break
                threading.Thread(
                    target=self._handle_client, args=(conn, addr), daemon=True
                ).start()
        finally:
            self._cleanup()
            print("[Server] TelephoneServer 已关闭")
    
    def stop(self) -> None:
        """停止服务器"""
        self._running = False
        try:
            self._sock.close()
        except Exception:
            pass
    
    # ========== 内部实现 ==========
    
    def _cleanup(self) -> None:
        """清理资源"""
        with self._lock:
            for cid, info in list(self._clients.items()):
                try:
                    info.conn.close()
                except Exception:
                    pass
            self._clients.clear()
            self._username_map.clear()
        try:
            self._sock.close()
        except Exception:
            pass
    
    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        """处理客户端连接"""
        cid = id(conn)
        
        with self._lock:
            self._clients[cid] = ClientInfo(conn=conn, addr=addr)
        print(f"[Server] 新连接: {addr}")
        
        buffer = ""
        try:
            while self._running:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")
                while MESSAGE_DELIMITER in buffer:
                    line, buffer = buffer.split(MESSAGE_DELIMITER, 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        mtype, payload = decode_message(line)
                    except ValueError as e:
                        print(f"[Server] 无效消息: {e}")
                        continue
                    self._process_message(cid, mtype, payload, conn, line)
        except ConnectionResetError:
            pass
        finally:
            self._handle_disconnect(cid)
            print(f"[Server] 连接关闭: {addr}")
    
    def _process_message(self, cid: int, mtype: str, payload: dict, conn: socket.socket, raw_line: str) -> None:
        """处理各类消息"""
        if mtype == "login":
            self._handle_login(cid, payload, conn)
        elif mtype == "logout":
            self._handle_logout(cid)
        elif mtype == "contact_add":
            self._handle_contact_add(cid, payload)
        elif mtype == "contact_delete":
            self._handle_contact_delete(cid, payload)
        elif mtype == "contact_update":
            self._handle_contact_update(cid, payload)
        elif mtype == "contact_list":
            self._handle_contact_list(cid, payload)
        elif mtype == "contact_search":
            self._handle_contact_search(cid, payload)
        elif mtype == "call_invite":
            self._handle_call_invite(cid, payload)
        elif mtype == "call_accept":
            self._handle_call_accept(cid, payload)
        elif mtype == "call_reject":
            self._handle_call_reject(cid, payload)
        elif mtype == "call_hangup":
            self._handle_call_hangup(cid, payload)
        elif mtype == "text":
            self._handle_text(cid, payload)
        elif mtype == "audio_chunk":
            # 直接转发原始消息
            self._forward_audio(cid, raw_line)
    
    def _handle_login(self, cid: int, payload: dict, conn: socket.socket) -> None:
        """处理登录请求（输入用户名即可登录，自动创建用户）"""
        username = payload.get("username", "")
        
        if not username:
            response = encode_response(False, "用户名不能为空")
            self._send(conn, response)
            return
        
        # 自动创建用户（如果不存在）
        self._data_store.ensure_user(username)
        
        with self._lock:
            # 检查是否已登录
            if username in self._username_map:
                response = encode_response(False, "用户已在线")
                self._send(conn, response)
                return
            
            info = self._clients.get(cid)
            if info:
                info.username = username
            self._username_map[username] = cid
        
        response = encode_response(True, "登录成功")
        self._send(conn, response)
    
    def _handle_logout(self, cid: int) -> None:
        """处理登出请求"""
        with self._lock:
            info = self._clients.get(cid)
            if info and info.username:
                # 如果在通话中，先挂断
                if info.in_call_with:
                    self._end_call(info.username, info.in_call_with)
                
                self._username_map.pop(info.username, None)
                info.username = ""
    
    def _handle_disconnect(self, cid: int) -> None:
        """处理客户端断开"""
        username = ""
        in_call_with = ""
        with self._lock:
            info = self._clients.pop(cid, None)
            if info:
                username = info.username
                in_call_with = info.in_call_with
                if username:
                    self._username_map.pop(username, None)
        
        # 如果在通话中，结束通话
        if in_call_with:
            self._end_call(username, in_call_with)
    
    def _handle_contact_add(self, cid: int, payload: dict) -> None:
        """处理添加联系人"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                response = encode_response(False, "未登录")
                self._send_by_cid(cid, response)
                return
            username = info.username
        
        contact_name = payload.get("contact_name", "")
        if not contact_name:
            response = encode_response(False, "联系人名称不能为空")
            self._send_by_cid(cid, response)
            return
        
        success, msg = self._data_store.add_contact(username, contact_name)
        response = encode_response(success, msg)
        self._send_by_cid(cid, response)
    
    def _handle_contact_delete(self, cid: int, payload: dict) -> None:
        """处理删除联系人"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                response = encode_response(False, "未登录")
                self._send_by_cid(cid, response)
                return
            username = info.username
        
        contact_name = payload.get("contact_name", "")
        success, msg = self._data_store.delete_contact(username, contact_name)
        response = encode_response(success, msg)
        self._send_by_cid(cid, response)
    
    def _handle_contact_update(self, cid: int, payload: dict) -> None:
        """处理更新联系人"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                response = encode_response(False, "未登录")
                self._send_by_cid(cid, response)
                return
            username = info.username
        
        old_name = payload.get("old_name", "")
        new_name = payload.get("new_name", "")
        if not old_name or not new_name:
            response = encode_response(False, "联系人名称不能为空")
            self._send_by_cid(cid, response)
            return
        
        success, msg = self._data_store.update_contact(username, old_name, new_name)
        response = encode_response(success, msg)
        self._send_by_cid(cid, response)
    
    def _handle_contact_list(self, cid: int, payload: dict) -> None:
        """处理获取联系人列表"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                response = encode_response(False, "未登录")
                self._send_by_cid(cid, response)
                return
            username = info.username
        
        contacts = self._data_store.get_contacts(username)
        response = encode_response(True, "获取成功", {"contacts": contacts})
        self._send_by_cid(cid, response)
    
    def _handle_contact_search(self, cid: int, payload: dict) -> None:
        """处理搜索联系人"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                response = encode_response(False, "未登录")
                self._send_by_cid(cid, response)
                return
            username = info.username
        
        keyword = payload.get("keyword", "")
        contacts = self._data_store.search_contacts(username, keyword)
        response = encode_response(True, "搜索成功", {"contacts": contacts})
        self._send_by_cid(cid, response)
    
    def _handle_call_invite(self, cid: int, payload: dict) -> None:
        """处理呼叫请求"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            caller = info.username
        
        target = payload.get("target", "")
        if not target:
            return
        
        # 先在锁内读取状态，再在锁外发送消息
        with self._lock:
            # 检查目标用户是否存在且在线
            if target not in self._username_map:
                # 用户不在线，发送提示
                response = encode_call_not_found(target, caller)
                self._send_by_cid(cid, response)
                return
            
            target_cid = self._username_map[target]
            target_info = self._clients.get(target_cid)
            
            # 检查目标是否正在通话
            if target_info and target_info.in_call_with:
                response = encode_call_busy(target, caller)
                self._send_by_cid(cid, response)
                return
            
            # 转发呼叫请求给目标
            invite_msg = encode_call_invite_msg(caller, target)
            self._send_by_cid(target_cid, invite_msg)
    
    def _handle_call_accept(self, cid: int, payload: dict) -> None:
        """处理接听请求"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            callee = info.username
        
        caller = payload.get("caller", "")
        if not caller:
            return
        
        with self._lock:
            if caller not in self._username_map:
                return
            caller_cid = self._username_map[caller]
            caller_info = self._clients.get(caller_cid)
            
            # 建立通话
            info.in_call_with = caller
            if caller_info:
                caller_info.in_call_with = callee
        
        # 通知双方通话已开始
        accept_msg = encode_call_accept(callee, caller)
        self._send_by_cid(cid, accept_msg)
        
        caller_msg = encode_call_accept(caller, callee)
        self._send_by_cid(caller_cid, caller_msg)
    
    def _handle_call_reject(self, cid: int, payload: dict) -> None:
        """处理拒绝接听"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            username = info.username
        
        caller = payload.get("caller", "")
        
        with self._lock:
            if caller in self._username_map:
                caller_cid = self._username_map[caller]
                reject_msg = encode_call_reject(username, caller, "对方拒绝接听")
                self._send_by_cid(caller_cid, reject_msg)
    
    def _handle_call_hangup(self, cid: int, payload: dict) -> None:
        """处理挂断"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username:
                return
            username = info.username
        
        target = payload.get("target", "")
        self._end_call(username, target)
    
    def _end_call(self, username: str, target: str) -> None:
        """结束通话"""
        # 通知对方
        with self._lock:
            if target in self._username_map:
                target_cid = self._username_map[target]
                hangup_msg = encode_call_hangup(username, target)
                self._send_by_cid(target_cid, hangup_msg)
                
                target_info = self._clients.get(target_cid)
                if target_info:
                    target_info.in_call_with = ""
            
            # 清理通话状态
            if username in self._username_map:
                cid = self._username_map[username]
                info = self._clients.get(cid)
                if info:
                    info.in_call_with = ""
    
    def _handle_text(self, cid: int, payload: dict) -> None:
        """处理文本消息（通话中）"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.username or not info.in_call_with:
                return
            target = info.in_call_with
            username = info.username
        
        content = payload.get("content", "")
        text_msg = encode_text(f"{username}: {content}")
        
        with self._lock:
            if target in self._username_map:
                target_cid = self._username_map[target]
                self._send_by_cid(target_cid, text_msg)
    
    def _forward_audio(self, cid: int, raw_line: str) -> None:
        """转发音频数据"""
        with self._lock:
            info = self._clients.get(cid)
            if not info or not info.in_call_with:
                return
            target = info.in_call_with
        
        with self._lock:
            if target in self._username_map:
                target_cid = self._username_map[target]
                self._send_by_cid(target_cid, raw_line)
    
    def _send(self, conn: socket.socket, msg: str) -> None:
        """发送消息"""
        try:
            conn.sendall((msg + MESSAGE_DELIMITER).encode("utf-8"))
        except Exception:
            pass
    
    def _send_by_cid(self, cid: int, msg: str) -> None:
        """通过连接ID发送消息"""
        with self._lock:
            info = self._clients.get(cid)
            if info:
                self._send(info.conn, msg)


def encode_call_invite_msg(caller: str, target: str) -> str:
    """编码呼叫邀请消息"""
    import json
    msg = {"type": "call_invite", "caller": caller, "target": target}
    return json.dumps(msg, ensure_ascii=False)


def main() -> None:
    """主函数"""
    server = TelephoneServer()
    try:
        print("=" * 50)
        print("任务4 - IP电话系统服务器")
        print("=" * 50)
        print()
        print("服务器正在启动...")
        print(f"使用端口: {DEFAULT_PORT}")
        print("按 Ctrl+C 停止服务器。")
        print()
        server.start()
    except KeyboardInterrupt:
        print("\n[Server] 收到中断信号，正在关闭服务器...")
        server.stop()


if __name__ == "__main__":
    main()
