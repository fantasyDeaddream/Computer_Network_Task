#!/usr/bin/env python3
# threaded_tcp_chat_server_realtime.py

import socket
import threading
import json
import struct
import pickle
import os

HOST = '10.192.3.86'
PORT = 65432
MAX_MSG = 100 * 1024 * 1024
USERS_FILE = 'users.pkl'

clients = {}          # username -> (conn, addr)
clients_lock = threading.Lock()
users = {}            # username -> password
friends_map = {}      # username -> set(friend_username)
offline_messages = {} # username -> [{'from': ..., 'text': ...}, ...]
pending_friend_requests = {}  # target -> set(requester)
pending_calls = {}    # caller <-> callee (pending)
active_calls = {}     # user -> peer (active)
call_lock = threading.Lock()
friends_lock = threading.RLock()

# 同一连接的所有发送必须串行，避免通话音频帧与文本/状态消息交叉
conn_send_locks = {}
conn_send_locks_lock = threading.Lock()

shutdown_event = threading.Event()


def users_write():
    try:
        with friends_lock:
            payload = {
                'users': users,
                'friends': {u: sorted(list(v)) for u, v in friends_map.items()},
                'offline_messages': offline_messages,
            }
        with open(USERS_FILE, 'wb') as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        print('保存 users 失败:', e)


def users_read():
    global friends_map
    global offline_messages
    if not os.path.exists(USERS_FILE):
        friends_map = {}
        offline_messages = {}
        return {}
    try:
        with open(USERS_FILE, 'rb') as f:
            obj = pickle.load(f)
            if not isinstance(obj, dict):
                friends_map = {}
                offline_messages = {}
                return {}

            # v2: {'users': {...}, 'friends': {...}, 'offline_messages': {...}}
            if isinstance(obj.get('users'), dict):
                loaded_users = obj.get('users', {})
                raw_friends = obj.get('friends', {})
                raw_offline = obj.get('offline_messages', {})

                friends_map = {
                    u: set(v) if isinstance(v, (list, set, tuple)) else set()
                    for u, v in raw_friends.items()
                }
                offline_messages = {
                    u: list(v) if isinstance(v, list) else []
                    for u, v in raw_offline.items()
                }
                for u in loaded_users.keys():
                    friends_map.setdefault(u, set())
                    offline_messages.setdefault(u, [])
                return loaded_users

            # 兼容旧版：直接是 username -> password
            friends_map = {u: set() for u in obj.keys()}
            offline_messages = {u: [] for u in obj.keys()}
            return obj
    except Exception as e:
        print('读取 users 失败:', e)
        friends_map = {}
        offline_messages = {}
        return {}


def _ensure_user_meta(username):
    with friends_lock:
        friends_map.setdefault(username, set())
        offline_messages.setdefault(username, [])


def are_friends(user_a, user_b):
    with friends_lock:
        return user_b in friends_map.get(user_a, set())


def user_status(username):
    with call_lock:
        in_call = (username in active_calls) or (username in pending_calls)
    if in_call:
        return 'calling'
    return 'online_free' if get_online_conn(username) else 'not_online'


def send_friend_list(conn, username):
    with friends_lock:
        friends = sorted(list(friends_map.get(username, set())))
    payload = [{'username': f, 'status': user_status(f)} for f in friends]
    send_json(conn, {'type': 'friend_list_response', 'friends': payload})


def notify_friend_status(username):
    status = user_status(username)
    with friends_lock:
        friends = list(friends_map.get(username, set()))
    for f in friends:
        conn = get_online_conn(f)
        if not conn:
            continue
        try:
            send_json(conn, {'type': 'friend_status', 'friend': username, 'status': status})
        except Exception:
            pass


def deliver_offline_messages(conn, username):
    with friends_lock:
        msgs = list(offline_messages.get(username, []))
        offline_messages[username] = []
    if msgs:
        users_write()
    for msg in msgs:
        try:
            send_json(conn, {
                'type': 'message',
                'from': msg.get('from', '?'),
                'text': msg.get('text', ''),
                'offline': True,
            })
        except Exception:
            break


