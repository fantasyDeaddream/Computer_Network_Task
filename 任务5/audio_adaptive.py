from __future__ import annotations

import audioop
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _ensure_task2_on_path() -> None:
    base = Path(__file__).resolve().parents[1]
    task2_dir = base / "任务2"
    if str(task2_dir) not in sys.path:
        sys.path.insert(0, str(task2_dir))


_ensure_task2_on_path()

from audio_config import CHANNELS, CHUNK_SIZE, SAMPLE_RATE, SAMPLE_WIDTH


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int
    channels: int
    sample_width: int
    chunk_size: int

    @property
    def bytes_per_frame(self) -> int:
        return self.channels * self.sample_width

    @property
    def bytes_per_chunk(self) -> int:
        return self.bytes_per_frame * self.chunk_size

    def to_payload(self) -> dict:
        return {
            "sample_rate": int(self.sample_rate),
            "channels": int(self.channels),
            "sample_width": int(self.sample_width),
            "chunk_size": int(self.chunk_size),
        }

    def describe(self) -> str:
        bits = self.sample_width * 8
        channel_text = "mono" if self.channels == 1 else f"{self.channels}ch"
        return (
            f"{self.sample_rate}Hz/{channel_text}/{bits}bit/"
            f"{self.chunk_size}f"
        )

    @classmethod
    def from_payload(
        cls, payload: Optional[dict], default: Optional["AudioFormat"] = None
    ) -> "AudioFormat":
        if default is None:
            default = CANONICAL_AUDIO_FORMAT
        if not payload:
            return default

        try:
            sample_rate = int(payload.get("sample_rate", default.sample_rate))
            channels = int(payload.get("channels", default.channels))
            sample_width = int(payload.get("sample_width", default.sample_width))
            chunk_size = int(payload.get("chunk_size", default.chunk_size))
        except (AttributeError, TypeError, ValueError):
            return default

        if sample_rate <= 0 or channels <= 0 or sample_width <= 0 or chunk_size <= 0:
            return default
        if channels not in (1, 2) or sample_width not in (1, 2):
            return default
        return cls(sample_rate, channels, sample_width, chunk_size)


CANONICAL_AUDIO_FORMAT = AudioFormat(
    sample_rate=SAMPLE_RATE,
    channels=CHANNELS,
    sample_width=SAMPLE_WIDTH,
    chunk_size=CHUNK_SIZE,
)


@dataclass(frozen=True)
class AdaptiveAudioProfile:
    name: str
    audio_format: AudioFormat
    max_delay_ms: float
    max_jitter_ms: float
    max_loss_percent: float


ADAPTIVE_AUDIO_PROFILES: Tuple[AdaptiveAudioProfile, ...] = (
    AdaptiveAudioProfile(
        name="rich",
        audio_format=AudioFormat(16000, 2, 2, 1024),
        max_delay_ms=90.0,
        max_jitter_ms=12.0,
        max_loss_percent=0.8,
    ),
    AdaptiveAudioProfile(
        name="wideband",
        audio_format=AudioFormat(16000, 1, 2, 768),
        max_delay_ms=180.0,
        max_jitter_ms=35.0,
        max_loss_percent=2.0,
    ),
    AdaptiveAudioProfile(
        name="balanced",
        audio_format=AudioFormat(12000, 1, 2, 512),
        max_delay_ms=280.0,
        max_jitter_ms=80.0,
        max_loss_percent=5.0,
    ),
    AdaptiveAudioProfile(
        name="resilient",
        audio_format=AudioFormat(8000, 1, 1, 256),
        max_delay_ms=float("inf"),
        max_jitter_ms=float("inf"),
        max_loss_percent=float("inf"),
    ),
)

DEFAULT_ADAPTIVE_PROFILE = ADAPTIVE_AUDIO_PROFILES[1]

_PROFILE_BY_NAME: Dict[str, AdaptiveAudioProfile] = {
    profile.name: profile for profile in ADAPTIVE_AUDIO_PROFILES
}


def get_profile_by_name(name: Optional[str]) -> AdaptiveAudioProfile:
    if not name:
        return DEFAULT_ADAPTIVE_PROFILE
    return _PROFILE_BY_NAME.get(name, DEFAULT_ADAPTIVE_PROFILE)


def get_profile_name_for_format(audio_format: AudioFormat) -> str:
    for profile in ADAPTIVE_AUDIO_PROFILES:
        if profile.audio_format == audio_format:
            return profile.name
    return "custom"


