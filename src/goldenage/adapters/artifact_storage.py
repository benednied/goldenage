"""Local binary storage with exclusive writes beneath a trusted directory."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import UUID


class LocalArtifactStore:
    """Store original bytes under server-owned names; never use client paths.

    The configured root and its ancestors must be controlled by the application
    owner. POSIX opens are relative to a verified directory descriptor. Windows
    locks each directory against writes and deletion/rename and rejects reparse points.
    """

    def __init__(self, root: Path) -> None:
        # Resolve trusted configuration ancestors once (e.g. macOS /var). Keep
        # the final component unresolved so a symlink at the root is rejected.
        absolute = Path(os.path.abspath(root))
        self._root = absolute.parent.resolve() / absolute.name
        with self._open_root(create=True) as root_fd:
            self._identity = self._root_identity(root_fd)

    def store(self, artifact_id: UUID, file_name: str, content: bytes) -> str:
        del file_name  # Original names belong exclusively in Artifact metadata.
        name = f"{UUID(str(artifact_id))}.bin"
        with self._open_root() as root_fd:
            if self._root_identity(root_fd) != self._identity:
                raise OSError("Artifact storage root was replaced")
            # O_EXCL rejects both existing files and symlinks, including dangling
            # links. Never unlink a target unless this call created it.
            target = self._root / name if root_fd is None else name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            fd = os.open(target, flags, 0o600, dir_fd=root_fd)
            try:
                try:
                    remaining = memoryview(content)
                    while remaining:
                        written = os.write(fd, remaining)
                        if written <= 0:
                            raise OSError("Artifact write made no progress")
                        remaining = remaining[written:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except BaseException:
                os.unlink(target, dir_fd=root_fd)
                raise
        return str(self._root / name)

    def _root_identity(self, root_fd: int | None) -> tuple[int, int]:
        info = os.stat(self._root, follow_symlinks=False) if root_fd is None else os.fstat(root_fd)
        return info.st_dev, info.st_ino

    @contextmanager
    def _open_root(self, *, create: bool = False) -> Iterator[int | None]:
        if os.name == "nt":
            with _locked_windows_root(self._root, create=create):
                yield None
            return
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with ExitStack() as stack:
            directory = os.open(self._root.anchor, flags)
            stack.callback(os.close, directory)
            for part in self._root.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                directory = os.open(part, flags, dir_fd=directory)
                stack.callback(os.close, directory)
            yield directory


@contextmanager
def _locked_windows_root(root: Path, *, create: bool) -> Iterator[None]:
    """Pin Windows path components using handles that deny write/delete sharing.

    pywin32 is an existing Windows dependency. OPEN_REPARSE_POINT opens the
    link itself so junctions and other reparse points can be rejected before
    traversing children. Holding every ancestor prevents path replacement.
    """
    win32file = importlib.import_module("win32file")
    win32con = importlib.import_module("win32con")
    with ExitStack() as stack:
        path = Path(root.anchor)
        for part in ("", *root.parts[1:]):
            path = path / part
            if create and part:
                try:
                    # Python 3.14 applies a private Windows ACL for mode 0700.
                    path.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            handle = win32file.CreateFile(
                str(path),
                win32con.FILE_READ_ATTRIBUTES,
                win32con.FILE_SHARE_READ,
                None,
                win32con.OPEN_EXISTING,
                win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
            stack.callback(handle.Close)
            attributes = win32file.GetFileInformationByHandle(handle)[0]
            if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError("Artifact storage cannot traverse a Windows reparse point")
            if not attributes & win32con.FILE_ATTRIBUTE_DIRECTORY:
                raise NotADirectoryError(str(path))
        yield