def handle_friend_request(conn, current_username, msg):
    to = (msg.get('to') or '').strip()
    if not to:
        send_json(conn, {'type': 'error', 'text': "缺少 'to' 字段"})
        return
    if to == current_username:
        send_json(conn, {'type': 'error', 'text': '不能添加自己为好友'})
        return
    if to not in users:
        send_json(conn, {'type': 'error', 'text': f'用户 {to} 不存在'})
        return
    if are_friends(current_username, to):
        send_json(conn, {'type': 'info', 'text': f'{to} 已是你的好友'})
        return

    target_conn = get_online_conn(to)
    if not target_conn:
        send_json(conn, {'type': 'error', 'text': f'用户 {to} 不在线，无法发送好友请求'})
        return

    with friends_lock:
        reqs = pending_friend_requests.setdefault(to, set())
        if current_username in reqs:
            send_json(conn, {'type': 'info', 'text': f'你已经向 {to} 发送过好友请求'})
            return
        reqs.add(current_username)

    try:
        send_json(target_conn, {'type': 'friend_request', 'from': current_username})
        send_json(conn, {'type': 'info', 'text': f'已向 {to} 发送好友请求'})
    except Exception:
        with friends_lock:
            pending_friend_requests.get(to, set()).discard(current_username)
            if not pending_friend_requests.get(to):
                pending_friend_requests.pop(to, None)
        send_json(conn, {'type': 'error', 'text': f'发送好友请求给 {to} 失败'})


def handle_friend_response(conn, current_username, msg):
    requester = (msg.get('to') or '').strip()
    accept = bool(msg.get('accept'))
    if not requester:
        send_json(conn, {'type': 'error', 'text': "缺少 'to' 字段"})
        return

    with friends_lock:
        reqs = pending_friend_requests.get(current_username, set())
        if requester not in reqs:
            send_json(conn, {'type': 'error', 'text': '该好友请求已失效'})
            return
        reqs.discard(requester)
        if not reqs:
            pending_friend_requests.pop(current_username, None)

    requester_conn = get_online_conn(requester)

    if not accept:
        if requester_conn:
            try:
                send_json(requester_conn, {'type': 'friend_rejected', 'from': current_username})
            except Exception:
                pass
        send_json(conn, {'type': 'info', 'text': f'已拒绝 {requester} 的好友请求'})
        return

    with friends_lock:
        friends_map.setdefault(current_username, set()).add(requester)
        friends_map.setdefault(requester, set()).add(current_username)
    users_write()

    my_conn = get_online_conn(current_username)
    if my_conn:
        try:
            send_json(my_conn, {'type': 'friend_added', 'friend': requester, 'status': user_status(requester)})
            send_friend_list(my_conn, current_username)
        except Exception:
            pass

    if requester_conn:
        try:
            send_json(requester_conn, {'type': 'friend_added', 'friend': current_username, 'status': user_status(current_username)})
            send_friend_list(requester_conn, requester)
        except Exception:
            pass

    notify_friend_status(current_username)
    notify_friend_status(requester)
    send_json(conn, {'type': 'info', 'text': f'你与 {requester} 已成为好友'})


def _get_conn_send_lock(conn):
    key = id(conn)
    with conn_send_locks_lock:
        lock = conn_send_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            conn_send_locks[key] = lock
        return lock


def _drop_conn_send_lock(conn):
    key = id(conn)
    with conn_send_locks_lock:
        conn_send_locks.pop(key, None)


def send_json(conn, obj):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    length = struct.pack('>I', len(data))
    lock = _get_conn_send_lock(conn)
    with lock:
        conn.sendall(length + data)


def send_raw(conn, data):
    lock = _get_conn_send_lock(conn)
    with lock:
        conn.sendall(data)


