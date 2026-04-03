from __future__ import annotations

import random
import socket
import struct
import sys
import threading
import time
from array import array
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

PACKET_MAGIC = b"MCA1"
PACKET_VERSION = 1
SENDER_ID_SIZE = 16
MULTICAST_TTL = 1
MULTICAST_GROUP_PREFIX = "239.255"
MULTICAST_PORT_MIN = 20000
MULTICAST_PORT_MAX = 40000

_PACKET_HEADER = struct.Struct("!4sBB16sIIHIH")


@dataclass
class AudioPacket:
    sender_id: bytes
    sequence: int
    timestamp_ms: int
    primary_payload: bytes
    redundant_sequence: Optional[int] = None
    redundant_payload: bytes = b""


@dataclass
class BufferedAudioFrame:
    sequence: int
    payload: bytes
    recovered: bool = False
    arrival_time: float = field(default_factory=time.monotonic)


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
    redundant_payload: bytes = b"",
) -> bytes:
    if len(sender_id) != SENDER_ID_SIZE:
        raise ValueError("invalid sender id length")
    if not primary_payload:
        raise ValueError("primary payload is empty")
    has_redundancy = bool(redundant_payload)
    if has_redundancy and redundant_sequence is None:
        raise ValueError("redundant sequence is required when redundancy exists")
    header = _PACKET_HEADER.pack(
        PACKET_MAGIC,
        PACKET_VERSION,
        1 if has_redundancy else 0,
        sender_id,
        sequence & 0xFFFFFFFF,
        timestamp_ms & 0xFFFFFFFF,
        len(primary_payload),
        0 if redundant_sequence is None else redundant_sequence & 0xFFFFFFFF,
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
    return AudioPacket(
        sender_id=sender_id,
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        primary_payload=primary_payload,
        redundant_sequence=redundant_sequence if flags & 0x01 else None,
        redundant_payload=redundant_payload if flags & 0x01 else b"",
    )


def frame_duration_seconds(sample_rate: int, chunk_size: int) -> float:
    return chunk_size / float(sample_rate)


def _bytes_to_samples(frame: bytes) -> array:
    samples = array("h")
    samples.frombytes(frame)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _samples_to_bytes(samples: array) -> bytes:
    if sys.byteorder != "little":
        samples = array("h", samples)
        samples.byteswap()
        return samples.tobytes()
    return samples.tobytes()


def attenuate_pcm16(frame: bytes, factor: float) -> bytes:
    samples = _bytes_to_samples(frame)
    out = array("h")
    for sample in samples:
        scaled = int(sample * factor)
        if scaled > 32767:
            scaled = 32767
        elif scaled < -32768:
            scaled = -32768
        out.append(scaled)
    return _samples_to_bytes(out)


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

    prev_samples = _bytes_to_samples(previous_frame[:frame_bytes])
    next_samples = _bytes_to_samples(next_frame[:frame_bytes])
    out = array("h")
    for prev, nxt in zip(prev_samples, next_samples):
        value = int(prev + (nxt - prev) * ratio)
        if value > 32767:
            value = 32767
        elif value < -32768:
            value = -32768
        out.append(value)
    return _samples_to_bytes(out)


class RedundantJitterBuffer:

    def __init__(
        self,
        *,
        frame_bytes: int,
        startup_frames: int = 3,
        max_frames: int = 48,
        startup_timeout: float = 0.18,
    ) -> None:
        self._frame_bytes = frame_bytes
        self._startup_frames = startup_frames
        self._max_frames = max_frames
        self._startup_timeout = startup_timeout
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._frames: Dict[int, BufferedAudioFrame] = {}
            self._next_sequence: Optional[int] = None
            self._last_frame = b"\x00" * self._frame_bytes
            self._started = False
            self._startup_deadline: Optional[float] = None

    def push(self, packet: AudioPacket) -> None:
        with self._lock:
            if self._next_sequence is None:
                first_sequence = packet.sequence
                if packet.redundant_sequence is not None:
                    first_sequence = min(first_sequence, packet.redundant_sequence)
                self._next_sequence = first_sequence
                self._startup_deadline = time.monotonic() + self._startup_timeout

            self._store_frame(packet.sequence, packet.primary_payload, recovered=False)
            if packet.redundant_sequence is not None and packet.redundant_payload:
                self._store_frame(
                    packet.redundant_sequence,
                    packet.redundant_payload,
                    recovered=True,
                )
            self._trim_future_frames()

    def pop(self) -> Optional[bytes]:
        with self._lock:
            if self._next_sequence is None:
                return None
            if not self._started:
                if not self._startup_ready():
                    return None
                self._started = True

            sequence = self._next_sequence
            frame = self._frames.pop(sequence, None)
            if frame is not None:
                payload = frame.payload
            else:
                next_sequence, next_payload = self._find_next_payload(sequence)
                ratio = 0.5
                if next_sequence is not None:
                    gap = max(1, next_sequence - sequence)
                    ratio = 1.0 / (gap + 1)
                payload = interpolate_pcm16(
                    self._last_frame,
                    next_payload,
                    self._frame_bytes,
                    ratio=ratio,
                )

            self._last_frame = payload
            self._next_sequence += 1
            return payload

    def _startup_ready(self) -> bool:
        if self._startup_deadline is None:
            return False
        if len(self._frames) >= self._startup_frames:
            return True
        return time.monotonic() >= self._startup_deadline

    def _store_frame(self, sequence: int, payload: bytes, recovered: bool) -> None:
        if self._next_sequence is not None and sequence < self._next_sequence:
            return
        existing = self._frames.get(sequence)
        if existing is None or (existing.recovered and not recovered):
            self._frames[sequence] = BufferedAudioFrame(
                sequence=sequence,
                payload=payload[: self._frame_bytes],
                recovered=recovered,
            )

    def _trim_future_frames(self) -> None:
        if len(self._frames) <= self._max_frames:
            return
        for sequence in sorted(self._frames.keys())[:-self._max_frames]:
            self._frames.pop(sequence, None)

    def _find_next_payload(self, sequence: int) -> Tuple[Optional[int], Optional[bytes]]:
        future_sequences = [seq for seq in self._frames if seq > sequence]
        if not future_sequences:
            return None, None
        next_sequence = min(future_sequences)
        frame = self._frames.get(next_sequence)
        if frame is None:
            return None, None
        return next_sequence, frame.payload


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
