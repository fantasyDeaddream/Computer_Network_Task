from __future__ import annotations

import random
import socket
import struct
import sys
import threading
import time
from array import array
from dataclasses import dataclass, field
from math import sqrt
from typing import Dict, Iterable, Optional, Tuple

PACKET_MAGIC = b"MCA2"
PACKET_VERSION = 2
SENDER_ID_SIZE = 16
MULTICAST_TTL = 1
MULTICAST_GROUP_PREFIX = "239.255"
MULTICAST_PORT_MIN = 20000
MULTICAST_PORT_MAX = 40000

_PACKET_HEADER = struct.Struct("!4sBB16sIIHIIH")


@dataclass
class AudioPacket:
    sender_id: bytes
    sequence: int
    timestamp_ms: int
    primary_payload: bytes
    redundant_sequence: Optional[int] = None
    redundant_timestamp_ms: Optional[int] = None
    redundant_payload: bytes = b""


@dataclass
class BufferedAudioFrame:
    sequence: int
    timestamp_ms: int
    payload: bytes
    recovered: bool = False
    arrival_time: float = field(default_factory=time.monotonic)


@dataclass
class HighPassFilterState:
    previous_input: int = 0
    previous_output: float = 0.0


@dataclass
class NoiseMetrics:
    rms: float
    low_frequency_ratio: float
    zero_crossing_rate: float


def allocate_multicast_endpoint(
    used_endpoints: set[Tuple[str, int]],
) -> Tuple[str, int]:
    for _ in range(1024):
        group = (
            f"{MULTICAST_GROUP_PREFIX}."
            f"{random.randint(1, 254)}.{random.randint(1, 254)}"
        )
        port = random.randint(MULTICAST_PORT_MIN, MULTICAST_PORT_MAX)
        endpoint = (group, port)
        if endpoint not in used_endpoints:
            return endpoint
    raise RuntimeError("failed to allocate multicast endpoint")


def pack_audio_packet(
    *,
    sender_id: bytes,
    sequence: int,
    timestamp_ms: int,
    primary_payload: bytes,
    redundant_sequence: Optional[int] = None,
    redundant_timestamp_ms: Optional[int] = None,
    redundant_payload: bytes = b"",
) -> bytes:
    if len(sender_id) != SENDER_ID_SIZE:
        raise ValueError("invalid sender id length")
    if not primary_payload:
        raise ValueError("primary payload is empty")
    has_redundancy = bool(redundant_payload)
    if has_redundancy and redundant_sequence is None:
        raise ValueError("redundant sequence is required when redundancy exists")
    if has_redundancy and redundant_timestamp_ms is None:
        raise ValueError("redundant timestamp is required when redundancy exists")

    header = _PACKET_HEADER.pack(
        PACKET_MAGIC,
        PACKET_VERSION,
        1 if has_redundancy else 0,
        sender_id,
        sequence & 0xFFFFFFFF,
        timestamp_ms & 0xFFFFFFFF,
        len(primary_payload),
        0 if redundant_sequence is None else redundant_sequence & 0xFFFFFFFF,
        0 if redundant_timestamp_ms is None else redundant_timestamp_ms & 0xFFFFFFFF,
        len(redundant_payload),
    )
    return header + primary_payload + redundant_payload


def unpack_audio_packet(raw: bytes) -> AudioPacket:
    if len(raw) < _PACKET_HEADER.size:
        raise ValueError("packet too small")

    (
        magic,
        version,
        flags,
        sender_id,
        sequence,
        timestamp_ms,
        primary_len,
        redundant_sequence,
        redundant_timestamp_ms,
        redundant_len,
    ) = _PACKET_HEADER.unpack(raw[: _PACKET_HEADER.size])
    if magic != PACKET_MAGIC:
        raise ValueError("invalid packet magic")
    if version != PACKET_VERSION:
        raise ValueError("unsupported packet version")

    expected_size = _PACKET_HEADER.size + primary_len + redundant_len
    if len(raw) != expected_size:
        raise ValueError("invalid packet size")

    offset = _PACKET_HEADER.size
    primary_payload = raw[offset : offset + primary_len]
    offset += primary_len
    redundant_payload = raw[offset : offset + redundant_len]
    has_redundancy = bool(flags & 0x01)
    return AudioPacket(
        sender_id=sender_id,
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        primary_payload=primary_payload,
        redundant_sequence=redundant_sequence if has_redundancy else None,
        redundant_timestamp_ms=redundant_timestamp_ms if has_redundancy else None,
        redundant_payload=redundant_payload if has_redundancy else b"",
    )


def frame_duration_seconds(sample_rate: int, chunk_size: int) -> float:
    return chunk_size / float(sample_rate)


def frame_duration_ms(sample_rate: int, chunk_size: int) -> int:
    return int(round(frame_duration_seconds(sample_rate, chunk_size) * 1000.0))


