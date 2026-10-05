"""T100 [US1] filesystem containment, link/device rejection and archive-bomb defence.

Everything here is pure logic the host can verify -- no sandbox runtime required --
because these are the checks that must hold *before* a byte is written into a
workspace or read back out of one. The threat model is a hostile input manifest
or a hostile processor output trying to read or write outside the workspace.

The guards, and why each exists:

* **realpath containment.** A path is accepted only if its *resolved* location
  (symlinks and ``..`` collapsed, via ``os.path.realpath``) is inside the
  resolved workspace root. Normalising the string is not enough: a symlink that
  points outside collapses to an outside realpath, which is exactly what this
  catches and a string check misses.
* **link / shortcut / mount rejection.** Symlinks, Windows junctions/reparse
  points and ``.lnk`` shortcuts are refused outright on both input staging and
  output collection, so a link can never be followed to escape even if it
  resolves inside today (TOCTOU).
* **device and reserved names.** Windows device names (``CON``, ``NUL``,
  ``COM1``...) and NUL bytes in a path are refused -- they are not files and are a
  classic exfiltration/DoS surface.
* **magic bytes vs declared type.** The leading bytes must match the declared
  media type; a ``.pdf`` that is really a script is refused, so a processor
  cannot be fed something other than what the manifest claims.
* **archive-bomb limits.** Nested-archive depth, total expanded bytes, entry
  count, per-entry compression ratio and individual-entry size are all bounded,
  so a few-KB zip cannot expand into a disk-filling or CPU-burning payload.
* **TOCTOU.** Containment is re-checked against the realpath at use time, not only
  at declaration time, and links are refused rather than resolved.
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# -- limits ------------------------------------------------------------------

#: Maximum nested-archive depth. A zip inside a zip inside a zip beyond this is a
#: bomb vector (each layer multiplies), so it is refused rather than expanded.
MAX_ARCHIVE_DEPTH = 3
#: Maximum total bytes an archive may expand to across all entries and layers.
MAX_TOTAL_EXPANDED_BYTES = 256 * 1024 * 1024
#: Maximum number of entries across an archive (a "zip of a million empty files").
MAX_ARCHIVE_ENTRIES = 10_000
#: Maximum per-entry compression ratio (expanded / compressed). A classic bomb has
#: a ratio in the thousands; legitimate documents rarely exceed ~100.
MAX_COMPRESSION_RATIO = 200
#: Maximum size of any single expanded entry.
MAX_ENTRY_BYTES = 64 * 1024 * 1024

#: Windows reserved device names (case-insensitive, with or without extension).
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

#: Leading byte signatures per media type. A short, high-signal prefix is enough
#: to catch a mislabelled file; it is not a full content-type sniffer.
_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "application/zip": (b"PK\x03\x04", b"PK\x05\x06"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        b"PK\x03\x04",
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}

#: Media types that are plain text: validated by decodability, not a magic prefix.
_TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown", "text/csv"})


class ValidationError(Exception):
    """A file or path violated a containment, type or resource rule.

    ``code`` is a stable short token; the message never includes a host path or
    other tenant data (it names the offending *relative* path only).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class WorkspaceEscape(ValidationError):  # noqa: N818 - reads as the condition
    """A path resolved outside its workspace root, or followed a link."""


@dataclass
class ArchiveReport:
    """What inspecting an archive found."""

    entries: int = 0
    total_expanded_bytes: int = 0
    max_depth: int = 0
    paths: list[str] = field(default_factory=list)


def normalize_relative(path: str) -> str:
    """Normalise a workspace-relative path to a canonical POSIX string.

    Rejects absolute paths, drive letters, NUL bytes, device names and any segment
    that is ``..``. ``..`` is rejected rather than resolved: resolving it here
    could turn ``a/../../x`` into ``x`` and hide an escape, so the only safe
    treatment is refusal. The result has forward slashes and no ``.`` segments.
    """
    if not path or "\x00" in path:
        raise WorkspaceEscape("PATH_INVALID", "a path is empty or contains a NUL byte")
    unified = path.replace("\\", "/").strip()
    if unified.startswith("/"):
        raise WorkspaceEscape("PATH_ABSOLUTE", "an absolute path is not allowed")
    # A Windows drive letter (``c:``) or a UNC prefix is an absolute path too.
    if len(unified) >= 2 and unified[1] == ":":
        raise WorkspaceEscape("PATH_ABSOLUTE", "a drive-letter path is not allowed")
    segments: list[str] = []
    for segment in unified.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise WorkspaceEscape("PATH_TRAVERSAL", "a '..' segment escapes the workspace")
        stem = segment.split(".")[0].casefold()
        if stem in _WINDOWS_DEVICE_NAMES:
            raise WorkspaceEscape(
                "PATH_DEVICE_NAME", f"segment {segment!r} is a reserved device name"
            )
        segments.append(segment)
    if not segments:
        raise WorkspaceEscape("PATH_INVALID", "a path has no usable segments")
    return str(PurePosixPath(*segments))


