#!/usr/bin/env python3
# merged dual-mode chat server (TCP relay + P2P signaling + group voice relay)

import ipaddress
import json
import os
import pickle
import socket
import struct
import threading

HOST = "10.192.22.43"
PORT = 65432
GROUP_VOICE_PORT = 65433
MAX_MSG = 100 * 1024 * 1024
USERS_FILE = "users.pkl"

clients = {}  # username -> (conn, addr)
client_net_meta = {}  # username -> {"p2p_ip": str, "p2p_port": int}
clients_lock = threading.Lock()

users = {}
friends_map = {}
offline_messages = {}
pending_friend_requests = {}

pending_calls = {}  # username -> peer
active_calls = {}  # username -> peer
call_mode_map = {}  # frozenset({u1,u2}) -> "tcp" | "p2p"
call_lock = threading.Lock()

friends_lock = threading.RLock()

# Group voice call state (TCP relay on dedicated port)
group_call_lock = threading.Lock()
group_call_state = {
    "active": False,
    "initiator": None,
    "participants": set(),
    "invited": set(),
}
group_voice_clients = {}  # username -> group voice tcp conn

conn_send_locks = {}
conn_send_locks_lock = threading.Lock()
shutdown_event = threading.Event()


def users_write():
    try:
        with friends_lock:
            payload = {
                "users": users,
                "friends": {u: sorted(list(v)) for u, v in friends_map.items()},
                "offline_messages": offline_messages,
            }
        with open(USERS_FILE, "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        print("保存 users 失败:", e)


def users_read():
    global friends_map
    global offline_messages
    if not os.path.exists(USERS_FILE):
        friends_map = {}
        offline_messages = {}
        return {}
    try:
        with open(USERS_FILE, "rb") as f:
            obj = pickle.load(f)
            if not isinstance(obj, dict):
                friends_map = {}
                offline_messages = {}
                return {}

            if isinstance(obj.get("users"), dict):
                loaded_users = obj.get("users", {})
                raw_friends = obj.get("friends", {})
                raw_offline = obj.get("offline_messages", {})
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

            friends_map = {u: set() for u in obj.keys()}
            offline_messages = {u: [] for u in obj.keys()}
            return obj
    except Exception as e:
        print("读取 users 失败:", e)
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


def pair_key(user_a, user_b):
    return frozenset((user_a, user_b))


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
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    length = struct.pack(">I", len(data))
    lock = _get_conn_send_lock(conn)
    with lock:
        conn.sendall(length + data)


def send_raw(conn, data):
    lock = _get_conn_send_lock(conn)
    with lock:
        conn.sendall(data)


def recvall(conn, n):
    buf = b""
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
    msg_len = struct.unpack(">I", raw_len)[0]
    data = recvall(conn, msg_len)
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


def get_online_conn(username):
    with clients_lock:
        target = clients.get(username)
    return target[0] if target else None


def online_users():
    with clients_lock:
        return list(clients.keys())


def update_client_net_meta(username, msg, addr):
    p2p_ip = str(msg.get("p2p_ip") or "").strip()
    p2p_port = msg.get("p2p_port")
    if not p2p_ip:
        p2p_ip = addr[0]

    port_val = 0
    try:
        port_val = int(p2p_port)
    except Exception:
        port_val = 0

    with clients_lock:
        client_net_meta[username] = {
            "p2p_ip": p2p_ip,
            "p2p_port": port_val,
        }


def get_client_net_meta(username):
    with clients_lock:
        return dict(client_net_meta.get(username, {}))


def same_subnet_ipv4(ip_a, ip_b, prefix=24):
    try:
        a = ipaddress.ip_address(ip_a)
        b = ipaddress.ip_address(ip_b)
        if a.version != 4 or b.version != 4:
            return False
        net_a = ipaddress.ip_network(f"{ip_a}/{prefix}", strict=False)
        net_b = ipaddress.ip_network(f"{ip_b}/{prefix}", strict=False)
        return net_a.network_address == net_b.network_address
    except Exception:
        return False


def resolve_call_mode(caller, callee, requested_mode):
    mode = str(requested_mode or "tcp").strip().lower()
    if mode not in ("tcp", "p2p", "auto"):
        mode = "tcp"

    c_meta = get_client_net_meta(caller)
    t_meta = get_client_net_meta(callee)
    c_ip = c_meta.get("p2p_ip")
    c_port = int(c_meta.get("p2p_port", 0) or 0)
    t_ip = t_meta.get("p2p_ip")
    t_port = int(t_meta.get("p2p_port", 0) or 0)

    p2p_available = bool(c_ip and t_ip and c_port > 0 and t_port > 0)

    if mode == "auto":
        if p2p_available and same_subnet_ipv4(c_ip, t_ip):
            mode = "p2p"
        else:
            mode = "tcp"

    if mode == "p2p" and not p2p_available:
        return None, "P2P 信息不完整，无法建立直连（请检查双方 P2P 监听端口）"

    return mode, ""


def user_status(username):
    with call_lock:
        in_call = (username in active_calls) or (username in pending_calls)
    if in_call:
        return "calling"
    return "online_free" if get_online_conn(username) else "not_online"


def send_friend_list(conn, username):
    with friends_lock:
        friends = sorted(list(friends_map.get(username, set())))
    payload = [{"username": f, "status": user_status(f)} for f in friends]
    send_json(conn, {"type": "friend_list_response", "friends": payload})


def notify_friend_status(username):
    status = user_status(username)
    with friends_lock:
        friends = list(friends_map.get(username, set()))
    for f in friends:
        conn = get_online_conn(f)
        if not conn:
            continue
        try:
            send_json(conn, {"type": "friend_status", "friend": username, "status": status})
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
                "type": "message",
                "from": msg.get("from", "?"),
                "text": msg.get("text", ""),
                "offline": True,
            })
        except Exception:
            break


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
            print(f"广播给 {uname} 失败，移除客户端")
            remove_client(uname)


