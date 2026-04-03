"""
任务5 音频传输层测试：
- 组播端点分配
- 冗余音频包编解码
- 抖动缓冲的丢包恢复与插值补偿
"""

from __future__ import annotations

from array import array
import sys

from multicast_audio import (
    RedundantJitterBuffer,
    allocate_multicast_endpoint,
    pack_audio_packet,
    unpack_audio_packet,
)


def samples_to_bytes(values: list[int]) -> bytes:
    pcm = array("h", values)
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def bytes_to_samples(raw: bytes) -> list[int]:
    pcm = array("h")
    pcm.frombytes(raw)
    if sys.byteorder != "little":
        pcm.byteswap()
    return list(pcm)


def main() -> int:
    results: list[str] = []

    endpoint = allocate_multicast_endpoint({("239.255.1.1", 20001)})
    assert endpoint != ("239.255.1.1", 20001)
    assert endpoint[0].startswith("239.255.")
    assert endpoint[1] > 0
    results.append("PASS: Multicast endpoint allocation")

    sender_id = b"0123456789abcdef"
    frame0 = samples_to_bytes([100, -100])
    packet_raw = pack_audio_packet(
        sender_id=sender_id,
        sequence=7,
        timestamp_ms=12345,
        primary_payload=frame0,
        redundant_sequence=6,
        redundant_payload=frame0,
    )
    packet = unpack_audio_packet(packet_raw)
    assert packet.sender_id == sender_id
    assert packet.sequence == 7
    assert packet.redundant_sequence == 6
    assert packet.primary_payload == frame0
    assert packet.redundant_payload == frame0
    results.append("PASS: Audio packet encode/decode")

    recovery_buffer = RedundantJitterBuffer(
        frame_bytes=len(frame0),
        startup_frames=1,
        startup_timeout=0.0,
    )
    recovery_buffer.push(
        unpack_audio_packet(
            pack_audio_packet(
                sender_id=sender_id,
                sequence=1,
                timestamp_ms=1,
                primary_payload=samples_to_bytes([200, -200]),
                redundant_sequence=0,
                redundant_payload=frame0,
            )
        )
    )
    recovered = recovery_buffer.pop()
    primary = recovery_buffer.pop()
    assert bytes_to_samples(recovered) == [100, -100]
    assert bytes_to_samples(primary) == [200, -200]
    results.append("PASS: Redundancy recovers one lost frame without retransmission")

    frame_a = samples_to_bytes([0, 0])
    frame_c = samples_to_bytes([1000, -1000])
    interpolation_buffer = RedundantJitterBuffer(
        frame_bytes=len(frame_a),
        startup_frames=1,
        startup_timeout=0.0,
    )
    interpolation_buffer.push(
        unpack_audio_packet(
            pack_audio_packet(
                sender_id=sender_id,
                sequence=0,
                timestamp_ms=1,
                primary_payload=frame_a,
            )
        )
    )
    interpolation_buffer.push(
        unpack_audio_packet(
            pack_audio_packet(
                sender_id=sender_id,
                sequence=2,
                timestamp_ms=2,
                primary_payload=frame_c,
            )
        )
    )
    first = interpolation_buffer.pop()
    concealed = interpolation_buffer.pop()
    third = interpolation_buffer.pop()
    assert bytes_to_samples(first) == [0, 0]
    assert bytes_to_samples(concealed) == [500, -500]
    assert bytes_to_samples(third) == [1000, -1000]
    results.append("PASS: Missing frame is interpolated from buffered neighbors")

    print("\n" + "=" * 50)
    print("Multicast Audio Test Results:")
    print("=" * 50)
    for result in results:
        print(f"  {result}")
    print("=" * 50)
    print(f"All {len(results)} tests completed!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
