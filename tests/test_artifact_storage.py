"""Regression tests for untrusted names and filesystem write boundaries."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from goldenage.adapters.artifact_storage import LocalArtifactStore


@pytest.mark.parametrize(
    "name",
    [
        "mail.msg",
        "../outside.msg",
        "../../outside.msg",
        "/tmp/outside.msg",
        r"C:\Windows\outside.msg",
        r"\\server\share\outside.msg",
        r"..\outside.msg",
        "folder/mail.msg",
        "混合∕∖\u202email.msg",
        "\x00.msg",
        "",
    ],
)
def test_client_name_is_only_metadata(tmp_path: Path, name: str) -> None:
    root = tmp_path / "artifacts"
    artifact_id = uuid4()
    key = LocalArtifactStore(root).store(artifact_id, name, b"original\r\n\x00\xff")

    assert Path(key) == root / f"{artifact_id}.bin"
    assert Path(key).read_bytes() == b"original\r\n\x00\xff"
    assert list(root.iterdir()) == [Path(key)]


def test_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    artifact_id = uuid4()
    key = store.store(artifact_id, "first.msg", b"first")

    with pytest.raises(FileExistsError):
        store.store(artifact_id, "second.msg", b"second")

    assert Path(key).read_bytes() == b"first"


@pytest.mark.parametrize("dangling", [False, True])
def test_file_symlink_does_not_redirect_writes(tmp_path: Path, dangling: bool) -> None:
    outside = tmp_path / "outside.msg"
    if not dangling:
        outside.write_bytes(b"unchanged")
    root = tmp_path / "artifacts"
    store = LocalArtifactStore(root)
    artifact_id = uuid4()
    (root / f"{artifact_id}.bin").symlink_to(outside)

    with pytest.raises(FileExistsError):
        store.store(artifact_id, "mail.msg", b"bad")

    assert outside.read_bytes() == b"unchanged" if not dangling else not outside.exists()


def test_root_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "artifacts"
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        LocalArtifactStore(root)

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("symlink", [False, True])
def test_replaced_root_is_rejected(tmp_path: Path, symlink: bool) -> None:
    root = tmp_path / "artifacts"
    store = LocalArtifactStore(root)
    root.rename(tmp_path / "original")
    outside = tmp_path / "outside"
    outside.mkdir()
    if symlink:
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir()

    with pytest.raises(OSError):
        store.store(uuid4(), "mail.msg", b"bad")

    assert list(root.iterdir()) == []
    assert list(outside.iterdir()) == []


def test_legacy_directory_symlink_is_not_used(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = LocalArtifactStore(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    artifact_id = uuid4()
    (root / str(artifact_id)).symlink_to(outside, target_is_directory=True)

    key = store.store(artifact_id, "mail.msg", b"original")

    assert Path(key).read_bytes() == b"original"
    assert list(outside.iterdir()) == []


def test_legacy_storage_key_remains_readable(tmp_path: Path) -> None:
    artifact_id = uuid4()
    old_key = tmp_path / str(artifact_id) / "original.msg"
    old_key.parent.mkdir()
    old_key.write_bytes(b"legacy")

    new_key = LocalArtifactStore(tmp_path).store(uuid4(), "new.msg", b"new")

    assert old_key.read_bytes() == b"legacy"
    assert Path(new_key).read_bytes() == b"new"


def test_concurrent_writers_do_not_overwrite_each_other(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    artifact_id = uuid4()

    def write(value: int) -> bytes | None:
        content = bytes([value]) * 1000
        try:
            store.store(artifact_id, "mail.msg", content)
        except FileExistsError:
            return None
        return content

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(8)))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert (tmp_path / f"{artifact_id}.bin").read_bytes() == winners[0]


@pytest.mark.parametrize("failure", ["write", "fsync"])
def test_failed_write_removes_partial_file(tmp_path: Path, monkeypatch, failure: str) -> None:
    store = LocalArtifactStore(tmp_path)
    original_write = os.write

    def fail(fd: int, *args) -> int:
        original_write(fd, b"partial")
        raise OSError("disk failure")

    monkeypatch.setattr(os, failure, fail)
    with pytest.raises(OSError, match="disk failure"):
        store.store(uuid4(), "mail.msg", b"content")

    assert list(tmp_path.iterdir()) == []


def test_short_writes_preserve_all_bytes(tmp_path: Path, monkeypatch) -> None:
    original_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: original_write(fd, data[:2]))
    key = LocalArtifactStore(tmp_path).store(uuid4(), "mail.msg", b"123456789")
    assert Path(key).read_bytes() == b"123456789"


def test_invalid_id_cannot_be_used_as_a_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LocalArtifactStore(tmp_path).store("../outside", "mail.msg", b"bad")  # ty: ignore[invalid-argument-type]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_new_directories_and_files_are_private(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "artifacts"
    key = LocalArtifactStore(root).store(uuid4(), "mail.msg", b"content")
    assert root.stat().st_mode & 0o777 == 0o700
    assert root.parent.stat().st_mode & 0o777 == 0o700
    assert Path(key).stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative open")
def test_root_swap_before_file_open_cannot_redirect_write(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "artifacts"
    store = LocalArtifactStore(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    saved = tmp_path / "saved"
    original_open = os.open
    artifact_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    def swap_then_open(path, flags, mode=0o777, *, dir_fd=None):
        if flags & os.O_CREAT:
            root.rename(saved)
            root.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_then_open)
    store.store(artifact_id, "mail.msg", b"content")

    assert list(outside.iterdir()) == []
    assert (saved / f"{artifact_id}.bin").read_bytes() == b"content"