def handle_friend_request(conn, current_username, msg):
    to = (msg.get("to") or "").strip()
    if not to:
        send_json(conn, {"type": "error", "text": "缺少 'to' 字段"})
        return
    if to == current_username:
        send_json(conn, {"type": "error", "text": "不能添加自己为好友"})
        return
    if to not in users:
        send_json(conn, {"type": "error", "text": f"用户 {to} 不存在"})
        return
    if are_friends(current_username, to):
        send_json(conn, {"type": "info", "text": f"{to} 已是你的好友"})
        return

    target_conn = get_online_conn(to)
    if not target_conn:
        send_json(conn, {"type": "error", "text": f"用户 {to} 不在线，无法发送好友请求"})
        return

    with friends_lock:
        reqs = pending_friend_requests.setdefault(to, set())
        if current_username in reqs:
            send_json(conn, {"type": "info", "text": f"你已经向 {to} 发送过好友请求"})
            return
        reqs.add(current_username)

    try:
        send_json(target_conn, {"type": "friend_request", "from": current_username})
        send_json(conn, {"type": "info", "text": f"已向 {to} 发送好友请求"})
    except Exception:
        with friends_lock:
            pending_friend_requests.get(to, set()).discard(current_username)
            if not pending_friend_requests.get(to):
                pending_friend_requests.pop(to, None)
        send_json(conn, {"type": "error", "text": f"发送好友请求给 {to} 失败"})