def choose_adaptive_profile(
    delay_ms: float,
    jitter_ms: float,
    packet_loss_percent: float,
    current_profile_name: Optional[str] = None,
) -> AdaptiveAudioProfile:
    delay_ms = max(0.0, float(delay_ms))
    jitter_ms = max(0.0, float(jitter_ms))
    packet_loss_percent = max(0.0, float(packet_loss_percent))

    candidate = ADAPTIVE_AUDIO_PROFILES[-1]
    for profile in ADAPTIVE_AUDIO_PROFILES:
        if (
            delay_ms <= profile.max_delay_ms
            and jitter_ms <= profile.max_jitter_ms
            and packet_loss_percent <= profile.max_loss_percent
        ):
            candidate = profile
            break

    current = get_profile_by_name(current_profile_name)
    current_index = ADAPTIVE_AUDIO_PROFILES.index(current)
    candidate_index = ADAPTIVE_AUDIO_PROFILES.index(candidate)

    if candidate_index >= current_index:
        return candidate

    if current_index == 0:
        return current

    upgrade_target = ADAPTIVE_AUDIO_PROFILES[current_index - 1]
    if (
        delay_ms <= upgrade_target.max_delay_ms * 0.8
        and jitter_ms <= upgrade_target.max_jitter_ms * 0.8
        and packet_loss_percent <= upgrade_target.max_loss_percent * 0.8
    ):
        return candidate
    return current


def _normalize_frame_alignment(raw: bytes, audio_format: AudioFormat) -> bytes:
    frame_bytes = audio_format.bytes_per_frame
    if frame_bytes <= 0 or not raw:
        return raw
    usable = len(raw) - (len(raw) % frame_bytes)
    return raw[:usable]


def _convert_channels(raw: bytes, width: int, src_channels: int, dst_channels: int) -> bytes:
    if src_channels == dst_channels or not raw:
        return raw
    if src_channels == 1 and dst_channels == 2:
        return audioop.tostereo(raw, width, 1.0, 1.0)
    if src_channels == 2 and dst_channels == 1:
        return audioop.tomono(raw, width, 0.5, 0.5)
    raise ValueError(f"unsupported channel conversion: {src_channels} -> {dst_channels}")


def transcode_pcm(
    raw: bytes,
    source_format: AudioFormat,
    target_format: AudioFormat,
    rate_state: Optional[tuple] = None,
) -> tuple[bytes, Optional[tuple]]:
    if not raw:
        return b"", rate_state
    raw = _normalize_frame_alignment(raw, source_format)
    if not raw:
        return b"", rate_state

    working_width = 2 if 1 in (source_format.sample_width, target_format.sample_width) else source_format.sample_width
    working = raw

    if source_format.sample_width != working_width:
        working = audioop.lin2lin(working, source_format.sample_width, working_width)
    working = _convert_channels(
        working,
        working_width,
        source_format.channels,
        target_format.channels,
    )
    if source_format.sample_rate != target_format.sample_rate:
        working, rate_state = audioop.ratecv(
            working,
            working_width,
            target_format.channels,
            source_format.sample_rate,
            target_format.sample_rate,
            rate_state,
        )
    if working_width != target_format.sample_width:
        working = audioop.lin2lin(working, working_width, target_format.sample_width)

    working = _normalize_frame_alignment(working, target_format)
    return working, rate_state


class ReframingAudioTranscoder:
    def __init__(self, output_format: AudioFormat) -> None:
        self._output_format = output_format
        self._last_input_format: Optional[AudioFormat] = None
        self._rate_state: Optional[tuple] = None
        self._buffer = bytearray()

    @property
    def output_format(self) -> AudioFormat:
        return self._output_format

    def update_output_format(self, output_format: AudioFormat) -> None:
        if output_format != self._output_format:
            self._output_format = output_format
            self._rate_state = None
            self._buffer.clear()

    def feed(self, raw: bytes, input_format: AudioFormat) -> List[bytes]:
        if not raw:
            return []
        if self._last_input_format != input_format:
            self._last_input_format = input_format
            self._rate_state = None

        converted, self._rate_state = transcode_pcm(
            raw,
            input_format,
            self._output_format,
            self._rate_state,
        )
        if not converted:
            return []

        self._buffer.extend(converted)
        chunk_bytes = self._output_format.bytes_per_chunk
        chunks: List[bytes] = []
        while len(self._buffer) >= chunk_bytes:
            chunks.append(bytes(self._buffer[:chunk_bytes]))
            del self._buffer[:chunk_bytes]
        return chunks

    def reset(self) -> None:
        self._last_input_format = None
        self._rate_state = None
        self._buffer.clear()