def resolve_within(root: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` under ``root`` and prove the result is contained.

    This is the authoritative containment check. It normalises the relative path
    (which already refuses ``..``, absolutes and device names), then resolves the
    *real* path and asserts it is the root or inside it. A symlink or junction that
    points outside collapses to an outside realpath here and is refused -- the
    whole reason a realpath check is used instead of a string prefix check.
    """
    normalized = normalize_relative(relative_path)
    real_root = Path(os.path.realpath(root))
    candidate = Path(os.path.realpath(real_root / normalized))
    if candidate != real_root and real_root not in candidate.parents:
        raise WorkspaceEscape(
            "WORKSPACE_ESCAPE",
            f"path {normalized!r} resolves outside the workspace root",
        )
    return candidate


def assert_not_a_link(path: Path) -> None:
    """Refuse a symlink, junction/reparse point or Windows shortcut.

    Links are refused rather than followed so a link that resolves inside *now*
    cannot be swapped to point outside between check and use (TOCTOU). Junctions
    on Windows are detected via the reparse-point attribute, which ``is_symlink``
    does not always report.
    """
    if path.is_symlink():
        raise WorkspaceEscape("LINK_REJECTED", "a symlink is not allowed in a workspace")
    if path.suffix.casefold() == ".lnk":
        raise WorkspaceEscape("LINK_REJECTED", "a Windows shortcut is not allowed")
    if _is_reparse_point(path):
        raise WorkspaceEscape("LINK_REJECTED", "a junction/reparse point is not allowed")


def safe_input_path(root: Path, relative_path: str) -> Path:
    """Resolve a staged *input* path, refusing escapes and links along the way.

    Every ancestor between the root and the file is checked for a link too, so a
    linked *directory* mid-path cannot be used to escape (a link only on the final
    component is not the only vector).
    """
    contained = resolve_within(root, relative_path)
    real_root = Path(os.path.realpath(root))
    cursor = contained
    while cursor != real_root and cursor != cursor.parent:
        if cursor.exists() or cursor.is_symlink():
            assert_not_a_link(cursor)
        cursor = cursor.parent
    return contained


def safe_output_path(root: Path, relative_path: str) -> Path:
    """Resolve a collected *output* path with the same guarantees as inputs.

    Separate name because the two call sites read differently, but the rule is
    identical: a processor's output may not be a link and may not escape.
    """
    return safe_input_path(root, relative_path)


def detect_media_type(data: bytes, declared: str) -> str:
    """Return the declared type if the bytes match it; raise otherwise.

    Text types are validated by decodability (no magic number exists for plain
    text); binary types by a leading signature. A declared type with no known
    signature is accepted on the caller's word only when it is a text type, so a
    binary payload cannot masquerade as an unknown type.
    """
    if declared in _TEXT_MEDIA_TYPES:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                "MEDIA_TYPE_MISMATCH",
                f"declared {declared} but the bytes are not valid UTF-8 text",
            ) from exc
        if b"\x00" in data:
            raise ValidationError(
                "MEDIA_TYPE_MISMATCH", f"declared {declared} but the bytes contain NUL"
            )
        return declared
    signatures = _MAGIC_SIGNATURES.get(declared)
    if signatures is None:
        raise ValidationError(
            "MEDIA_TYPE_UNSUPPORTED", f"no magic-byte signature is known for {declared}"
        )
    if not any(data.startswith(signature) for signature in signatures):
        raise ValidationError(
            "MEDIA_TYPE_MISMATCH",
            f"the bytes do not match the declared media type {declared}",
        )
    return declared


def inspect_archive(
    data: bytes,
    *,
    depth: int = 1,
    accumulator: ArchiveReport | None = None,
) -> ArchiveReport:
    """Walk an archive, enforcing bomb limits at every layer.

    Raises :class:`ValidationError` the moment any limit is exceeded rather than
    after fully expanding, so a bomb is stopped early. Nested archives recurse up
    to :data:`MAX_ARCHIVE_DEPTH`. A non-zip payload returns an empty report (it is
    not an archive, so there is nothing to bound here).
    """
    report = accumulator or ArchiveReport()
    report.max_depth = max(report.max_depth, depth)
    if depth > MAX_ARCHIVE_DEPTH:
        raise ValidationError(
            "ARCHIVE_TOO_DEEP", f"nested archive depth exceeds {MAX_ARCHIVE_DEPTH}"
        )
    if not _looks_like_zip(data):
        return report
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            report.entries += len(infos)
            if report.entries > MAX_ARCHIVE_ENTRIES:
                raise ValidationError(
                    "ARCHIVE_TOO_MANY_ENTRIES",
                    f"archive has more than {MAX_ARCHIVE_ENTRIES} entries",
                )
            for info in infos:
                _check_entry(info)
                report.total_expanded_bytes += info.file_size
                report.paths.append(info.filename)
                if report.total_expanded_bytes > MAX_TOTAL_EXPANDED_BYTES:
                    raise ValidationError(
                        "ARCHIVE_EXPANDS_TOO_LARGE",
                        f"archive expands beyond {MAX_TOTAL_EXPANDED_BYTES} bytes",
                    )
                if info.filename.lower().endswith(".zip") and depth < MAX_ARCHIVE_DEPTH:
                    nested = archive.read(info)
                    inspect_archive(nested, depth=depth + 1, accumulator=report)
                elif info.filename.lower().endswith(".zip"):
                    raise ValidationError(
                        "ARCHIVE_TOO_DEEP",
                        f"nested archive depth exceeds {MAX_ARCHIVE_DEPTH}",
                    )
    except zipfile.BadZipFile as exc:
        raise ValidationError("ARCHIVE_CORRUPT", "the archive could not be read") from exc
    return report


def _check_entry(info: zipfile.ZipInfo) -> None:
    """Enforce per-entry limits and refuse an entry whose name escapes."""
    # A zip entry name is attacker-controlled; refuse traversal/absolute names so
    # extraction (if any) cannot escape, independent of the extractor used.
    normalize_relative(info.filename.rstrip("/") or ".")
    if info.file_size > MAX_ENTRY_BYTES:
        raise ValidationError(
            "ARCHIVE_ENTRY_TOO_LARGE",
            f"entry {info.filename!r} expands beyond {MAX_ENTRY_BYTES} bytes",
        )
    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > MAX_COMPRESSION_RATIO:
            raise ValidationError(
                "ARCHIVE_RATIO_SUSPICIOUS",
                f"entry {info.filename!r} compression ratio {ratio:.0f} exceeds "
                f"{MAX_COMPRESSION_RATIO}",
            )


def validate_upload(
    data: bytes,
    *,
    declared_media_type: str,
    max_bytes: int,
    expected_sha256: str | None = None,
) -> None:
    """Run the full pre-ingest gate over raw upload bytes.

    Order matters: size first (cheap, bounds everything after it), then hash (so a
    mismatch is caught before type sniffing), then magic bytes, then -- for a
    zip-based type -- the archive-bomb walk.
    """
    import hashlib

    if len(data) > max_bytes:
        raise ValidationError(
            "UPLOAD_TOO_LARGE", f"upload is {len(data)} bytes, limit is {max_bytes}"
        )
    if len(data) == 0:
        raise ValidationError("UPLOAD_EMPTY", "upload is empty")
    if expected_sha256 is not None:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256.lower():
            raise ValidationError(
                "UPLOAD_HASH_MISMATCH", "the bytes do not match the declared sha256"
            )
    detect_media_type(data, declared_media_type)
    if _looks_like_zip(data):
        inspect_archive(data)


def _looks_like_zip(data: bytes) -> bool:
    return data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _is_reparse_point(path: Path) -> bool:
    """Best-effort junction/reparse-point detection on Windows."""
    try:
        attributes = os.stat(path, follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False
    reparse_flag = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
    return bool(attributes & reparse_flag)