def handle_friend_response(conn, current_username, msg):
    requester = (msg.get("to") or "").strip()
    accept = bool(msg.get("accept"))
    if not requester:
        send_json(conn, {"type": "error", "text": "缺少 'to' 字段"})
        return

    with friends_lock:
        reqs = pending_friend_requests.get(current_username, set())
        if requester not in reqs:
            send_json(conn, {"type": "error", "text": "该好友请求已失效"})
            return
        reqs.discard(requester)
        if not reqs:
            pending_friend_requests.pop(current_username, None)

    requester_conn = get_online_conn(requester)

    if not accept:
        if requester_conn:
            try:
                send_json(requester_conn, {"type": "friend_rejected", "from": current_username})
            except Exception:
                pass
        send_json(conn, {"type": "info", "text": f"已拒绝 {requester} 的好友请求"})
        return

    with friends_lock:
        friends_map.setdefault(current_username, set()).add(requester)
        friends_map.setdefault(requester, set()).add(current_username)
    users_write()

    my_conn = get_online_conn(current_username)
    if my_conn:
        try:
            send_json(my_conn, {
                "type": "friend_added",
                "friend": requester,
                "status": user_status(requester),
            })
            send_friend_list(my_conn, current_username)
        except Exception:
            pass

    if requester_conn:
        try:
            send_json(requester_conn, {
                "type": "friend_added",
                "friend": current_username,
                "status": user_status(current_username),
            })
            send_friend_list(requester_conn, requester)
        except Exception:
            pass

    notify_friend_status(current_username)
    notify_friend_status(requester)
    send_json(conn, {"type": "info", "text": f"你与 {requester} 已成为好友"})


def clear_call_state(username, notify_peer=True, reason_text=None):
    peer = None
    mode = "tcp"
    with call_lock:
        if username in active_calls:
            peer = active_calls.pop(username)
            active_calls.pop(peer, None)
        elif username in pending_calls:
            peer = pending_calls.pop(username)
            pending_calls.pop(peer, None)

        if peer:
            mode = call_mode_map.pop(pair_key(username, peer), "tcp")

    if peer and notify_peer:
        peer_conn = get_online_conn(peer)
        if peer_conn:
            try:
                send_json(peer_conn, {
                    "type": "call_end",
                    "from": username,
                    "mode": mode,
                    "text": reason_text or "通话已结束",
                })
            except Exception:
                pass
    return peer


def notify_group_invite(initiator, targets):
    for uname in targets:
        target_conn = get_online_conn(uname)
        if not target_conn:
            continue
        try:
            send_json(target_conn, {
                "type": "group_call_invite",
                "from": initiator,
                "port": GROUP_VOICE_PORT,
            })
        except Exception:
            pass


def close_group_voice_conn(username):
    conn = None
    with group_call_lock:
        conn = group_voice_clients.pop(username, None)
    if conn:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        _drop_conn_send_lock(conn)


def reset_group_call(notify=True, reason="组播语音已结束"):
    with group_call_lock:
        if not group_call_state["active"]:
            return
        initiator = group_call_state["initiator"]
        participants = set(group_call_state["participants"])
        group_call_state["active"] = False
        group_call_state["initiator"] = None
        group_call_state["participants"].clear()
        group_call_state["invited"].clear()

    if notify:
        for uname in participants:
            if uname == initiator:
                continue
            conn = get_online_conn(uname)
            if conn:
                try:
                    send_json(conn, {
                        "type": "group_call_end",
                        "from": initiator or "SYSTEM",
                        "text": reason,
                    })
                except Exception:
                    pass

    for uname in list(group_voice_clients.keys()):
        close_group_voice_conn(uname)


def handle_group_call_start(conn, current_username):
    with group_call_lock:
        if group_call_state["active"]:
            send_json(conn, {"type": "error", "text": "已有组播语音通话在进行中"})
            return
        group_call_state["active"] = True
        group_call_state["initiator"] = current_username
        group_call_state["participants"] = {current_username}
        invited = set(online_users()) - {current_username}
        group_call_state["invited"] = set(invited)

    notify_group_invite(current_username, invited)
    send_json(conn, {
        "type": "info",
        "text": f"已发起组播语音邀请，监听端口 {GROUP_VOICE_PORT}",
    })


