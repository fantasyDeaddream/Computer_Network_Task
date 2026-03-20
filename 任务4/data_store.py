"""
任务4：数据存储模块

负责用户和联系人数据的持久化存储。
数据存储在 JSON 文件中。
"""

import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional


class DataStore:
    """数据存储类，管理用户和联系人"""
    
    def __init__(self, data_dir: str = "data"):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(exist_ok=True)
        
        self._users_file = self._data_dir / "users.json"
        self._contacts_dir = self._data_dir / "contacts"
        self._contacts_dir.mkdir(exist_ok=True)
        
        self._lock = threading.Lock()
        
        # 初始化用户文件
        if not self._users_file.exists():
            self._save_users({})
    
    def _load_users(self) -> Dict[str, dict]:
        """加载用户数据"""
        try:
            with open(self._users_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _save_users(self, users: Dict[str, dict]) -> None:
        """保存用户数据"""
        with open(self._users_file, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    
    def _get_contacts_file(self, username: str) -> Path:
        """获取用户联系人文件路径"""
        # 使用安全的文件名
        safe_name = username.replace("/", "_").replace("\\", "_")
        return self._contacts_dir / f"{safe_name}.json"
    
    def _load_contacts(self, username: str) -> List[str]:
        """加载用户联系人"""
        contacts_file = self._get_contacts_file(username)
        try:
            with open(contacts_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("contacts", [])
        except (FileNotFoundError, json.JSONDecodeError):
            return []
    
    def _save_contacts(self, username: str, contacts: List[str]) -> None:
        """保存用户联系人"""
        contacts_file = self._get_contacts_file(username)
        with open(contacts_file, "w", encoding="utf-8") as f:
            json.dump({"contacts": contacts}, f, ensure_ascii=False, indent=2)
    
    # ========== 用户管理 ==========
    
    def ensure_user(self, username: str) -> None:
        """
        确保用户存在，如果不存在则自动创建。
        """
        with self._lock:
            users = self._load_users()
            
            if username not in users:
                users[username] = {"username": username}
                self._save_users(users)
                # 初始化空联系人
                self._save_contacts(username, [])
    
    def user_exists(self, username: str) -> bool:
        """检查用户是否存在"""
        users = self._load_users()
        return username in users
    
    # ========== 联系人管理 ==========
    
    def add_contact(self, username: str, contact_name: str) -> tuple[bool, str]:
        """
        添加联系人
        """
        with self._lock:
            contacts = self._load_contacts(username)
            
            if contact_name in contacts:
                return False, "联系人已存在"
            
            contacts.append(contact_name)
            self._save_contacts(username, contacts)
            
            return True, "联系人添加成功"
    
    def delete_contact(self, username: str, contact_name: str) -> tuple[bool, str]:
        """
        删除联系人
        """
        with self._lock:
            contacts = self._load_contacts(username)
            
            if contact_name not in contacts:
                return False, "联系人不存在"
            
            contacts.remove(contact_name)
            self._save_contacts(username, contacts)
            
            return True, "联系人删除成功"
    
    def update_contact(self, username: str, old_name: str, new_name: str) -> tuple[bool, str]:
        """
        更新联系人
        """
        with self._lock:
            contacts = self._load_contacts(username)
            
            if old_name not in contacts:
                return False, "联系人不存在"
            
            idx = contacts.index(old_name)
            contacts[idx] = new_name
            self._save_contacts(username, contacts)
            
            return True, "联系人更新成功"
    
    def get_contacts(self, username: str) -> List[str]:
        """
        获取所有联系人
        """
        return self._load_contacts(username)
    
    def search_contacts(self, username: str, keyword: str) -> List[str]:
        """
        搜索联系人
        """
        contacts = self._load_contacts(username)
        if not keyword:
            return contacts
        return [c for c in contacts if keyword.lower() in c.lower()]


# 全局数据存储实例
_data_store: Optional[DataStore] = None


def get_data_store() -> DataStore:
    """获取全局数据存储实例"""
    global _data_store
    if _data_store is None:
        _data_store = DataStore()
    return _data_store
