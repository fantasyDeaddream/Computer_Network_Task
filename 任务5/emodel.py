"""
ITU-T G.107 E-model helpers for the conference demo.

The full recommendation has many telephony parameters. For this project we use
the usual VoIP planning subset:

    R = Ro - Is - Id - Ie_eff + A

Delay, jitter and packet loss are measured from audio frames, then converted to
an R factor and a narrowband MOS estimate.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional


DEFAULT_RO = 93.2
DEFAULT_IS = 0.0
DEFAULT_A = 0.0
DEFAULT_IE = 0.0
DEFAULT_BPL = 10.0
DEFAULT_BURST_RATIO = 1.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def delay_impairment(delay_ms: float) -> float:
    """Simplified G.107 delay impairment for mouth-to-ear delay."""
    delay_ms = max(0.0, delay_ms)
    late_delay = max(0.0, delay_ms - 177.3)
    return 0.024 * delay_ms + 0.11 * late_delay


def equipment_impairment(
    packet_loss_percent: float,
    ie: float = DEFAULT_IE,
    bpl: float = DEFAULT_BPL,
    burst_ratio: float = DEFAULT_BURST_RATIO,
) -> float:
    """Effective equipment impairment with packet loss."""
    packet_loss_percent = _clamp(packet_loss_percent, 0.0, 100.0)
    burst_ratio = max(0.1, burst_ratio)
    bpl = max(0.1, bpl)
    return ie + (95.0 - ie) * packet_loss_percent / (
        packet_loss_percent / burst_ratio + bpl
    )


def mos_from_r(r_factor: float) -> float:
    """Convert an R factor to narrowband MOS-CQE."""
    if r_factor <= 0:
        return 1.0
    if r_factor >= 100:
        return 4.5
    mos = (
        1.0
        + 0.035 * r_factor
        + r_factor * (r_factor - 60.0) * (100.0 - r_factor) * 7.0e-6
    )
    return _clamp(mos, 1.0, 4.5)


def rating_from_r(r_factor: float) -> str:
    if r_factor >= 90:
        return "excellent"
    if r_factor >= 80:
        return "good"
    if r_factor >= 70:
        return "fair"
    if r_factor >= 60:
        return "poor"
    return "bad"


@dataclass(frozen=True)
class EModelResult:
    r_factor: float
    mos: float
    rating: str
    delay_impairment: float
    equipment_impairment: float
    effective_delay_ms: float


def evaluate_quality(
    delay_ms: float,
    packet_loss_percent: float,
    jitter_ms: float = 0.0,
    ro: float = DEFAULT_RO,
    is_factor: float = DEFAULT_IS,
    ie: float = DEFAULT_IE,
    bpl: float = DEFAULT_BPL,
    burst_ratio: float = DEFAULT_BURST_RATIO,
    advantage_factor: float = DEFAULT_A,
) -> EModelResult:
    """
    Evaluate conversational speech quality from network impairment metrics.

    G.107 does not have a separate jitter term. The demo folds measured jitter
    into effective delay, which approximates a jitter buffer / late-packet cost.
    """
    effective_delay = max(0.0, delay_ms) + max(0.0, jitter_ms)
    id_factor = delay_impairment(effective_delay)
    ie_eff = equipment_impairment(packet_loss_percent, ie, bpl, burst_ratio)
    r_factor = ro - is_factor - id_factor - ie_eff + advantage_factor
    r_factor = _clamp(r_factor, 0.0, 100.0)
    return EModelResult(
        r_factor=r_factor,
        mos=mos_from_r(r_factor),
        rating=rating_from_r(r_factor),
        delay_impairment=id_factor,
        equipment_impairment=ie_eff,
        effective_delay_ms=effective_delay,
    )


@dataclass
class _SenderQualityState:
    received_packets: int = 0
    expected_packets: int = 0
    lost_packets: int = 0
    last_seq: Optional[int] = None
    last_sent_ms: Optional[float] = None
    last_arrival_ms: Optional[float] = None
    jitter_ms: float = 0.0
    delay_samples: Deque[float] = field(default_factory=lambda: deque(maxlen=200))
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class AudioQualityMonitor:
    """Tracks per-speaker network metrics and E-model quality reports."""

    def __init__(self) -> None:
        self._states: Dict[str, _SenderQualityState] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._states.clear()

    def observe_packet(
        self,
        sender: str,
        seq: Optional[int],
        sent_timestamp_ms: Optional[float],
        arrival_timestamp_ms: Optional[float] = None,
    ) -> dict:
        if arrival_timestamp_ms is None:
            arrival_timestamp_ms = time.time() * 1000.0
        with self._lock:
            state = self._states.setdefault(sender, _SenderQualityState())
            self._update_packet_counts(state, seq)
            self._update_delay_and_jitter(state, sent_timestamp_ms, arrival_timestamp_ms)
            state.received_packets += 1
            state.last_seen = time.time()
            return self._build_report(sender, state)

    def get_reports(self) -> Dict[str, dict]:
        with self._lock:
            return {
                sender: self._build_report(sender, state)
                for sender, state in self._states.items()
            }

    def _update_packet_counts(
        self, state: _SenderQualityState, seq: Optional[int]
    ) -> None:
        if seq is None:
            state.expected_packets += 1
            return

        try:
            seq = int(seq)
        except (TypeError, ValueError):
            state.expected_packets += 1
            return

        if state.last_seq is None:
            state.expected_packets += 1
            state.last_seq = seq
            return

        delta = seq - state.last_seq
        if 1 <= delta <= 100000:
            state.expected_packets += delta
            state.lost_packets += max(0, delta - 1)
            state.last_seq = seq
        elif delta <= 0:
            # Reordered or duplicated frame; do not count it as new expected data.
            return
        else:
            # Sender restarted its sequence counter.
            state.expected_packets += 1
            state.last_seq = seq

    def _update_delay_and_jitter(
        self,
        state: _SenderQualityState,
        sent_timestamp_ms: Optional[float],
        arrival_timestamp_ms: float,
    ) -> None:
        if sent_timestamp_ms is None:
            state.last_arrival_ms = arrival_timestamp_ms
            return

        try:
            sent_ms = float(sent_timestamp_ms)
        except (TypeError, ValueError):
            state.last_arrival_ms = arrival_timestamp_ms
            return

        delay = max(0.0, arrival_timestamp_ms - sent_ms)
        state.delay_samples.append(delay)

        if state.last_sent_ms is not None and state.last_arrival_ms is not None:
            transit_delta = (arrival_timestamp_ms - state.last_arrival_ms) - (
                sent_ms - state.last_sent_ms
            )
            state.jitter_ms += (abs(transit_delta) - state.jitter_ms) / 16.0

        state.last_sent_ms = sent_ms
        state.last_arrival_ms = arrival_timestamp_ms

    def _build_report(self, sender: str, state: _SenderQualityState) -> dict:
        expected = max(1, state.expected_packets)
        packet_loss = 100.0 * state.lost_packets / expected
        if state.delay_samples:
            avg_delay = sum(state.delay_samples) / len(state.delay_samples)
        else:
            avg_delay = 0.0

        result = evaluate_quality(
            delay_ms=avg_delay,
            packet_loss_percent=packet_loss,
            jitter_ms=state.jitter_ms,
        )
        return {
            "sender": sender,
            "received_packets": state.received_packets,
            "expected_packets": state.expected_packets,
            "lost_packets": state.lost_packets,
            "packet_loss_percent": packet_loss,
            "delay_ms": avg_delay,
            "jitter_ms": state.jitter_ms,
            "r_factor": result.r_factor,
            "mos": result.mos,
            "rating": result.rating,
            "delay_impairment": result.delay_impairment,
            "equipment_impairment": result.equipment_impairment,
            "effective_delay_ms": result.effective_delay_ms,
            "first_seen": state.first_seen,
            "last_seen": state.last_seen,
            "last_seen_age_s": max(0.0, time.time() - state.last_seen),
        }
