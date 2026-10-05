"""T090 [US1] workspace escape defence: ``..``, absolutes, devices, links, TOCTOU.

Pure filesystem tests on the real host -- no sandbox runtime needed, because
containment must hold before anything runs. Symlink/junction creation needs
privilege on Windows, so those cases skip cleanly when the OS refuses rather than
pretending they passed (same honesty rule as the infra-gated suites).

Every assertion is "the escape is refused", so a regression that lets one through
fails loudly.
"""

from __future__ import annotations

import os

import pytest

from backend.app.sandbox.validation import (
    WorkspaceEscape,
    assert_not_a_link,
    normalize_relative,
    resolve_within,
    safe_input_path,
)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    (root / "forms").mkdir(parents=True)
    (root / "forms" / "a.txt").write_text("inside", encoding="utf-8")
    # A sensitive file *outside* the workspace that escapes must never reach.
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    return root


# -- string-level traversal --------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "../../etc/passwd",
        "forms/../../secret.txt",
        "a/b/../../../secret.txt",
    ],
)
def test_dotdot_traversal_is_refused(workspace, path: str) -> None:
    with pytest.raises(WorkspaceEscape, match="TRAVERSAL|ESCAPE"):
        safe_input_path(workspace, path)


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "/absolute", "C:/Windows/System32", "c:/x", "\\\\server\\share\\x"],
)
def test_absolute_and_unc_paths_are_refused(workspace, path: str) -> None:
    with pytest.raises(WorkspaceEscape, match="ABSOLUTE|TRAVERSAL"):
        safe_input_path(workspace, path)


@pytest.mark.parametrize("name", ["CON", "nul.txt", "COM1", "LPT9.dat", "aux"])
def test_windows_device_names_are_refused(workspace, name: str) -> None:
    with pytest.raises(WorkspaceEscape, match="DEVICE_NAME"):
        normalize_relative(f"forms/{name}")


def test_nul_byte_in_path_is_refused(workspace) -> None:
    with pytest.raises(WorkspaceEscape, match="PATH_INVALID"):
        normalize_relative("forms/a\x00.txt")


def test_a_contained_path_resolves_cleanly(workspace) -> None:
    resolved = safe_input_path(workspace, "forms/a.txt")
    assert resolved.read_text(encoding="utf-8") == "inside"
    # Redundant separators and '.' segments normalise to the same file.
    assert safe_input_path(workspace, "forms/./a.txt") == resolved
    assert safe_input_path(workspace, "forms//a.txt") == resolved


# -- link / junction escape (realpath) ---------------------------------------


def test_symlink_pointing_outside_is_refused(workspace, tmp_path) -> None:
    link = workspace / "forms" / "escape"
    try:
        link.symlink_to(tmp_path / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("creating a symlink requires privilege on this host")
    # realpath containment: the link resolves outside, so it is refused...
    with pytest.raises(WorkspaceEscape, match="ESCAPE|LINK_REJECTED"):
        safe_input_path(workspace, "forms/escape")
    # ...and the link itself is refused as a link regardless of where it points.
    with pytest.raises(WorkspaceEscape, match="LINK_REJECTED"):
        assert_not_a_link(link)


def test_symlink_pointing_inside_is_still_refused_as_a_link(workspace) -> None:
    """A link that resolves *inside* today is still refused (TOCTOU).

    Accepting it would leave a window to repoint it outside between check and use,
    so links are refused outright rather than resolved-and-trusted.
    """
    link = workspace / "forms" / "inside-link"
    try:
        link.symlink_to(workspace / "forms" / "a.txt")
    except (OSError, NotImplementedError):
        pytest.skip("creating a symlink requires privilege on this host")
    with pytest.raises(WorkspaceEscape, match="LINK_REJECTED"):
        assert_not_a_link(link)


def test_linked_parent_directory_cannot_be_used_to_escape(workspace, tmp_path) -> None:
    """A link on a mid-path *directory* is caught, not only on the final file."""
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "target.txt").write_text("outside", encoding="utf-8")
    linked_dir = workspace / "linkeddir"
    try:
        linked_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("creating a directory symlink requires privilege on this host")
    with pytest.raises(WorkspaceEscape, match="ESCAPE|LINK_REJECTED"):
        safe_input_path(workspace, "linkeddir/target.txt")


def test_lnk_shortcut_is_refused(workspace) -> None:
    shortcut = workspace / "forms" / "evil.lnk"
    shortcut.write_bytes(b"L\x00\x00\x00")  # not a real shortcut; the suffix is enough
    with pytest.raises(WorkspaceEscape, match="LINK_REJECTED"):
        assert_not_a_link(shortcut)


# -- TOCTOU ------------------------------------------------------------------


def test_containment_is_rechecked_against_realpath_at_use_time(workspace, tmp_path) -> None:
    """resolve_within uses realpath, so a swapped link cannot pass a stale check."""
    target = workspace / "forms" / "doc.txt"
    target.write_text("ok", encoding="utf-8")
    assert resolve_within(workspace, "forms/doc.txt").exists()

    # Replace the file with a link to outside (the TOCTOU swap).
    target.unlink()
    try:
        target.symlink_to(tmp_path / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("creating a symlink requires privilege on this host")
    # The re-resolution now sees the escape; the earlier success does not persist.
    with pytest.raises(WorkspaceEscape, match="ESCAPE|LINK_REJECTED"):
        safe_input_path(workspace, "forms/doc.txt")


def test_realpath_containment_holds_for_a_sibling_prefix(workspace, tmp_path) -> None:
    """``ws-evil`` must not be accepted as inside ``ws`` by a string-prefix bug."""
    sibling = tmp_path / "ws-evil"
    sibling.mkdir()
    # A path that string-prefix-matches the root but is a different directory.
    with pytest.raises(WorkspaceEscape):
        resolve_within(workspace, "../ws-evil/x.txt")


def test_normalize_relative_is_posix_and_stable(workspace) -> None:
    assert normalize_relative("forms\\a.txt") == "forms/a.txt"
    assert normalize_relative("./forms/./a.txt") == "forms/a.txt"
    assert os.sep in (os.sep,)  # sanity: test runs on this host's os