def handle_group_call_response(conn, current_username, msg):
    join = bool(msg.get("join"))
    with group_call_lock:
        if not group_call_state["active"]:
            send_json(conn, {"type": "error", "text": "当前没有组播语音邀请"})
            return
        if current_username == group_call_state["initiator"]:
            send_json(conn, {"type": "error", "text": "发起者无需响应邀请"})
            return
        if current_username not in group_call_state["invited"]:
            send_json(conn, {"type": "error", "text": "你未被邀请或邀请已失效"})
            return

        initiator = group_call_state["initiator"]
        group_call_state["invited"].discard(current_username)
        if join:
            group_call_state["participants"].add(current_username)

    if join:
        send_json(conn, {
            "type": "group_call_join_ok",
            "port": GROUP_VOICE_PORT,
            "initiator": initiator,
        })
        ini_conn = get_online_conn(initiator)
        if ini_conn:
            try:
                send_json(ini_conn, {"type": "info", "text": f"{current_username} 已加入组播语音"})
            except Exception:
                pass
    else:
        send_json(conn, {"type": "info", "text": "你已拒绝本次组播语音邀请"})


def handle_group_call_end(conn, current_username):
    with group_call_lock:
        if not group_call_state["active"]:
            send_json(conn, {"type": "error", "text": "当前没有组播语音通话"})
            return
        if group_call_state["initiator"] != current_username:
            send_json(conn, {"type": "error", "text": "仅发起者可结束组播语音"})
            return
    reset_group_call(notify=True, reason="发起者结束了组播语音")
    send_json(conn, {"type": "info", "text": "你已结束组播语音"})


def relay_group_voice_frame(sender, msg, raw_bytes):
    with group_call_lock:
        if not group_call_state["active"]:
            return
        if sender not in group_call_state["participants"]:
            return
        recipients = list(group_call_state["participants"] - {sender})

    header = {
        "type": "group_voice_frame",
        "from": sender,
        "bytes_len": len(raw_bytes),
        "rate": int(msg.get("rate", 16000)),
        "channels": int(msg.get("channels", 1)),
        "sampwidth": int(msg.get("sampwidth", 2)),
    }

    for uname in recipients:
        with group_call_lock:
            target_conn = group_voice_clients.get(uname)
        if not target_conn:
            continue
        try:
            send_json(target_conn, header)
            send_raw(target_conn, raw_bytes)
        except Exception:
            close_group_voice_conn(uname)


def handle_group_voice_client(conn, addr):
    username = None
    try:
        auth = recv_json(conn)
        if not auth:
            return
        if auth.get("type") != "group_auth":
            return

        username = str(auth.get("username") or "").strip()
        if not username:
            return

        with group_call_lock:
            active = group_call_state["active"]
            allowed = username in group_call_state["participants"]
        if not active or not allowed:
            send_json(conn, {"type": "error", "text": "你当前不在组播语音参与名单中"})
            return

        old_conn = None
        with group_call_lock:
            old_conn = group_voice_clients.get(username)
            group_voice_clients[username] = conn

        if old_conn and old_conn is not conn:
            try:
                old_conn.close()
            except Exception:
                pass
            _drop_conn_send_lock(old_conn)

        send_json(conn, {"type": "group_auth_ok", "text": "组播语音连接成功"})

        while not shutdown_event.is_set():
            msg = recv_json(conn)
            if msg is None:
                break
            if msg.get("type") != "group_voice_frame":
                continue

            bytes_len = msg.get("bytes_len")
            if not isinstance(bytes_len, int) or bytes_len <= 0 or bytes_len > MAX_MSG:
                continue
            raw = recvall(conn, bytes_len)
            if raw is None:
                break
            relay_group_voice_frame(username, msg, raw)
    except Exception as e:
        print("组播语音连接异常:", addr, e)
    finally:
        if username:
            close_group_voice_conn(username)
        else:
            try:
                conn.close()
            except Exception:
                pass


def group_voice_accept_loop(sock):
    while not shutdown_event.is_set():
        try:
            conn, addr = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=handle_group_voice_client, args=(conn, addr), daemon=True).start()