def recvall(conn, n):
    buf = b''
    while len(buf) < n:
        try:
            chunk = conn.recv(n - len(buf))
        except Exception:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_json(conn):
    raw_len = recvall(conn, 4)
    if not raw_len:
        return None
    msg_len = struct.unpack('>I', raw_len)[0]
    data = recvall(conn, msg_len)
    if not data:
        return None
    try:
        return json.loads(data.decode('utf-8'))
    except Exception:
        return None


def get_online_conn(username):
    with clients_lock:
        target = clients.get(username)
    return target[0] if target else None


def online_users():
    with clients_lock:
        return list(clients.keys())


def broadcast(msg_obj, exclude_username=None):
    recipients = []
    with clients_lock:
        for uname, (conn, _addr) in list(clients.items()):
            if uname == exclude_username:
                continue
            recipients.append((uname, conn))

    for uname, conn in recipients:
        try:
            send_json(conn, msg_obj)
        except Exception:
            print(f'广播给 {uname} 失败，移除客户端')
            remove_client(uname)


def clear_call_state(username, notify_peer=True, reason_text=None):
    peer = None
    with call_lock:
        if username in active_calls:
            peer = active_calls.pop(username)
            active_calls.pop(peer, None)
        elif username in pending_calls:
            peer = pending_calls.pop(username)
            pending_calls.pop(peer, None)

    if peer and notify_peer:
        peer_conn = get_online_conn(peer)
        if peer_conn:
            try:
                send_json(peer_conn, {
                    'type': 'call_end',
                    'from': username,
                    'text': reason_text or '通话已结束',
                })
            except Exception:
                pass
    return peer


def remove_client(username):
    peer = clear_call_state(username, notify_peer=True, reason_text='对方已断开')
    with friends_lock:
        pending_friend_requests.pop(username, None)
        for target in list(pending_friend_requests.keys()):
            pending_friend_requests[target].discard(username)
            if not pending_friend_requests[target]:
                pending_friend_requests.pop(target, None)
    with clients_lock:
        tup = clients.pop(username, None)

    if tup:
        conn, addr = tup
        try:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            conn.close()
        except Exception:
            pass
        _drop_conn_send_lock(conn)
        broadcast({'type': 'info', 'text': f'用户 {username} 已断开'}, exclude_username=None)
        print(f'Removed client {username} {addr}')
        if peer:
            print(f'清理与 {username} 相关的呼叫状态，涉及 {peer}')
            notify_friend_status(peer)
    notify_friend_status(username)


def handle_call_start(conn, current_username, msg):
    to = msg.get('to')
    if not to:
        send_json(conn, {'type': 'error', 'text': "缺少 'to' 字段"})
        return
    if to == current_username:
        send_json(conn, {'type': 'error', 'text': '不能呼叫自己'})
        return

    target_conn = get_online_conn(to)
    if not target_conn:
        send_json(conn, {'type': 'error', 'text': f'用户 {to} 不在线'})
        return

    with call_lock:
        if current_username in active_calls or current_username in pending_calls:
            send_json(conn, {'type': 'error', 'text': '你当前已有进行中的通话或邀请'})
            return
        if to in active_calls or to in pending_calls:
            send_json(conn, {'type': 'error', 'text': f'用户 {to} 正在通话或已有邀请'})
            return
        pending_calls[current_username] = to
        pending_calls[to] = current_username

    try:
        send_json(target_conn, {
            'type': 'call_start',
            'from': current_username,
            'rate': int(msg.get('rate', 16000)),
            'channels': int(msg.get('channels', 1)),
            'sampwidth': int(msg.get('sampwidth', 2)),
        })
        send_json(conn, {'type': 'info', 'text': f'已向 {to} 发起语音通话'})
        notify_friend_status(current_username)
        notify_friend_status(to)
    except Exception:
        with call_lock:
            pending_calls.pop(current_username, None)
            pending_calls.pop(to, None)
        send_json(conn, {'type': 'error', 'text': f'呼叫 {to} 失败'})


