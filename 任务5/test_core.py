"""
任务5 核心功能测试（不依赖GUI和音频设备）
"""

import threading
import time
import socket
import json
import sys


def send_msg(sock, obj):
    data = json.dumps(obj, ensure_ascii=False) + "\n"
    sock.sendall(data.encode("utf-8"))


def recv_all(sock, timeout=1.0):
    sock.settimeout(timeout)
    buf = ""
    try:
        while True:
            d = sock.recv(4096)
            if not d:
                break
            buf += d.decode("utf-8", errors="ignore")
    except socket.timeout:
        pass
    return [json.loads(l) for l in buf.strip().split("\n") if l.strip()]


def main():
    # 使用非标准端口避免冲突
    PORT = 18883

    from conference_server import ConferenceServer

    server = ConferenceServer(port=PORT)
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    time.sleep(0.5)

    results = []

    # --- Test 0: E-model audio metadata helpers ---
    from conference_protocol import (
        decode_message,
        decode_udp_audio_packet,
        encode_room_audio_chunk,
        encode_quality_report,
        encode_udp_audio_packet,
    )
    from audio_adaptive import CANONICAL_AUDIO_FORMAT
    from emodel import evaluate_quality

    encoded = encode_room_audio_chunk(
        "r1",
        "alice",
        b"abc",
        seq=7,
        timestamp_ms=123,
        audio_format=CANONICAL_AUDIO_FORMAT.to_payload(),
    )
    mtype, payload = decode_message(encoded)
    assert mtype == "room_audio_chunk"
    assert payload["seq"] == 7 and payload["timestamp_ms"] == 123

    udp_packet = encode_udp_audio_packet(
        "alice",
        b"abc",
        seq=8,
        timestamp_ms=456,
        audio_format=CANONICAL_AUDIO_FORMAT.to_payload(),
    )
    sender, seq, timestamp_ms, raw, audio_format = decode_udp_audio_packet(udp_packet)
    assert (sender, seq, timestamp_ms, raw) == ("alice", 8, 456, b"abc")
    assert audio_format["sample_rate"] == CANONICAL_AUDIO_FORMAT.sample_rate

    quality = evaluate_quality(delay_ms=50, packet_loss_percent=0, jitter_ms=5)
    assert quality.r_factor > 80 and quality.mos > 4.0
    results.append("PASS: E-model helpers")

    # --- Test 1: Login ---
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s1.connect(("localhost", PORT))
    send_msg(s1, {"type": "login", "username": "alice"})
    time.sleep(0.3)
    msgs = recv_all(s1)
    assert len(msgs) >= 1 and msgs[0].get("success") is True, f"Login failed: {msgs}"
    results.append("PASS: Alice login")

    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.connect(("localhost", PORT))
    send_msg(s2, {"type": "login", "username": "bob"})
    time.sleep(0.3)
    msgs = recv_all(s2)
    assert len(msgs) >= 1 and msgs[0].get("success") is True, f"Login failed: {msgs}"
    results.append("PASS: Bob login")

    # --- Test 2: Create Room ---
    send_msg(s1, {"type": "room_create", "creator": "alice"})
    time.sleep(0.3)
    msgs = recv_all(s1)
    create_resp = None
    for m in msgs:
        if m.get("success") is True and "room_id" in m.get("data", {}):
            create_resp = m
            break
    assert create_resp is not None, f"Create room failed: {msgs}"
    room_id = create_resp["data"]["room_id"]
    results.append(f"PASS: Room created (id={room_id})")

    # --- Test 3: Invite ---
    send_msg(
        s1,
        {
            "type": "room_invite",
            "room_id": room_id,
            "inviter": "alice",
            "target": "bob",
        },
    )
    time.sleep(0.3)

    # Alice gets invite success response
    msgs_a = recv_all(s1)
    invite_ok = any(m.get("success") is True for m in msgs_a)
    assert invite_ok, f"Invite response failed: {msgs_a}"
    results.append("PASS: Invite sent")

    # Bob gets invite notification
    msgs_b = recv_all(s2)
    invite_notify = any(m.get("type") == "room_invite_notify" for m in msgs_b)
    assert invite_notify, f"Invite notify not received: {msgs_b}"
    results.append("PASS: Bob received invite")

    # --- Test 4: Join Room ---
    send_msg(s2, {"type": "room_join", "room_id": room_id, "username": "bob"})
    time.sleep(0.3)

    msgs_b = recv_all(s2)
    join_ok = any(m.get("success") is True for m in msgs_b)
    assert join_ok, f"Join failed: {msgs_b}"
    results.append("PASS: Bob joined room")

    # Check member update received
    member_update = any(m.get("type") == "room_member_update" for m in msgs_b)
    results.append(
        f"{'PASS' if member_update else 'INFO'}: Bob member update in join response"
    )

    # Alice should also get member update
    msgs_a = recv_all(s1)
    alice_update = any(m.get("type") == "room_member_update" for m in msgs_a)
    results.append(
        f"{'PASS' if alice_update else 'INFO'}: Alice member update after Bob joined"
    )

    # --- Test 5: Adaptive downstream audio ---
    send_msg(
        s2,
        json.loads(
            encode_quality_report(
                room_id,
                "bob",
                delay_ms=420,
                jitter_ms=120,
                packet_loss_percent=9.0,
                sample_count=1,
            )
        ),
    )
    time.sleep(0.2)

    adaptive_audio = bytes([64, 192]) * CANONICAL_AUDIO_FORMAT.chunk_size
    send_msg(
        s1,
        json.loads(
            encode_room_audio_chunk(
                room_id,
                "alice",
                adaptive_audio,
                seq=11,
                timestamp_ms=789,
                audio_format=CANONICAL_AUDIO_FORMAT.to_payload(),
            )
        ),
    )
    time.sleep(0.4)

    msgs_b = recv_all(s2)
    adaptive_chunks = [
        m for m in msgs_b if m.get("type") == "room_audio_chunk" and m.get("sender") == "alice"
    ]
    assert adaptive_chunks, f"Adaptive room audio not received: {msgs_b}"
    adaptive_format = adaptive_chunks[0].get("audio_format", {})
    assert adaptive_format.get("sample_rate") == 8000, adaptive_chunks[0]
    assert adaptive_format.get("channels") == 1, adaptive_chunks[0]
    assert adaptive_format.get("sample_width") == 1, adaptive_chunks[0]
    assert adaptive_format.get("chunk_size") == 256, adaptive_chunks[0]
    assert adaptive_chunks[0].get("profile") == "resilient", adaptive_chunks[0]
    results.append("PASS: Adaptive downstream audio profile applied")

    # --- Test 6: Leave Room ---
    send_msg(s2, {"type": "room_leave", "room_id": room_id, "username": "bob"})
    time.sleep(0.3)
    msgs_b = recv_all(s2)
    leave_ok = any(m.get("success") is True for m in msgs_b)
    assert leave_ok, f"Leave failed: {msgs_b}"
    results.append("PASS: Bob left room")

    # --- Test 7: Dismiss Room ---
    # Re-invite and join Bob
    send_msg(
        s1,
        {
            "type": "room_invite",
            "room_id": room_id,
            "inviter": "alice",
            "target": "bob",
        },
    )
    time.sleep(0.2)
    recv_all(s1)
    recv_all(s2)

    send_msg(s2, {"type": "room_join", "room_id": room_id, "username": "bob"})
    time.sleep(0.2)
    recv_all(s2)
    recv_all(s1)

    send_msg(s1, {"type": "room_dismiss", "room_id": room_id, "creator": "alice"})
    time.sleep(0.3)

    msgs_b = recv_all(s2)
    dismissed = any(m.get("type") == "room_dismissed_notify" for m in msgs_b)
    assert dismissed, f"Dismiss notify not received: {msgs_b}"
    results.append("PASS: Room dismissed, Bob notified")

    # Cleanup
    server.stop()
    s1.close()
    s2.close()

    print("\n" + "=" * 50)
    print("Test Results:")
    print("=" * 50)
    for r in results:
        print(f"  {r}")
    print("=" * 50)
    print(f"All {len(results)} tests completed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