def handle_call_start(conn, current_username, msg):
    to = str(msg.get("to") or "").strip()
    if not to:
        send_json(conn, {"type": "error", "text": "缺少 'to' 字段"})
        return
    if to == current_username:
        send_json(conn, {"type": "error", "text": "不能呼叫自己"})
        return

    target_conn = get_online_conn(to)
    if not target_conn:
        send_json(conn, {"type": "error", "text": f"用户 {to} 不在线"})
        return

    mode, err = resolve_call_mode(current_username, to, msg.get("mode", "tcp"))
    if not mode:
        send_json(conn, {"type": "error", "text": err})
        return

    with call_lock:
        if current_username in active_calls or current_username in pending_calls:
            send_json(conn, {"type": "error", "text": "你当前已有进行中的通话或邀请"})
            return
        if to in active_calls or to in pending_calls:
            send_json(conn, {"type": "error", "text": f"用户 {to} 正在通话或已有邀请"})
            return
        pending_calls[current_username] = to
        pending_calls[to] = current_username
        call_mode_map[pair_key(current_username, to)] = mode

    cmeta = get_client_net_meta(current_username)
    try:
        send_json(target_conn, {
            "type": "call_start",
            "from": current_username,
            "mode": mode,
            "rate": int(msg.get("rate", 16000)),
            "channels": int(msg.get("channels", 1)),
            "sampwidth": int(msg.get("sampwidth", 2)),
            "p2p_peer_ip": cmeta.get("p2p_ip", ""),
            "p2p_peer_port": int(cmeta.get("p2p_port", 0) or 0),
        })
        send_json(conn, {"type": "info", "text": f"已向 {to} 发起语音通话（{mode}）"})
        notify_friend_status(current_username)
        notify_friend_status(to)
    except Exception:
        with call_lock:
            pending_calls.pop(current_username, None)
            pending_calls.pop(to, None)
            call_mode_map.pop(pair_key(current_username, to), None)
        send_json(conn, {"type": "error", "text": f"呼叫 {to} 失败"})


def handle_call_accept(conn, current_username, msg):
    peer = msg.get("to")
    if not peer:
        with call_lock:
            peer = pending_calls.get(current_username)
    if not peer:
        send_json(conn, {"type": "error", "text": "没有可接听的呼叫"})
        return

    peer_conn = get_online_conn(peer)
    if not peer_conn:
        with call_lock:
            pending_calls.pop(current_username, None)
            pending_calls.pop(peer, None)
            call_mode_map.pop(pair_key(current_username, peer), None)
        send_json(conn, {"type": "error", "text": f"用户 {peer} 不在线"})
        return

    with call_lock:
        if pending_calls.get(current_username) != peer or pending_calls.get(peer) != current_username:
            send_json(conn, {"type": "error", "text": "该通话邀请已失效"})
            return
        pending_calls.pop(current_username, None)
        pending_calls.pop(peer, None)
        active_calls[current_username] = peer
        active_calls[peer] = current_username
        mode = call_mode_map.get(pair_key(current_username, peer), "tcp")

    my_meta = get_client_net_meta(current_username)
    try:
        send_json(peer_conn, {
            "type": "call_accept",
            "from": current_username,
            "mode": mode,
            "p2p_peer_ip": my_meta.get("p2p_ip", ""),
            "p2p_peer_port": int(my_meta.get("p2p_port", 0) or 0),
        })
        send_json(conn, {"type": "info", "text": f"你已接听 {peer} 的通话（{mode}）"})
        notify_friend_status(current_username)
        notify_friend_status(peer)
    except Exception:
        with call_lock:
            active_calls.pop(current_username, None)
            active_calls.pop(peer, None)
            call_mode_map.pop(pair_key(current_username, peer), None)
        send_json(conn, {"type": "error", "text": f"通知 {peer} 接听失败"})