def handle_call_accept(conn, current_username, msg):
    peer = msg.get('to')
    if not peer:
        with call_lock:
            peer = pending_calls.get(current_username)
    if not peer:
        send_json(conn, {'type': 'error', 'text': '没有可接听的呼叫'})
        return

    peer_conn = get_online_conn(peer)
    if not peer_conn:
        with call_lock:
            pending_calls.pop(current_username, None)
            pending_calls.pop(peer, None)
        send_json(conn, {'type': 'error', 'text': f'用户 {peer} 不在线'})
        return

    with call_lock:
        # 双向映射必须同时成立，任一方向不一致都视为邀请失效
        if pending_calls.get(current_username) != peer or pending_calls.get(peer) != current_username:
            send_json(conn, {'type': 'error', 'text': '该通话邀请已失效'})
            return
        pending_calls.pop(current_username, None)
        pending_calls.pop(peer, None)
        active_calls[current_username] = peer
        active_calls[peer] = current_username

    try:
        send_json(peer_conn, {'type': 'call_accept', 'from': current_username})
        send_json(conn, {'type': 'info', 'text': f'你已接听 {peer} 的通话'})
        notify_friend_status(current_username)
        notify_friend_status(peer)
    except Exception:
        with call_lock:
            active_calls.pop(current_username, None)
            active_calls.pop(peer, None)
        send_json(conn, {'type': 'error', 'text': f'通知 {peer} 接听失败'})


def handle_call_reject(conn, current_username, msg):
    peer = msg.get('to')
    if not peer:
        with call_lock:
            peer = pending_calls.get(current_username)
    if not peer:
        send_json(conn, {'type': 'error', 'text': '没有可拒绝的呼叫'})
        return

    peer_conn = get_online_conn(peer)
    with call_lock:
        pending_calls.pop(current_username, None)
        pending_calls.pop(peer, None)

    if peer_conn:
        try:
            send_json(peer_conn, {'type': 'call_reject', 'from': current_username})
        except Exception:
            pass
    try:
        send_json(conn, {'type': 'info', 'text': f'已拒绝 {peer} 的通话'})
    except Exception:
        pass
    notify_friend_status(current_username)
    notify_friend_status(peer)


def handle_call_end(conn, current_username, msg):
    to = msg.get('to')
    peer = None
    with call_lock:
        if to and active_calls.get(current_username) == to:
            peer = to
            active_calls.pop(current_username, None)
            active_calls.pop(peer, None)
        elif current_username in active_calls:
            peer = active_calls.pop(current_username)
            active_calls.pop(peer, None)
        elif to and pending_calls.get(current_username) == to:
            peer = to
            pending_calls.pop(current_username, None)
            pending_calls.pop(peer, None)
        elif current_username in pending_calls:
            peer = pending_calls.pop(current_username)
            pending_calls.pop(peer, None)

    if peer:
        peer_conn = get_online_conn(peer)
        if peer_conn:
            try:
                send_json(peer_conn, {'type': 'call_end', 'from': current_username})
            except Exception:
                pass
    try:
        send_json(conn, {'type': 'info', 'text': '通话已结束'})
    except Exception:
        pass
    if peer:
        notify_friend_status(current_username)
        notify_friend_status(peer)


def handle_audio_frame(conn, current_username, msg):
    to = msg.get('to')
    bytes_len = msg.get('bytes_len')
    if not to or not isinstance(bytes_len, int):
        send_json(conn, {'type': 'error', 'text': "audio_frame 需要 'to' 与 'bytes_len' 字段"})
        return
    if bytes_len <= 0 or bytes_len > MAX_MSG:
        send_json(conn, {'type': 'error', 'text': '音频帧大小不合规'})
        return

    audio_bytes = recvall(conn, bytes_len)
    if audio_bytes is None:
        return

    with call_lock:
        active_peer = active_calls.get(current_username)
    if active_peer != to:
        # 通话刚结束时可能还有少量晚到的音频帧，静默丢弃即可，避免挂断后向客户端报错。
        return

    target_conn = get_online_conn(to)
    if not target_conn:
        clear_call_state(current_username, notify_peer=False)
        return

    try:
        header = {
            'type': 'audio_frame',
            'from': current_username,
            'bytes_len': bytes_len,
            'rate': int(msg.get('rate', 16000)),
            'channels': int(msg.get('channels', 1)),
            'sampwidth': int(msg.get('sampwidth', 2)),
        }
        send_json(target_conn, header)
        send_raw(target_conn, audio_bytes)
    except Exception:
        clear_call_state(current_username, notify_peer=True, reason_text='通话中断')
        notify_friend_status(current_username)
        notify_friend_status(to)


