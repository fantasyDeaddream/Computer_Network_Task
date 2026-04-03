#!/usr/bin/env python3
# GUI wrapper for merged_server_dualmode

import sys
import threading

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import stable_merged_server_dualmode as core

# 补充 stable 版本缺失的管理函数
def snapshot_clients():
    out = {}
    with core.clients_lock:
        names = list(core.clients.keys())
        metas = {u: dict(core.client_net_meta.get(u, {})) for u in names}

    for username in names:
        meta = metas.get(username, {})
        out[username] = {
            "p2p_ip": meta.get("p2p_ip", ""),
            "p2p_port": int(meta.get("p2p_port", 0) or 0),
            "status": core.user_status(username),
        }
    return out

def kick_user(username):
    username = str(username or "").strip()
    if not username:
        return False, "用户名为空"
    with core.clients_lock:
        exists = username in core.clients
    if not exists:
        return False, f"用户 {username} 不在线"
    core.remove_client(username)
    return True, f"已强制下线 {username}"



class ServerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Merge 服务端控制台")
        self.resize(900, 640)

        cw = QWidget()
        self.setCentralWidget(cw)
        layout = QVBoxLayout(cw)

        self.info_bar = QLabel("服务端准备启动...")
        self.info_bar.setStyleSheet("background: #1d4ed8; color: white; padding: 10px; font-weight: bold;")
        layout.addWidget(self.info_bar)

        layout.addWidget(QLabel("控制台日志"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("background: #111827; color: #86efac; font-family: Consolas;")
        layout.addWidget(self.log, 2)

        layout.addWidget(QLabel("在线用户"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["用户名", "状态", "P2P IP", "P2P端口", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        self._server_thread = None
        self._running = False

        self._start_server_thread()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_view)
        self.timer.start(1000)

    def _start_server_thread(self):
        if self._running:
            return
        self._running = True

        def worker():
            self._log("[系统] 服务端线程启动")
            try:
                # 兼容 stable 版本
                core.main()
            except Exception as e:
                self._log(f"[错误] 服务端异常: {e}")
            finally:
                self._running = False
                self._log("[系统] 服务端线程已停止")

        self._server_thread = threading.Thread(target=worker, daemon=True)
        self._server_thread.start()

    def _log(self, text):
        self.log.appendPlainText(str(text))

    def refresh_view(self):
        self.info_bar.setText(
            f"SERVER | {core.HOST}:{core.PORT} | GROUP {core.HOST}:{core.GROUP_VOICE_PORT} | 在线: {len(core.online_users())}"
        )

        snap = snapshot_clients()
        self.table.setRowCount(0)

        for row, (username, info) in enumerate(sorted(snap.items())):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(username))
            self.table.setItem(row, 1, QTableWidgetItem(str(info.get("status", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(str(info.get("p2p_ip", ""))))
            self.table.setItem(row, 3, QTableWidgetItem(str(info.get("p2p_port", 0))))

            btn = QPushButton("强制下线")
            btn.clicked.connect(lambda _=False, u=username: self.kick_user_wrapped(u))
            self.table.setCellWidget(row, 4, btn)

    def kick_user_wrapped(self, username):
        ok, text = kick_user(username)
        if ok:
            self._log(f"[管理] {text}")
        else:
            QMessageBox.warning(self, "提示", text)

    def closeEvent(self, event):
        core.shutdown_event.set()
        self._log("[系统] 正在关闭服务端...")
        event.accept()


def main():
    app = QApplication(sys.argv)
    w = ServerGUI()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