def handle_call_reject(conn, current_username, msg):
    peer = msg.get("to")
    if not peer:
        with call_lock:
            peer = pending_calls.get(current_username)
    if not peer:
        send_json(conn, {"type": "error", "text": "没有可拒绝的呼叫"})
        return

    mode = "tcp"
    peer_conn = get_online_conn(peer)
    with call_lock:
        pending_calls.pop(current_username, None)
        pending_calls.pop(peer, None)
        mode = call_mode_map.pop(pair_key(current_username, peer), "tcp")

    if peer_conn:
        try:
            send_json(peer_conn, {"type": "call_reject", "from": current_username, "mode": mode})
        except Exception:
            pass
    try:
        send_json(conn, {"type": "info", "text": f"已拒绝 {peer} 的通话"})
    except Exception:
        pass
    notify_friend_status(current_username)
    notify_friend_status(peer)


def handle_call_end(conn, current_username, msg):
    to = msg.get("to")
    peer = None
    mode = "tcp"

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
            mode = call_mode_map.pop(pair_key(current_username, peer), "tcp")

    if peer:
        peer_conn = get_online_conn(peer)
        if peer_conn:
            try:
                send_json(peer_conn, {"type": "call_end", "from": current_username, "mode": mode})
            except Exception:
                pass
    try:
        send_json(conn, {"type": "info", "text": "通话已结束"})
    except Exception:
        pass

    if peer:
        notify_friend_status(current_username)
        notify_friend_status(peer)


def handle_audio_frame(conn, current_username, msg):
    to = msg.get("to")
    bytes_len = msg.get("bytes_len")
    if not to or not isinstance(bytes_len, int):
        send_json(conn, {"type": "error", "text": "audio_frame 需要 'to' 与 'bytes_len' 字段"})
        return
    if bytes_len <= 0 or bytes_len > MAX_MSG:
        send_json(conn, {"type": "error", "text": "音频帧大小不合规"})
        return

    audio_bytes = recvall(conn, bytes_len)
    if audio_bytes is None:
        return

    with call_lock:
        active_peer = active_calls.get(current_username)
        mode = call_mode_map.get(pair_key(current_username, to), "tcp")

    if active_peer != to:
        return
    if mode != "tcp":
        return

    target_conn = get_online_conn(to)
    if not target_conn:
        clear_call_state(current_username, notify_peer=False)
        return

    try:
        header = {
            "type": "audio_frame",
            "from": current_username,
            "bytes_len": bytes_len,
            "rate": int(msg.get("rate", 16000)),
            "channels": int(msg.get("channels", 1)),
            "sampwidth": int(msg.get("sampwidth", 2)),
        }
        send_json(target_conn, header)
        send_raw(target_conn, audio_bytes)
    except Exception:
        clear_call_state(current_username, notify_peer=True, reason_text="通话中断")
        notify_friend_status(current_username)
        notify_friend_status(to)


def remove_client(username):
    peer = clear_call_state(username, notify_peer=True, reason_text="对方已断开")

    with group_call_lock:
        if group_call_state["active"]:
            if username == group_call_state["initiator"]:
                # 发起者掉线，直接结束
                pass
            else:
                group_call_state["participants"].discard(username)
                group_call_state["invited"].discard(username)

    if group_call_state["active"] and group_call_state["initiator"] == username:
        reset_group_call(notify=True, reason="发起者离线，组播语音结束")
    close_group_voice_conn(username)

    with friends_lock:
        pending_friend_requests.pop(username, None)
        for target in list(pending_friend_requests.keys()):
            pending_friend_requests[target].discard(username)
            if not pending_friend_requests[target]:
                pending_friend_requests.pop(target, None)

    with clients_lock:
        tup = clients.pop(username, None)
        client_net_meta.pop(username, None)

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
        broadcast({"type": "info", "text": f"用户 {username} 已断开"}, exclude_username=None)
        print(f"Removed client {username} {addr}")
        if peer:
            print(f"清理与 {username} 相关的呼叫状态，涉及 {peer}")
            notify_friend_status(peer)
    notify_friend_status(username)


