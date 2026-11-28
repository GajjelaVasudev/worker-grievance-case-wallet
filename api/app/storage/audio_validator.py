"""Content-based audio validation using ffprobe and ffmpeg stream decoding."""

from dataclasses import dataclass
import json
import math
import subprocess
import tempfile
from pathlib import Path


class AudioValidationError(ValueError):
    """Raised when uploaded audio violates format, integrity, size, or duration rules."""


@dataclass(frozen=True)
class ValidatedAudio:
    """Verified audio stream properties measured from actual decoded audio."""

    mime_type: str
    extension: str
    codec_name: str
    duration_sec: float
    size_bytes: int


_CONTAINER_RULES: list[tuple[str, set[str], str, str]] = [
    ("webm", {"opus", "vorbis"}, "audio/webm", ".webm"),
    ("matroska", {"opus", "vorbis"}, "audio/webm", ".webm"),
    ("ogg", {"opus", "vorbis", "flac"}, "audio/ogg", ".ogg"),
    ("wav", set(), "audio/wav", ".wav"),
    ("mp3", {"mp3"}, "audio/mpeg", ".mp3"),
    ("mov,mp4,m4a,3gp,3g2,mj2", {"aac", "mp3", "opus", "flac", "alac"}, "audio/mp4", ".m4a"),
    ("mp4", {"aac", "mp3", "opus", "flac", "alac"}, "audio/mp4", ".m4a"),
    ("m4a", {"aac", "mp3", "opus", "flac", "alac"}, "audio/mp4", ".m4a"),
    ("flac", {"flac"}, "audio/flac", ".flac"),
    ("aac", {"aac"}, "audio/aac", ".aac"),
]

_MIME_ALIASES = {
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/mp3": "audio/mpeg",
    "audio/x-mp3": "audio/mpeg",
    "audio/mpeg3": "audio/mpeg",
    "audio/x-mpeg-3": "audio/mpeg",
    "audio/m4a": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/opus": "audio/ogg",
    "audio/x-flac": "audio/flac",
    "audio/x-aac": "audio/aac",
}


def _normalize_declared_mime(declared_content_type: str | None) -> str | None:
    if not declared_content_type:
        return None
    base = declared_content_type.split(";")[0].strip().lower()
    if not base or base == "application/octet-stream":
        return None
    return _MIME_ALIASES.get(base, base)


def _match_supported_format(format_name: str, codec_name: str) -> tuple[str, str] | None:
    format_tokens = {token.strip().lower() for token in format_name.split(",") if token.strip()}
    normalized_codec = codec_name.strip().lower()

    for container_key, allowed_codecs, mime_type, ext in _CONTAINER_RULES:
        container_tokens = {token.strip().lower() for token in container_key.split(",")}
        if not format_tokens.intersection(container_tokens):
            continue
        if container_key == "wav" and normalized_codec.startswith("pcm_"):
            return mime_type, ext
        if normalized_codec in allowed_codecs:
            return mime_type, ext
    return None


def _parse_float(value: object) -> float | None:
    if value is None or value == "N/A":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _run_ffprobe(file_path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration",
        "-show_entries",
        "stream=codec_type,codec_name,duration",
        "-of",
        "json",
        str(file_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except FileNotFoundError as exc:
        raise AudioValidationError("Audio inspection tooling (ffprobe) is unavailable.") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioValidationError("Timed out while inspecting uploaded audio.") from exc
    except OSError as exc:
        raise AudioValidationError("Unable to inspect uploaded audio.") from exc

    if proc.returncode != 0:
        raise AudioValidationError("Uploaded file is not a valid or supported audio recording.")

    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AudioValidationError("Could not parse audio stream metadata.") from exc


def _decode_audio_duration(file_path: Path, *, max_duration_seconds: float) -> float:
    decode_limit = max_duration_seconds + 1.0
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(file_path),
        "-map",
        "0:a:0",
        "-t",
        f"{decode_limit:.3f}",
        "-progress",
        "pipe:1",
        "-nostats",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except FileNotFoundError as exc:
        raise AudioValidationError("Audio decoding tooling (ffmpeg) is unavailable.") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioValidationError("Timed out while decoding uploaded audio.") from exc
    except OSError as exc:
        raise AudioValidationError("Unable to decode uploaded audio.") from exc

    if proc.returncode != 0 or (proc.stderr and proc.stderr.strip()):
        raise AudioValidationError("Audio content is corrupted or could not be decoded cleanly.")

    last_us: int | None = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("out_time_us="):
            raw_val = line.split("=", 1)[1].strip()
            try:
                last_us = int(raw_val)
            except ValueError:
                continue

    if last_us is None or last_us <= 0:
        raise AudioValidationError("Uploaded audio contains no decodable audio frames.")

    return round(last_us / 1_000_000.0, 3)


def validate_audio_bytes(
    raw_bytes: bytes,
    *,
    max_upload_mb: int = 10,
    max_duration_seconds: float = 30.0,
    declared_content_type: str | None = None,
) -> ValidatedAudio:
    """Validate audio bytes strictly by actual container, codec, decodability, size, and duration."""
    if not raw_bytes:
        raise AudioValidationError("Uploaded audio file is empty.")

    max_bytes = max_upload_mb * 1024 * 1024
    if len(raw_bytes) > max_bytes:
        raise AudioValidationError(f"Audio file exceeds the {max_upload_mb} MB maximum upload limit.")

    normalized_declared = _normalize_declared_mime(declared_content_type)
    if normalized_declared is not None and not normalized_declared.startswith("audio/"):
        raise AudioValidationError("Uploaded content type is not an audio format.")

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_path = Path(tmp.name)

        probe_data = _run_ffprobe(tmp_path)
        streams = probe_data.get("streams") or []
        if not streams:
            raise AudioValidationError("No media streams found in uploaded audio file.")

        if any(stream.get("codec_type") != "audio" for stream in streams):
            raise AudioValidationError("Only audio-only recordings are allowed (video streams are rejected).")

        audio_stream = streams[0]
        codec_name = str(audio_stream.get("codec_name") or "").strip().lower()
        fmt = probe_data.get("format") or {}
        format_name = str(fmt.get("format_name") or "").strip().lower()

        matched = _match_supported_format(format_name, codec_name)
        if matched is None:
            raise AudioValidationError(
                "Unsupported audio container or codec. Supported formats: WebM/Opus, OGG, WAV, MP3, M4A/MP4, FLAC, AAC."
            )
        detected_mime, ext = matched

        header_durations = [
            d
            for d in (
                _parse_float(audio_stream.get("duration")),
                _parse_float(fmt.get("duration")),
            )
            if d is not None
        ]
        if any(d > max_duration_seconds for d in header_durations):
            raise AudioValidationError(
                f"Recording duration exceeds the {int(max_duration_seconds)}-second maximum limit."
            )

        decoded_duration = _decode_audio_duration(tmp_path, max_duration_seconds=max_duration_seconds)
        measured_duration = max([decoded_duration, *header_durations])

        if measured_duration <= 0:
            raise AudioValidationError("Recording has zero playable audio duration.")
        if measured_duration > max_duration_seconds:
            raise AudioValidationError(
                f"Recording duration ({measured_duration:.1f}s) exceeds the {int(max_duration_seconds)}-second maximum limit."
            )

        return ValidatedAudio(
            mime_type=detected_mime,
            extension=ext,
            codec_name=codec_name,
            duration_sec=round(measured_duration, 3),
            size_bytes=len(raw_bytes),
        )
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
