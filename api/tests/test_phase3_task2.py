"""Phase 3 Task 2 tests: ffprobe audio validation, private storage, isolation, and safe deletion."""

import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.cases import service
from app.core.database import Base, ensure_default_worker
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase, RoutingChannel  # noqa: F401
from app.models.voice import CaseExtractionDraft, VoiceRecording
from app.models.worker import WorkerProfile  # noqa: F401
from app.routing.matcher import ensure_routing_seed
from app.schemas.case import CreateCaseRequest, PaymentDiscrepancyDetails
from app.storage import audio_validator
from app.storage.audio_validator import AudioValidationError, validate_audio_bytes
from app.storage.local_store import FileValidationError, LocalStorageService
from app.voice.storage import (
    delete_case_recording,
    get_recording_for_case,
    resolve_recording_audio_path,
    serialize_voice_recording,
    store_case_recording,
)

PNG_HEADER_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _make_fk_session_factory(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fks(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _generate_audio_bytes(tmp_path, filename: str, *, duration_sec: float, extra_args: list[str] | None = None) -> bytes:
    target = tmp_path / filename
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration_sec}",
        *(extra_args or []),
        str(target),
    ]
    subprocess.run(cmd, check=True)
    return target.read_bytes()


def test_valid_supported_audio_formats_measured_and_stored_safely(tmp_path):
    """Valid WebM/Opus, WAV, OGG, and MP3 clips pass real decode validation and never expose disk paths."""
    SessionFactory = _make_fk_session_factory(tmp_path / "task2_valid.db")
    storage = LocalStorageService(str(tmp_path / "private_store"), max_upload_mb=10, max_audio_duration_seconds=30.0)

    samples = [
        ("clip.webm", ["-c:a", "libopus"], "audio/webm", "opus"),
        ("clip.wav", [], "audio/wav", "pcm_s16le"),
        ("clip.ogg", ["-c:a", "libopus"], "audio/ogg", "opus"),
        ("clip.mp3", ["-c:a", "libmp3lame"], "audio/mpeg", "mp3"),
    ]

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="swiggy",
                details=PaymentDiscrepancyDetails(platform="swiggy", issue_description="Missing bonus"),
            ),
        )

        for idx, (fname, args, expected_mime, expected_codec) in enumerate(samples, start=1):
            audio_bytes = _generate_audio_bytes(tmp_path, fname, duration_sec=2.0, extra_args=args)
            rec = store_case_recording(
                db,
                case=case,
                storage=storage,
                raw_bytes=audio_bytes,
                original_filename=fname,
                declared_content_type=expected_mime,
            )
            assert rec.recording_order == idx
            assert rec.mime_type == expected_mime
            assert rec.codec_name == expected_codec
            assert 1.5 <= rec.measured_duration_sec <= 2.5
            assert rec.asr_provider == "none"
            assert rec.transcription_status == "pending"

            resolved = resolve_recording_audio_path(storage, case_id=case.id, recording=rec)
            assert resolved.exists()
            assert f"/{case.id}/audio/{rec.id}" in resolved.as_posix()

            serialized = serialize_voice_recording(rec)
            assert "storage_key" not in serialized
            assert str(tmp_path) not in str(serialized)


def test_invalid_corrupted_and_spoofed_audio_rejected(tmp_path):
    """Empty, text, corrupted bytes, video streams, and spoofed extensions/MIME headers are rejected."""
    with pytest.raises(AudioValidationError, match="empty"):
        validate_audio_bytes(b"")

    with pytest.raises(AudioValidationError):
        validate_audio_bytes(b"Not real audio bytes", declared_content_type="audio/wav")

    # Corrupt truncated WAV header with fake declared audio MIME
    with pytest.raises(AudioValidationError):
        validate_audio_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ", declared_content_type="audio/wav")

    # PNG bytes disguised as .wav with audio/wav MIME
    with pytest.raises(AudioValidationError):
        validate_audio_bytes(PNG_HEADER_BYTES, declared_content_type="audio/wav")

    # Real WAV bytes declared with non-audio MIME type
    real_wav = _generate_audio_bytes(tmp_path, "real.wav", duration_sec=1.0)
    with pytest.raises(AudioValidationError, match="not an audio format"):
        validate_audio_bytes(real_wav, declared_content_type="image/png")

    # Valid WAV disguised with .mp3 filename & generic octet-stream still detects real WAV format
    validated = validate_audio_bytes(real_wav, declared_content_type="application/octet-stream")
    assert validated.mime_type == "audio/wav"
    assert validated.extension == ".wav"

    # Video stream inside WebM container must be rejected
    video_webm = tmp_path / "video.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            str(video_webm),
        ],
        check=True,
    )
    with pytest.raises(AudioValidationError, match="video streams are rejected"):
        validate_audio_bytes(video_webm.read_bytes(), declared_content_type="audio/webm")


def test_oversized_and_overduration_audio_rejected(tmp_path):
    """Files exceeding 10 MB or 30 seconds are rejected cleanly."""
    # Exceeds 10 MB application limit
    oversized = b"\x00" * (10 * 1024 * 1024 + 1)
    with pytest.raises(AudioValidationError, match="10 MB"):
        validate_audio_bytes(oversized, max_upload_mb=10, max_duration_seconds=30.0)

    # Exceeds 30 second duration limit
    long_ogg = _generate_audio_bytes(tmp_path, "long.ogg", duration_sec=32.0, extra_args=["-c:a", "libopus"])
    with pytest.raises(AudioValidationError, match="30-second maximum limit"):
        validate_audio_bytes(long_ogg, max_upload_mb=10, max_duration_seconds=30.0)