def handle_client(conn, addr):
    print("连接来自", addr)
    current_username = None
    try:
        while not shutdown_event.is_set():
            msg = recv_json(conn)
            if msg is None:
                break

            mtype = msg.get("type")

            if mtype == "register":
                username = str(msg.get("username", "")).strip()
                password = msg.get("password", "")
                if not username:
                    send_json(conn, {"type": "error", "text": "用户名不能为空"})
                    continue
                with clients_lock:
                    if username in clients:
                        send_json(conn, {"type": "error", "text": "用户名已被占用（在线）"})
                        continue
                    clients[username] = (conn, addr)
                current_username = username
                users[username] = password
                _ensure_user_meta(username)
                update_client_net_meta(username, msg, addr)
                users_write()
                send_json(conn, {"type": "info", "text": f"注册成功，欢迎 {username}"})
                broadcast({"type": "info", "text": f"用户 {username} 已上线"}, exclude_username=username)
                send_friend_list(conn, username)
                print(f"用户注册: {username} from {addr}")
                notify_friend_status(username)

            elif mtype == "login":
                username = str(msg.get("username", "")).strip()
                password = msg.get("password", "")
                if not username:
                    send_json(conn, {"type": "error", "text": "用户名不能为空"})
                    continue
                if username not in users:
                    send_json(conn, {"type": "error", "text": "此用户不存在"})
                    continue
                if users.get(username) != password:
                    send_json(conn, {"type": "error", "text": "密码错误"})
                    continue
                with clients_lock:
                    if username in clients:
                        send_json(conn, {"type": "error", "text": "用户名已在线"})
                        continue
                    clients[username] = (conn, addr)
                current_username = username
                _ensure_user_meta(username)
                update_client_net_meta(username, msg, addr)
                send_json(conn, {"type": "info", "text": f"登录成功，欢迎 {username}"})
                broadcast({"type": "info", "text": f"用户 {username} 已上线"}, exclude_username=username)
                send_friend_list(conn, username)
                deliver_offline_messages(conn, username)
                print(f"用户登录: {username} from {addr}")
                notify_friend_status(username)

            elif mtype == "friend_list_request":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                send_friend_list(conn, current_username)

            elif mtype == "friend_request":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                handle_friend_request(conn, current_username, msg)

            elif mtype == "friend_response":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                handle_friend_response(conn, current_username, msg)

            elif mtype == "list_request":
                send_json(conn, {"type": "list_response", "users": online_users()})

            elif mtype == "broadcast":
                text = msg.get("text", "")
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                broadcast({"type": "broadcast", "from": current_username, "text": text}, exclude_username=None)

            elif mtype == "message":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                to = msg.get("to")
                text = msg.get("text", "")
                if not to:
                    send_json(conn, {"type": "error", "text": "缺少 'to' 字段"})
                    continue
                if not are_friends(current_username, to):
                    send_json(conn, {"type": "error", "text": f"你与 {to} 还不是好友，无法私聊"})
                    continue
                target_conn = get_online_conn(to)
                if not target_conn:
                    with friends_lock:
                        offline_messages.setdefault(to, []).append({"from": current_username, "text": text})
                    users_write()
                    send_json(conn, {"type": "info", "text": f"{to} 当前离线，消息将于其上线后送达"})
                    continue
                try:
                    send_json(target_conn, {"type": "message", "from": current_username, "text": text})
                    send_json(conn, {"type": "info", "text": f"已发送给 {to}"})
                except Exception:
                    send_json(conn, {"type": "error", "text": f"发送给 {to} 失败"})

            elif mtype == "audio":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                to = msg.get("to")
                bytes_len = msg.get("bytes_len")
                if not to or not isinstance(bytes_len, int):
                    send_json(conn, {"type": "error", "text": "audio 需要 'to' 与 'bytes_len' 字段"})
                    continue
                if not are_friends(current_username, to):
                    send_json(conn, {"type": "error", "text": f"你与 {to} 还不是好友，无法私聊语音"})
                    continue
                if bytes_len <= 0 or bytes_len > MAX_MSG:
                    send_json(conn, {"type": "error", "text": "音频大小不合规"})
                    continue
                audio_bytes = recvall(conn, bytes_len)
                if audio_bytes is None:
                    break
                target_conn = get_online_conn(to)
                if not target_conn:
                    send_json(conn, {"type": "error", "text": f"用户 {to} 不在线，无法发送语音"})
                    continue
                try:
                    header = {
                        "type": "audio",
                        "from": current_username,
                        "bytes_len": bytes_len,
                        "format": msg.get("format", "wav"),
                        "channels": msg.get("channels"),
                        "sampwidth": msg.get("sampwidth"),
                        "framerate": msg.get("framerate"),
                    }
                    send_json(target_conn, header)
                    send_raw(target_conn, audio_bytes)
                    send_json(conn, {"type": "info", "text": f"已向 {to} 发送语音"})
                except Exception:
                    send_json(conn, {"type": "error", "text": f"发送给 {to} 时失败"})

            elif mtype == "call_start":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                to = msg.get("to")
                if not are_friends(current_username, to):
                    send_json(conn, {"type": "error", "text": f"你与 {to} 还不是好友，无法发起通话"})
                    continue
                handle_call_start(conn, current_username, msg)

            elif mtype == "call_accept":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                handle_call_accept(conn, current_username, msg)

            elif mtype == "call_reject":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                handle_call_reject(conn, current_username, msg)

            elif mtype == "call_end":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                handle_call_end(conn, current_username, msg)

            elif mtype == "audio_frame":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                handle_audio_frame(conn, current_username, msg)

            elif mtype == "group_call_start":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                handle_group_call_start(conn, current_username)

            elif mtype == "group_call_response":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                handle_group_call_response(conn, current_username, msg)

            elif mtype == "group_call_end":
                if not current_username:
                    send_json(conn, {"type": "error", "text": "请先注册或登录"})
                    continue
                handle_group_call_end(conn, current_username)

            elif mtype == "quit":
                send_json(conn, {"type": "info", "text": "bye"})
                break

            else:
                send_json(conn, {"type": "error", "text": f"未知消息类型: {mtype}"})
    except Exception as e:
        print("客户端处理出错:", e)
    finally:
        if current_username:
            remove_client(current_username)
        else:
            try:
                conn.close()
            except Exception:
                pass
        print("连接关闭", addr)