def bytes_to_samples(frame: bytes) -> array:
    samples = array("h")
    samples.frombytes(frame)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def samples_to_bytes(samples: array) -> bytes:
    if sys.byteorder != "little":
        converted = array("h", samples)
        converted.byteswap()
        return converted.tobytes()
    return samples.tobytes()


def attenuate_pcm16(frame: bytes, factor: float) -> bytes:
    samples = bytes_to_samples(frame)
    out = array("h")
    for sample in samples:
        out.append(_clamp_pcm16(int(sample * factor)))
    return samples_to_bytes(out)


def interpolate_pcm16(
    previous_frame: Optional[bytes],
    next_frame: Optional[bytes],
    frame_bytes: int,
    ratio: float = 0.5,
) -> bytes:
    if previous_frame is None and next_frame is None:
        return b"\x00" * frame_bytes
    if previous_frame is None:
        return next_frame[:frame_bytes]
    if next_frame is None:
        return attenuate_pcm16(previous_frame[:frame_bytes], 0.92)

    previous_samples = bytes_to_samples(previous_frame[:frame_bytes])
    next_samples = bytes_to_samples(next_frame[:frame_bytes])
    mixed = array("h")
    for previous_sample, next_sample in zip(previous_samples, next_samples):
        value = previous_sample + (next_sample - previous_sample) * ratio
        mixed.append(_clamp_pcm16(int(value)))
    return samples_to_bytes(mixed)


def high_pass_filter_pcm16(
    frame: bytes,
    state: HighPassFilterState,
    *,
    sample_rate: int,
    cutoff_hz: float = 140.0,
) -> bytes:
    dt = 1.0 / float(sample_rate)
    rc = 1.0 / (2.0 * 3.141592653589793 * cutoff_hz)
    alpha = rc / (rc + dt)
    samples = bytes_to_samples(frame)
    filtered = array("h")
    previous_input = state.previous_input
    previous_output = state.previous_output
    for sample in samples:
        output = alpha * (previous_output + sample - previous_input)
        filtered.append(_clamp_pcm16(int(output)))
        previous_input = sample
        previous_output = output
    state.previous_input = previous_input
    state.previous_output = previous_output
    return samples_to_bytes(filtered)


def analyze_noise(frame: bytes) -> NoiseMetrics:
    samples = bytes_to_samples(frame)
    if not samples:
        return NoiseMetrics(rms=0.0, low_frequency_ratio=0.0, zero_crossing_rate=0.0)

    energy = 0.0
    low_energy = 0.0
    zero_crossings = 0
    running_average = 0.0
    smoothing = 0.03
    previous = samples[0]

    for sample in samples:
        sample_float = float(sample)
        energy += sample_float * sample_float
        running_average = running_average + smoothing * (sample_float - running_average)
        low_energy += running_average * running_average
        if (previous >= 0 > sample) or (previous < 0 <= sample):
            zero_crossings += 1
        previous = sample

    rms = sqrt(energy / len(samples))
    low_ratio = min(1.0, low_energy / max(energy, 1.0))
    zero_crossing_rate = zero_crossings / max(1, len(samples) - 1)
    return NoiseMetrics(
        rms=rms,
        low_frequency_ratio=low_ratio,
        zero_crossing_rate=zero_crossing_rate,
    )


def has_low_frequency_noise(
    metrics: NoiseMetrics,
    *,
    rms_threshold: float = 280.0,
    low_ratio_threshold: float = 0.55,
    zero_crossing_threshold: float = 0.08,
) -> bool:
    if metrics.rms < rms_threshold:
        return False
    return (
        metrics.low_frequency_ratio >= low_ratio_threshold
        and metrics.zero_crossing_rate <= zero_crossing_threshold
    )


def mix_pcm16_frames(frames: Iterable[Tuple[bytes, float]]) -> bytes:
    weighted_frames = [(frame, weight) for frame, weight in frames if frame and weight > 0.0]
    if not weighted_frames:
        return b""
    if len(weighted_frames) == 1:
        return weighted_frames[0][0]

    sample_arrays = [bytes_to_samples(frame) for frame, _ in weighted_frames]
    frame_length = len(sample_arrays[0])
    output = array("h")
    normalization = max(1.0, sum(weight for _, weight in weighted_frames))

    for index in range(frame_length):
        value = 0.0
        for samples, (_, weight) in zip(sample_arrays, weighted_frames):
            value += samples[index] * weight
        output.append(_clamp_pcm16(int(value / normalization)))
    return samples_to_bytes(output)


def pcm16_rms(frame: bytes) -> float:
    samples = bytes_to_samples(frame)
    if not samples:
        return 0.0
    energy = 0.0
    for sample in samples:
        energy += float(sample) * float(sample)
    return sqrt(energy / len(samples))