def handle_client(conn, addr):
    print('连接来自', addr)
    current_username = None
    try:
        while not shutdown_event.is_set():
            msg = recv_json(conn)
            if msg is None:
                break

            mtype = msg.get('type')

            if mtype == 'register':
                username = msg.get('username', '').strip()
                password = msg.get('password', '')
                if not username:
                    send_json(conn, {'type': 'error', 'text': '用户名不能为空'})
                    continue
                with clients_lock:
                    if username in clients:
                        send_json(conn, {'type': 'error', 'text': '用户名已被占用（在线）'})
                        continue
                    clients[username] = (conn, addr)
                current_username = username
                users[username] = password
                _ensure_user_meta(username)
                users_write()
                send_json(conn, {'type': 'info', 'text': f'注册成功，欢迎 {username}'})
                broadcast({'type': 'info', 'text': f'用户 {username} 已上线'}, exclude_username=username)
                send_friend_list(conn, username)
                print(f'用户注册: {username} from {addr}')
                notify_friend_status(username)

            elif mtype == 'login':
                username = msg.get('username', '').strip()
                password = msg.get('password', '')
                if not username:
                    send_json(conn, {'type': 'error', 'text': '用户名不能为空'})
                    continue
                if username not in users:
                    send_json(conn, {'type': 'error', 'text': '此用户不存在'})
                    continue
                if users.get(username) != password:
                    send_json(conn, {'type': 'error', 'text': '密码错误'})
                    continue
                with clients_lock:
                    if username in clients:
                        send_json(conn, {'type': 'error', 'text': '用户名已在线'})
                        continue
                    clients[username] = (conn, addr)
                current_username = username
                _ensure_user_meta(username)
                send_json(conn, {'type': 'info', 'text': f'登录成功，欢迎 {username}'})
                broadcast({'type': 'info', 'text': f'用户 {username} 已上线'}, exclude_username=username)
                send_friend_list(conn, username)
                deliver_offline_messages(conn, username)
                print(f'用户登录: {username} from {addr}')
                notify_friend_status(username)

            elif mtype == 'friend_list_request':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                send_friend_list(conn, current_username)

            elif mtype == 'friend_request':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                handle_friend_request(conn, current_username, msg)

            elif mtype == 'friend_response':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                handle_friend_response(conn, current_username, msg)

            elif mtype == 'list_request':
                send_json(conn, {'type': 'list_response', 'users': online_users()})

            elif mtype == 'broadcast':
                text = msg.get('text', '')
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                broadcast({'type': 'broadcast', 'from': current_username, 'text': text}, exclude_username=None)

            elif mtype == 'message':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                to = msg.get('to')
                text = msg.get('text', '')
                if not to:
                    send_json(conn, {'type': 'error', 'text': "缺少 'to' 字段"})
                    continue
                if not are_friends(current_username, to):
                    send_json(conn, {'type': 'error', 'text': f'你与 {to} 还不是好友，无法私聊'})
                    continue
                target_conn = get_online_conn(to)
                if not target_conn:
                    with friends_lock:
                        offline_messages.setdefault(to, []).append({'from': current_username, 'text': text})
                    users_write()
                    send_json(conn, {'type': 'info', 'text': f'{to} 当前离线，消息将于其上线后送达'})
                    continue
                try:
                    send_json(target_conn, {'type': 'message', 'from': current_username, 'text': text})
                    send_json(conn, {'type': 'info', 'text': f'已发送给 {to}'})
                except Exception:
                    send_json(conn, {'type': 'error', 'text': f'发送给 {to} 失败'})

            elif mtype == 'audio':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                to = msg.get('to')
                bytes_len = msg.get('bytes_len')
                if not to or not isinstance(bytes_len, int):
                    send_json(conn, {'type': 'error', 'text': "audio 需要 'to' 与 'bytes_len' 字段"})
                    continue
                if not are_friends(current_username, to):
                    send_json(conn, {'type': 'error', 'text': f'你与 {to} 还不是好友，无法私聊语音'})
                    continue
                if bytes_len <= 0 or bytes_len > MAX_MSG:
                    send_json(conn, {'type': 'error', 'text': '音频大小不合规'})
                    continue
                audio_bytes = recvall(conn, bytes_len)
                if audio_bytes is None:
                    break
                target_conn = get_online_conn(to)
                if not target_conn:
                    send_json(conn, {'type': 'error', 'text': f'用户 {to} 不在线，无法发送语音'})
                    continue
                try:
                    header = {
                        'type': 'audio',
                        'from': current_username,
                        'bytes_len': bytes_len,
                        'format': msg.get('format', 'wav'),
                        'channels': msg.get('channels'),
                        'sampwidth': msg.get('sampwidth'),
                        'framerate': msg.get('framerate'),
                    }
                    send_json(target_conn, header)
                    send_raw(target_conn, audio_bytes)
                    send_json(conn, {'type': 'info', 'text': f'已向 {to} 发送语音'})
                except Exception:
                    send_json(conn, {'type': 'error', 'text': f'发送给 {to} 时失败'})

            elif mtype == 'call_start':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                to = msg.get('to')
                if not are_friends(current_username, to):
                    send_json(conn, {'type': 'error', 'text': f'你与 {to} 还不是好友，无法发起通话'})
                    continue
                handle_call_start(conn, current_username, msg)

            elif mtype == 'call_accept':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                handle_call_accept(conn, current_username, msg)

            elif mtype == 'call_reject':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                handle_call_reject(conn, current_username, msg)

            elif mtype == 'call_end':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                handle_call_end(conn, current_username, msg)

            elif mtype == 'audio_frame':
                if not current_username:
                    send_json(conn, {'type': 'error', 'text': '请先注册或登录'})
                    continue
                handle_audio_frame(conn, current_username, msg)

            elif mtype == 'quit':
                send_json(conn, {'type': 'info', 'text': 'bye'})
                break

            else:
                send_json(conn, {'type': 'error', 'text': f'未知消息类型: {mtype}'})
    except Exception as e:
        print('客户端处理出错:', e)
    finally:
        if current_username:
            remove_client(current_username)
        else:
            try:
                conn.close()
            except Exception:
                pass
        print('连接关闭', addr)


def repl():
    print('命令：/list（列在线用户) /quit（退出）')
    while not shutdown_event.is_set():
        try:
            line = input()
        except EOFError:
            break
        if not line:
            continue
        if line.strip() == '/list':
            print('Users:', online_users())
        elif line.strip() == '/quit':
            print('[系统]服务器关闭')
            shutdown_event.set()
            break


def main():
    global users
    users = users_read()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen()
    sock.settimeout(1.0)
    print(f'聊天服务器在 {HOST}:{PORT} 启动')
    print('已加载已注册用户：', list(users.keys()))

    repl_thread = threading.Thread(target=repl, daemon=True)
    repl_thread.start()

    try:
        while not shutdown_event.is_set():
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print('收到 KeyboardInterrupt，服务器关闭')
        shutdown_event.set()
    finally:
        print('开始关闭所有客户端连接...')
        try:
            sock.close()
        except Exception:
            pass

        with clients_lock:
            all_clients = list(clients.items())
            clients.clear()

        for uname, (conn, _) in all_clients:
            try:
                send_json(conn, {'type': 'info', 'text': '服务器即将关闭'})
            except Exception:
                pass
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

        users_write()
        print('服务器已彻底关闭。')


if __name__ == '__main__':
    main()
