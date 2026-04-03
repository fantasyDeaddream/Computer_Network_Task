"""
任务5 控制面核心功能测试（不依赖 GUI 和音频设备）
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time


def send_msg(sock: socket.socket, obj: dict) -> None:
    payload = json.dumps(obj, ensure_ascii=False) + "\n"
    sock.sendall(payload.encode("utf-8"))


def recv_all(sock: socket.socket, timeout: float = 1.0) -> list[dict]:
    sock.settimeout(timeout)
    buffer = ""
    try:
        while True:
            data = sock.recv(4096)
            if not data:
                break
            buffer += data.decode("utf-8", errors="ignore")
    except socket.timeout:
        pass
    return [json.loads(line) for line in buffer.strip().split("\n") if line.strip()]


def main() -> int:
    port = 18883

    from conference_server import ConferenceServer

    server = ConferenceServer(port=port)
    server_thread = threading.Thread(target=server.start)
    server_thread.start()
    time.sleep(0.5)

    results: list[str] = []

    alice = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    bob = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        alice.connect(("localhost", port))
        send_msg(alice, {"type": "login", "username": "alice"})
        time.sleep(0.3)
        messages = recv_all(alice)
        assert messages and messages[0].get("success") is True, f"Alice login failed: {messages}"
        results.append("PASS: Alice login")

        bob.connect(("localhost", port))
        send_msg(bob, {"type": "login", "username": "bob"})
        time.sleep(0.3)
        messages = recv_all(bob)
        assert messages and messages[0].get("success") is True, f"Bob login failed: {messages}"
        results.append("PASS: Bob login")

        send_msg(alice, {"type": "room_create", "creator": "alice", "audio_protocol": "tcp"})
        time.sleep(0.3)
        messages = recv_all(alice)
        create_response = next(
            (
                message
                for message in messages
                if message.get("success") is True and "room_id" in message.get("data", {})
            ),
            None,
        )
        assert create_response is not None, f"Create room failed: {messages}"
        room_data = create_response["data"]
        room_id = room_data["room_id"]
        assert room_data.get("audio_protocol") == "udp", room_data
        assert room_data.get("multicast_group", "").startswith("239.255."), room_data
        assert isinstance(room_data.get("multicast_port"), int) and room_data["multicast_port"] > 0
        results.append(f"PASS: Room created with multicast endpoint ({room_data['multicast_group']}:{room_data['multicast_port']})")

        send_msg(
            alice,
            {
                "type": "room_invite",
                "room_id": room_id,
                "inviter": "alice",
                "target": "bob",
            },
        )
        time.sleep(0.3)

        alice_messages = recv_all(alice)
        assert any(message.get("success") is True for message in alice_messages), alice_messages
        results.append("PASS: Invite sent")

        bob_messages = recv_all(bob)
        invite_notify = next(
            (message for message in bob_messages if message.get("type") == "room_invite_notify"),
            None,
        )
        assert invite_notify is not None, bob_messages
        results.append("PASS: Bob received invite")

        send_msg(bob, {"type": "room_join", "room_id": room_id, "username": "bob"})
        time.sleep(0.3)

        bob_messages = recv_all(bob)
        join_response = next(
            (
                message
                for message in bob_messages
                if message.get("success") is True and message.get("data", {}).get("room_id") == room_id
            ),
            None,
        )
        assert join_response is not None, bob_messages
        join_data = join_response["data"]
        assert join_data.get("multicast_group") == room_data["multicast_group"], join_data
        assert join_data.get("multicast_port") == room_data["multicast_port"], join_data
        results.append("PASS: Bob joined room and received multicast endpoint")

        member_update = any(message.get("type") == "room_member_update" for message in bob_messages)
        results.append(
            f"{'PASS' if member_update else 'INFO'}: Bob member update in join response"
        )

        alice_messages = recv_all(alice)
        alice_update = any(message.get("type") == "room_member_update" for message in alice_messages)
        results.append(
            f"{'PASS' if alice_update else 'INFO'}: Alice member update after Bob joined"
        )

        send_msg(bob, {"type": "room_leave", "room_id": room_id, "username": "bob"})
        time.sleep(0.3)
        bob_messages = recv_all(bob)
        assert any(message.get("success") is True for message in bob_messages), bob_messages
        results.append("PASS: Bob left room")

        send_msg(
            alice,
            {
                "type": "room_invite",
                "room_id": room_id,
                "inviter": "alice",
                "target": "bob",
            },
        )
        time.sleep(0.2)
        recv_all(alice)
        recv_all(bob)

        send_msg(bob, {"type": "room_join", "room_id": room_id, "username": "bob"})
        time.sleep(0.2)
        recv_all(bob)
        recv_all(alice)

        send_msg(alice, {"type": "room_dismiss", "room_id": room_id, "creator": "alice"})
        time.sleep(0.3)
        bob_messages = recv_all(bob)
        dismissed = any(
            message.get("type") == "room_dismissed_notify" for message in bob_messages
        )
        assert dismissed, bob_messages
        results.append("PASS: Room dismissed, Bob notified")
    finally:
        alice.close()
        bob.close()
        time.sleep(0.2)
        server.stop()
        server_thread.join(timeout=2.0)

    print("\n" + "=" * 50)
    print("Test Results:")
    print("=" * 50)
    for result in results:
        print(f"  {result}")
    print("=" * 50)
    print(f"All {len(results)} tests completed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
