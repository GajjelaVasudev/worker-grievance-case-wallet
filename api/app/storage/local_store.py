"""Safe local volume storage with magic-byte MIME validation and SHA-256 integrity."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import uuid

from app.storage.audio_validator import AudioValidationError, validate_audio_bytes

ALLOWED_EVIDENCE_FORMATS: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}

_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class FileValidationError(ValueError):
    """Raised when an uploaded evidence file violates format or size rules."""


@dataclass(frozen=True)
class StoredFile:
    """Metadata returned after persisting a validated evidence upload."""

    storage_key: str
    mime_type: str
    size_bytes: int
    sha256_hash: str
    sanitized_filename: str


@dataclass(frozen=True)
class StoredAudioFile:
    """Metadata returned after persisting a validated audio recording."""

    storage_key: str
    mime_type: str
    codec_name: str
    duration_sec: float
    size_bytes: int
    sha256_hash: str
    sanitized_filename: str


def detect_magic_mime(data: bytes) -> str | None:
    """Detect supported evidence MIME type strictly from file magic bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def sanitize_filename(raw_name: str | None) -> str:
    """Sanitize original filename for safe metadata display only."""
    basename = Path(raw_name or "evidence").name
    cleaned = _SAFE_SEGMENT_RE.sub("_", basename).strip("._")
    return cleaned[:120] or "evidence"


class LocalStorageService:
    """Stores evidence and audio files safely inside the configured root volume."""

    def __init__(
        self,
        root_dir: str,
        max_upload_mb: int = 10,
        max_audio_duration_seconds: float = 30.0,
    ) -> None:
        self._root = Path(root_dir).resolve()
        self._max_upload_mb = max_upload_mb
        self._max_bytes = max_upload_mb * 1024 * 1024
        self._max_audio_duration_seconds = max_audio_duration_seconds

    def save_evidence_bytes(
        self,
        *,
        case_id: str,
        evidence_id: str,
        raw_bytes: bytes,
        original_filename: str | None,
        declared_content_type: str | None = None,
    ) -> StoredFile:
        """Validate magic bytes and size, then write file under UUID key."""
        if not raw_bytes:
            raise FileValidationError("Uploaded file is empty")
        if len(raw_bytes) > self._max_bytes:
            raise FileValidationError(f"File exceeds maximum allowed size of {self._max_bytes // (1024 * 1024)} MB")

        detected_mime = detect_magic_mime(raw_bytes)
        if detected_mime is None or detected_mime not in ALLOWED_EVIDENCE_FORMATS:
            raise FileValidationError(
                "Unsupported file format. Only PNG, JPEG, WebP screenshots and PDF documents are allowed."
            )

        if declared_content_type and declared_content_type != "application/octet-stream":
            normalized_declared = declared_content_type.split(";")[0].strip().lower()
            if normalized_declared == "image/jpg":
                normalized_declared = "image/jpeg"
            if normalized_declared in ALLOWED_EVIDENCE_FORMATS and normalized_declared != detected_mime:
                raise FileValidationError("Declared file type does not match actual file contents.")

        safe_case = _SAFE_SEGMENT_RE.sub("", case_id) or str(uuid.uuid4())
        ext = ALLOWED_EVIDENCE_FORMATS[detected_mime]
        storage_key = f"{safe_case}/{evidence_id}{ext}"
        target_path = self.resolve_safe_path(storage_key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(raw_bytes)

        digest = hashlib.sha256(raw_bytes).hexdigest()
        return StoredFile(
            storage_key=storage_key,
            mime_type=detected_mime,
            size_bytes=len(raw_bytes),
            sha256_hash=digest,
            sanitized_filename=sanitize_filename(original_filename),
        )

    def save_audio_bytes(
        self,
        *,
        case_id: str,
        recording_id: str,
        raw_bytes: bytes,
        original_filename: str | None,
        declared_content_type: str | None = None,
    ) -> StoredAudioFile:
        """Validate actual audio stream/duration/size and save privately per case."""
        validated = validate_audio_bytes(
            raw_bytes,
            max_upload_mb=self._max_upload_mb,
            max_duration_seconds=self._max_audio_duration_seconds,
            declared_content_type=declared_content_type,
        )
        safe_case = self._safe_id_segment(case_id, "case_id")
        safe_recording = self._safe_id_segment(recording_id, "recording_id")
        storage_key = f"{safe_case}/audio/{safe_recording}{validated.extension}"
        target_path = self.resolve_case_scoped_path(safe_case, storage_key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(raw_bytes)

        digest = hashlib.sha256(raw_bytes).hexdigest()
        return StoredAudioFile(
            storage_key=storage_key,
            mime_type=validated.mime_type,
            codec_name=validated.codec_name,
            duration_sec=validated.duration_sec,
            size_bytes=validated.size_bytes,
            sha256_hash=digest,
            sanitized_filename=sanitize_filename(original_filename or f"recording{validated.extension}"),
        )

    @staticmethod
    def _safe_id_segment(raw_id: str, label: str) -> str:
        cleaned = _SAFE_SEGMENT_RE.sub("", (raw_id or "").strip())
        if not cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
            raise FileValidationError(f"Invalid {label}")
        return cleaned

    def resolve_safe_path(self, storage_key: str) -> Path:
        """Resolve a storage key and prevent directory traversal outside root."""
        if not storage_key or "\\" in storage_key or storage_key.startswith("/"):
            raise FileValidationError("Invalid storage key path")
        if ".." in Path(storage_key).parts:
            raise FileValidationError("Invalid storage key path")
        candidate = (self._root / storage_key).resolve()
        if candidate == self._root or self._root not in candidate.parents:
            raise FileValidationError("Invalid storage key path")
        return candidate

    def resolve_case_scoped_path(self, case_id: str, storage_key: str) -> Path:
        """Resolve a storage key strictly confined to the owning case directory."""
        safe_case = self._safe_id_segment(case_id, "case_id")
        candidate = self.resolve_safe_path(storage_key)
        case_root = (self._root / safe_case).resolve()
        if case_root not in candidate.parents:
            raise FileValidationError("Cross-case file access is not permitted")
        return candidate

    def delete_file(self, storage_key: str) -> None:
        """Delete a stored file if it exists on disk without failing when already absent."""
        target = self.resolve_safe_path(storage_key)
        if target.exists() and target.is_file():
            target.unlink()
