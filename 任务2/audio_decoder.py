"""
Audio decoding and WAV normalization helpers.
"""

from __future__ import annotations

import audioop
import wave
from io import BytesIO

from audio_config import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH
from custom_exceptions import DecodingError


class AudioDecoder:
    """Decode WAV data and normalize it to the system playback format."""

    @staticmethod
    def _read_wav_params(wav_data: bytes) -> tuple[bytes, int, int, int]:
        if not wav_data:
            raise DecodingError("WAV数据不能为空")
        if not isinstance(wav_data, bytes):
            raise DecodingError("WAV数据必须是bytes类型")

        try:
            wav_buffer = BytesIO(wav_data)
            with wave.open(wav_buffer, "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                audio_frames = wav_file.readframes(wav_file.getnframes())
            return audio_frames, sample_rate, channels, sample_width
        except wave.Error as exc:
            raise DecodingError(f"无效的WAV格式: {exc}") from exc
        except Exception as exc:
            raise DecodingError(f"解码WAV格式失败: {exc}") from exc

    @staticmethod
    def decode_wav(wav_data: bytes) -> tuple[bytes, int, int, int]:
        audio_frames, sample_rate, channels, sample_width = AudioDecoder._read_wav_params(
            wav_data
        )

        if channels != CHANNELS:
            raise DecodingError(f"声道数不匹配: 期望{CHANNELS}，实际{channels}")
        if sample_width != SAMPLE_WIDTH:
            raise DecodingError(
                f"采样宽度不匹配: 期望{SAMPLE_WIDTH}字节，实际{sample_width}字节"
            )
        if sample_rate != SAMPLE_RATE:
            raise DecodingError(f"采样率不匹配: 期望{SAMPLE_RATE}Hz，实际{sample_rate}Hz")

        return audio_frames, sample_rate, channels, sample_width

    @staticmethod
    def normalize_wav(
        wav_data: bytes,
        target_sample_rate: int = SAMPLE_RATE,
        target_channels: int = CHANNELS,
        target_sample_width: int = SAMPLE_WIDTH,
    ) -> tuple[bytes, int, int, int]:
        audio_frames, sample_rate, channels, sample_width = AudioDecoder._read_wav_params(
            wav_data
        )

        try:
            normalized = audio_frames

            if sample_width == 1:
                # 8-bit PCM in WAV is unsigned; convert to signed before further processing.
                normalized = audioop.bias(normalized, 1, -128)

            if channels not in {1, 2}:
                raise DecodingError(f"暂不支持{channels}声道WAV文件")

            if channels != target_channels:
                if channels == 2 and target_channels == 1:
                    normalized = audioop.tomono(normalized, sample_width, 0.5, 0.5)
                elif channels == 1 and target_channels == 2:
                    normalized = audioop.tostereo(normalized, sample_width, 1, 1)
                channels = target_channels

            if sample_rate != target_sample_rate:
                normalized, _ = audioop.ratecv(
                    normalized,
                    sample_width,
                    channels,
                    sample_rate,
                    target_sample_rate,
                    None,
                )
                sample_rate = target_sample_rate

            if sample_width != target_sample_width:
                normalized = audioop.lin2lin(normalized, sample_width, target_sample_width)
                sample_width = target_sample_width

            return normalized, sample_rate, channels, sample_width
        except DecodingError:
            raise
        except audioop.error as exc:
            raise DecodingError(f"WAV参数转换失败: {exc}") from exc
        except Exception as exc:
            raise DecodingError(f"WAV自适应转换失败: {exc}") from exc