def _clamp_pcm16(value: int) -> int:
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return value


class RedundantJitterBuffer:

    def __init__(
        self,
        *,
        frame_bytes: int,
        frame_duration_ms: int,
        startup_frames: int = 3,
        max_frames: int = 48,
        startup_timeout: float = 0.18,
    ) -> None:
        self._frame_bytes = frame_bytes
        self._frame_duration_ms = frame_duration_ms
        self._startup_frames = startup_frames
        self._max_frames = max_frames
        self._startup_timeout = startup_timeout
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._frames: Dict[int, BufferedAudioFrame] = {}
            self._next_sequence: Optional[int] = None
            self._next_timestamp_ms: Optional[int] = None
            self._last_frame = b"\x00" * self._frame_bytes
            self._started = False
            self._startup_deadline: Optional[float] = None

    def push(self, packet: AudioPacket) -> None:
        with self._lock:
            if self._next_sequence is None:
                first_sequence = packet.sequence
                first_timestamp = packet.timestamp_ms
                if (
                    packet.redundant_sequence is not None
                    and packet.redundant_timestamp_ms is not None
                ):
                    if packet.redundant_sequence < first_sequence:
                        first_sequence = packet.redundant_sequence
                        first_timestamp = packet.redundant_timestamp_ms
                self._next_sequence = first_sequence
                self._next_timestamp_ms = first_timestamp
                self._startup_deadline = time.monotonic() + self._startup_timeout

            self._store_frame(
                packet.sequence,
                packet.timestamp_ms,
                packet.primary_payload,
                recovered=False,
            )
            if (
                packet.redundant_sequence is not None
                and packet.redundant_timestamp_ms is not None
                and packet.redundant_payload
            ):
                self._store_frame(
                    packet.redundant_sequence,
                    packet.redundant_timestamp_ms,
                    packet.redundant_payload,
                    recovered=True,
                )
            self._trim_future_frames()

    def pop(self) -> Optional[BufferedAudioFrame]:
        with self._lock:
            if self._next_sequence is None or self._next_timestamp_ms is None:
                return None
            if not self._started:
                if not self._startup_ready():
                    return None
                self._started = True

            sequence = self._next_sequence
            timestamp_ms = self._next_timestamp_ms
            frame = self._frames.pop(sequence, None)
            if frame is not None:
                result = frame
            else:
                next_frame = self._find_next_frame(sequence)
                ratio = 0.5
                if next_frame is not None:
                    gap = max(1, next_frame.sequence - sequence)
                    ratio = 1.0 / (gap + 1)
                payload = interpolate_pcm16(
                    self._last_frame,
                    None if next_frame is None else next_frame.payload,
                    self._frame_bytes,
                    ratio=ratio,
                )
                result = BufferedAudioFrame(
                    sequence=sequence,
                    timestamp_ms=timestamp_ms,
                    payload=payload,
                    recovered=True,
                )

            self._last_frame = result.payload
            self._next_sequence += 1
            self._next_timestamp_ms += self._frame_duration_ms
            return result

    def _startup_ready(self) -> bool:
        if self._startup_deadline is None:
            return False
        if len(self._frames) >= self._startup_frames:
            return True
        return time.monotonic() >= self._startup_deadline

    def _store_frame(
        self,
        sequence: int,
        timestamp_ms: int,
        payload: bytes,
        recovered: bool,
    ) -> None:
        if self._next_sequence is not None and sequence < self._next_sequence:
            return
        existing = self._frames.get(sequence)
        if existing is None or (existing.recovered and not recovered):
            self._frames[sequence] = BufferedAudioFrame(
                sequence=sequence,
                timestamp_ms=timestamp_ms,
                payload=payload[: self._frame_bytes],
                recovered=recovered,
            )

    def _trim_future_frames(self) -> None:
        if len(self._frames) <= self._max_frames:
            return
        for sequence in sorted(self._frames.keys())[:-self._max_frames]:
            self._frames.pop(sequence, None)

    def _find_next_frame(self, sequence: int) -> Optional[BufferedAudioFrame]:
        future_sequences = [seq for seq in self._frames if seq > sequence]
        if not future_sequences:
            return None
        next_sequence = min(future_sequences)
        return self._frames.get(next_sequence)


def resolve_multicast_interface_ip(remote_host: str) -> str:
    candidates = [remote_host]
    if remote_host in {"127.0.0.1", "localhost", "0.0.0.0"}:
        candidates.append("8.8.8.8")

    loopback_ip = None
    for candidate in candidates:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((candidate, 9))
            local_ip = probe.getsockname()[0]
            if local_ip.startswith("127."):
                loopback_ip = local_ip
                continue
            return local_ip
        except OSError:
            continue
        finally:
            probe.close()
    return loopback_ip or "0.0.0.0"
