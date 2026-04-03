"""
任务5 音频传输层测试：
- 组播端点分配
- 带时间戳的冗余音频包编解码
- 逐路抖动缓冲的丢包恢复与插值补偿
- 低频噪音检测/滤波
- 多路混音
"""

from __future__ import annotations

from array import array
import sys

from multicast_audio import (
    HighPassFilterState,
    RedundantJitterBuffer,
    allocate_multicast_endpoint,
    analyze_noise,
    bytes_to_samples,
    frame_duration_ms,
    has_low_frequency_noise,
    high_pass_filter_pcm16,
    mix_pcm16_frames,
    pack_audio_packet,
    pcm16_rms,
    unpack_audio_packet,
)


def samples_to_bytes(values: list[int]) -> bytes:
    pcm = array("h", values)
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


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
        redundant_timestamp_ms=12345 - 64,
        redundant_payload=frame0,
    )
    packet = unpack_audio_packet(packet_raw)
    assert packet.sender_id == sender_id
    assert packet.sequence == 7
    assert packet.timestamp_ms == 12345
    assert packet.redundant_sequence == 6
    assert packet.redundant_timestamp_ms == 12281
    assert packet.primary_payload == frame0
    assert packet.redundant_payload == frame0
    results.append("PASS: Audio packet encode/decode with per-frame timestamps")

    duration_ms = frame_duration_ms(16000, 1024)
    recovery_buffer = RedundantJitterBuffer(
        frame_bytes=len(frame0),
        frame_duration_ms=duration_ms,
        startup_frames=1,
        startup_timeout=0.0,
    )
    recovery_buffer.push(
        unpack_audio_packet(
            pack_audio_packet(
                sender_id=sender_id,
                sequence=1,
                timestamp_ms=64,
                primary_payload=samples_to_bytes([200, -200]),
                redundant_sequence=0,
                redundant_timestamp_ms=0,
                redundant_payload=frame0,
            )
        )
    )
    recovered = recovery_buffer.pop()
    primary = recovery_buffer.pop()
    assert recovered is not None and primary is not None
    assert recovered.sequence == 0 and recovered.timestamp_ms == 0
    assert primary.sequence == 1 and primary.timestamp_ms == 64
    assert list(bytes_to_samples(recovered.payload)) == [100, -100]
    assert list(bytes_to_samples(primary.payload)) == [200, -200]
    results.append("PASS: Redundancy recovers one lost frame without retransmission")

    frame_a = samples_to_bytes([0, 0])
    frame_c = samples_to_bytes([1000, -1000])
    interpolation_buffer = RedundantJitterBuffer(
        frame_bytes=len(frame_a),
        frame_duration_ms=duration_ms,
        startup_frames=1,
        startup_timeout=0.0,
    )
    interpolation_buffer.push(
        unpack_audio_packet(
            pack_audio_packet(
                sender_id=sender_id,
                sequence=0,
                timestamp_ms=0,
                primary_payload=frame_a,
            )
        )
    )
    interpolation_buffer.push(
        unpack_audio_packet(
            pack_audio_packet(
                sender_id=sender_id,
                sequence=2,
                timestamp_ms=128,
                primary_payload=frame_c,
            )
        )
    )
    first = interpolation_buffer.pop()
    concealed = interpolation_buffer.pop()
    third = interpolation_buffer.pop()
    assert first is not None and concealed is not None and third is not None
    assert list(bytes_to_samples(first.payload)) == [0, 0]
    assert list(bytes_to_samples(concealed.payload)) == [500, -500]
    assert list(bytes_to_samples(third.payload)) == [1000, -1000]
    results.append("PASS: Missing frame is interpolated from buffered neighbors")

    noisy_frame = samples_to_bytes([2500] * 512)
    metrics = analyze_noise(noisy_frame)
    assert has_low_frequency_noise(metrics) is True
    filtered_frame = high_pass_filter_pcm16(
        noisy_frame,
        state=HighPassFilterState(),
        sample_rate=16000,
    )
    assert pcm16_rms(filtered_frame) < pcm16_rms(noisy_frame)
    results.append("PASS: Low-frequency noise is detected and attenuated by high-pass filtering")

    mixed = mix_pcm16_frames(
        [
            (samples_to_bytes([1000, -1000]), 1.0),
            (samples_to_bytes([500, -500]), 0.5),
        ]
    )
    assert list(bytes_to_samples(mixed)) == [833, -833]
    results.append("PASS: Multi-route audio is mixed into a single playout stream")

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