def repl():
    print("命令：/list（列在线用户) /quit（退出）")
    while not shutdown_event.is_set():
        try:
            line = input()
        except EOFError:
            break
        if not line:
            continue
        if line.strip() == "/list":
            print("Users:", online_users())
        elif line.strip() == "/quit":
            print("[系统]服务器关闭")
            shutdown_event.set()
            break


def main():
    global users
    users = users_read()

    main_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    main_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    main_sock.bind((HOST, PORT))
    main_sock.listen()
    main_sock.settimeout(1.0)

    group_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    group_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    group_sock.bind((HOST, GROUP_VOICE_PORT))
    group_sock.listen()
    group_sock.settimeout(1.0)

    print(f"聊天服务器在 {HOST}:{PORT} 启动")
    print(f"组播语音中转端口: {HOST}:{GROUP_VOICE_PORT}")
    print("已加载已注册用户：", list(users.keys()))

    repl_thread = threading.Thread(target=repl, daemon=True)
    repl_thread.start()

    group_thread = threading.Thread(target=group_voice_accept_loop, args=(group_sock,), daemon=True)
    group_thread.start()

    try:
        while not shutdown_event.is_set():
            try:
                conn, addr = main_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("收到 KeyboardInterrupt，服务器关闭")
        shutdown_event.set()
    finally:
        print("开始关闭所有客户端连接...")
        try:
            main_sock.close()
        except Exception:
            pass
        try:
            group_sock.close()
        except Exception:
            pass

        reset_group_call(notify=True, reason="服务器关闭，组播语音结束")

        with clients_lock:
            all_clients = list(clients.items())
            clients.clear()
            client_net_meta.clear()

        for _uname, (conn, _) in all_clients:
            try:
                send_json(conn, {"type": "info", "text": "服务器即将关闭"})
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
            _drop_conn_send_lock(conn)

        users_write()
        print("服务器已彻底关闭。")


if __name__ == "__main__":
    main()