def test_ffprobe_and_ffmpeg_failures_handled_safely(tmp_path, monkeypatch):
    """Missing, timing-out, or failing ffprobe subprocesses raise AudioValidationError safely."""
    real_wav = _generate_audio_bytes(tmp_path, "probe_err.wav", duration_sec=1.0)

    def _missing_tool(*args, **kwargs):
        raise FileNotFoundError("ffprobe not found")

    monkeypatch.setattr(audio_validator.subprocess, "run", _missing_tool)
    with pytest.raises(AudioValidationError, match="ffprobe"):
        validate_audio_bytes(real_wav)

    def _timeout_tool(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=10)

    monkeypatch.setattr(audio_validator.subprocess, "run", _timeout_tool)
    with pytest.raises(AudioValidationError, match="Timed out"):
        validate_audio_bytes(real_wav)


def test_case_isolation_safe_filenames_and_path_traversal_prevention(tmp_path):
    """Storage isolates recordings per case, ignores hostile filenames, and blocks traversal."""
    SessionFactory = _make_fk_session_factory(tmp_path / "task2_iso.db")
    storage = LocalStorageService(str(tmp_path / "private_store"), max_upload_mb=10, max_audio_duration_seconds=30.0)
    wav_bytes = _generate_audio_bytes(tmp_path, "iso.wav", duration_sec=1.0)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case_a = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="uber",
                details=PaymentDiscrepancyDetails(platform="uber", issue_description="Case A"),
            ),
        )
        case_b = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="rapido",
                details=PaymentDiscrepancyDetails(platform="rapido", issue_description="Case B"),
            ),
        )

        rec_a = store_case_recording(
            db,
            case=case_a,
            storage=storage,
            raw_bytes=wav_bytes,
            original_filename="../../etc/passwd.wav",
            declared_content_type="audio/wav",
        )
        resolved = resolve_recording_audio_path(storage, case_id=case_a.id, recording=rec_a)
        assert resolved.name == f"{rec_a.id}.wav"
        assert "etc" not in resolved.parts

        # Cross-case DB and path access blocked
        assert get_recording_for_case(db, case_id=case_b.id, recording_id=rec_a.id) is None
        with pytest.raises(FileValidationError, match="Cross-case"):
            resolve_recording_audio_path(storage, case_id=case_b.id, recording=rec_a)

        # Path traversal attempts blocked
        for bad_key in ("../escape.wav", "/etc/passwd", "a/../../secret.wav", "a\\b.wav"):
            with pytest.raises(FileValidationError):
                storage.resolve_safe_path(bad_key)


def test_recording_deletion_isolation_and_missing_file_safety(tmp_path):
    """Deleting one recording keeps sibling recordings, evidence, and drafts intact; missing files do not crash."""
    SessionFactory = _make_fk_session_factory(tmp_path / "task2_del.db")
    storage = LocalStorageService(str(tmp_path / "private_store"), max_upload_mb=10, max_audio_duration_seconds=30.0)
    wav_bytes = _generate_audio_bytes(tmp_path, "del.wav", duration_sec=1.0)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="swiggy",
                details=PaymentDiscrepancyDetails(platform="swiggy", issue_description="Deletion test"),
            ),
        )

        rec1 = store_case_recording(
            db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="clip1.wav"
        )
        rec2 = store_case_recording(
            db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="clip2.wav"
        )
        ev = service.add_evidence_item(
            db,
            case=case,
            storage=storage,
            evidence_category="payout_summary",
            raw_bytes=PNG_HEADER_BYTES,
            original_filename="screen.png",
            declared_content_type="image/png",
            worker_note=None,
        )
        draft = CaseExtractionDraft(
            id=str(uuid.uuid4()),
            case_id=case.id,
            voice_recording_id=rec1.id,
            source_transcript_layer="worker_edited_transcript",
            source_transcript_snapshot="Sample snapshot",
            llm_provider="none",
            extraction_status="key_not_set",
        )
        db.add(draft)
        db.commit()

        path1 = resolve_recording_audio_path(storage, case_id=case.id, recording=rec1)
        path2 = resolve_recording_audio_path(storage, case_id=case.id, recording=rec2)
        ev_path = storage.resolve_case_scoped_path(case.id, ev.storage_key)
        assert path1.exists() and path2.exists() and ev_path.exists()

        # Delete recording 1 only
        delete_case_recording(db, case=case, recording=rec1, storage=storage)
        assert not path1.exists()
        assert path2.exists()
        assert ev_path.exists()

        reloaded_draft = db.scalar(select(CaseExtractionDraft).where(CaseExtractionDraft.id == draft.id))
        assert reloaded_draft is not None
        assert reloaded_draft.voice_recording_id is None

        # Simulate missing file on disk before deleting recording 2 -> must not crash
        path2.unlink()
        delete_case_recording(db, case=case, recording=rec2, storage=storage)
        assert get_recording_for_case(db, case_id=case.id, recording_id=rec2.id) is None

        db.refresh(case)
        event_types = [e.event_type for e in case.timeline_events]
        assert event_types.count("voice_recorded") == 2
        assert event_types.count("voice_removed") == 2

        # Case deletion cleans up remaining evidence file
        service.delete_case_and_files(db, case=case, storage=storage)
        assert not ev_path.exists()
