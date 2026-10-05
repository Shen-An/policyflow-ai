"""T091 [US1] malicious-upload defence: magic bytes, type, size, archive bombs.

Pure tests on real bytes (real zip files built in-memory), no sandbox runtime
needed. The threat model is an upload that lies about what it is -- a script named
``.pdf``, a few-KB zip that expands to fill a disk, a deeply nested archive -- and
the guard is that each lie is refused before the bytes are ingested or indexed.
"""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from backend.app.sandbox.validation import (
    MAX_ARCHIVE_DEPTH,
    MAX_ARCHIVE_ENTRIES,
    MAX_COMPRESSION_RATIO,
    ValidationError,
    detect_media_type,
    inspect_archive,
    validate_upload,
)

PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _zip(entries: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return buffer.getvalue()


# -- magic bytes vs declared type --------------------------------------------


def test_matching_magic_bytes_pass(workspace_free=None) -> None:
    assert detect_media_type(PDF_BYTES, "application/pdf") == "application/pdf"
    assert detect_media_type(PNG_BYTES, "image/png") == "image/png"


def test_a_script_named_pdf_is_refused() -> None:
    script = b"#!/bin/sh\nrm -rf /\n"
    with pytest.raises(ValidationError, match="MEDIA_TYPE_MISMATCH"):
        detect_media_type(script, "application/pdf")


def test_text_type_must_actually_be_text() -> None:
    assert detect_media_type(b"hello, policy", "text/plain") == "text/plain"
    with pytest.raises(ValidationError, match="MEDIA_TYPE_MISMATCH"):
        detect_media_type(b"\xff\xfe\x00binary", "text/plain")
    with pytest.raises(ValidationError, match="MEDIA_TYPE_MISMATCH"):
        detect_media_type(b"has a \x00 nul", "text/plain")


def test_unknown_binary_type_is_refused_not_trusted() -> None:
    with pytest.raises(ValidationError, match="MEDIA_TYPE_UNSUPPORTED"):
        detect_media_type(b"\x00\x01\x02", "application/x-unknown")


# -- size and hash -----------------------------------------------------------


def test_oversize_upload_is_refused() -> None:
    with pytest.raises(ValidationError, match="UPLOAD_TOO_LARGE"):
        validate_upload(PDF_BYTES, declared_media_type="application/pdf", max_bytes=4)


def test_empty_upload_is_refused() -> None:
    with pytest.raises(ValidationError, match="UPLOAD_EMPTY"):
        validate_upload(b"", declared_media_type="application/pdf", max_bytes=1024)


def test_hash_mismatch_is_refused() -> None:
    with pytest.raises(ValidationError, match="HASH_MISMATCH"):
        validate_upload(
            PDF_BYTES,
            declared_media_type="application/pdf",
            max_bytes=1024,
            expected_sha256="f" * 64,
        )


def test_matching_hash_passes() -> None:
    validate_upload(
        PDF_BYTES,
        declared_media_type="application/pdf",
        max_bytes=1024,
        expected_sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
    )


# -- archive bombs -----------------------------------------------------------


def test_a_normal_archive_passes() -> None:
    data = _zip({"a.txt": b"hello", "b.txt": b"world"})
    report = inspect_archive(data)
    assert report.entries == 2
    assert "a.txt" in report.paths


def test_a_high_ratio_entry_is_refused() -> None:
    # Highly compressible payload: a megabyte of zeros compresses tiny.
    bomb = _zip({"bomb.bin": b"\x00" * (4 * 1024 * 1024)})
    with pytest.raises(ValidationError, match="RATIO_SUSPICIOUS|EXPANDS_TOO_LARGE|ENTRY_TOO_LARGE"):
        inspect_archive(bomb)


def test_too_many_entries_is_refused() -> None:
    entries = {f"f{i}.txt": b"x" for i in range(MAX_ARCHIVE_ENTRIES + 1)}
    with pytest.raises(ValidationError, match="TOO_MANY_ENTRIES"):
        inspect_archive(_zip(entries, compression=zipfile.ZIP_STORED))


def test_nested_archive_beyond_depth_is_refused() -> None:
    # Build archives nested one level deeper than the allowed depth.
    payload = _zip({"leaf.txt": b"deep"}, compression=zipfile.ZIP_STORED)
    for level in range(MAX_ARCHIVE_DEPTH + 1):
        payload = _zip({f"layer{level}.zip": payload}, compression=zipfile.ZIP_STORED)
    with pytest.raises(ValidationError, match="TOO_DEEP"):
        inspect_archive(payload)


def test_archive_entry_with_traversal_name_is_refused() -> None:
    # A zip whose entry name tries to escape on extraction.
    data = _zip({"../../evil.txt": b"x"}, compression=zipfile.ZIP_STORED)
    with pytest.raises(ValidationError, match="TRAVERSAL|ESCAPE|PATH"):
        inspect_archive(data)


def test_validate_upload_runs_the_archive_walk_for_zip_types() -> None:
    bomb = _zip({"bomb.bin": b"\x00" * (4 * 1024 * 1024)})
    with pytest.raises(ValidationError):
        validate_upload(
            bomb,
            declared_media_type="application/zip",
            max_bytes=64 * 1024 * 1024,
        )


def test_compression_ratio_bound_is_the_declared_one() -> None:
    assert MAX_COMPRESSION_RATIO >= 1
    # A docx is a zip; a legitimate one with modest compression must pass.
    docx_like = _zip(
        {"word/document.xml": b"<xml>" + b"policy text " * 100 + b"</xml>"},
        compression=zipfile.ZIP_DEFLATED,
    )
    report = inspect_archive(docx_like)
    assert report.entries == 1
