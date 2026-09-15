# Secure local artifact writes

Implements [SEC-02 / issue #3](https://github.com/benednied/goldenage/issues/3),
a P0 root node with no prerequisites in the audit roadmap. Based on GitHub
`master` at `365775603d1ad8e55d116fa52357fb06de403a52`.

## Storage contract and compatibility

`LocalArtifactStore` now lives in `adapters/artifact_storage.py`; the old import
from `adapters/demo.py` remains available. The application generates a UUID for
every new artifact. The store validates that ID and creates `<uuid>.bin` directly
inside `GOLDENAGE_ARTIFACT_DIR`. The fixed extension deliberately makes no claim
about file format: manual uploads contain MSG bytes, while mailbox intake can
supply other serialized original content. The original filename remains in
`Artifact.file_name` and is still passed to extraction. No client-controlled
filename, extension, separator, or Unicode character affects the storage path.

Storage keys remain filesystem path strings. Existing `<uuid>/<original-name>`
files and database references are neither moved nor rewritten; no schema change
or migration is required. Case panels display persisted extracted content and
escaped original names. There is no binary artifact download route today, so
there is no new Content-Disposition header or raw-file read surface in this fix.

## Filesystem boundary

Configure a local storage directory whose ownership and parent directories are
controlled by the application operator. Existing directory permissions/ACLs are
not changed automatically. Trusted configuration ancestors are resolved once at
initialization (including system aliases such as macOS `/var`); a symlink at the
storage root itself is rejected. Each operation opens all canonical components
again and verifies the original root's device/inode identity. Missing roots are
only created at initialization, with private permissions.

- On macOS/Linux, directory opens use `O_DIRECTORY | O_NOFOLLOW`. File creation
  and failure cleanup are relative to the opened root descriptor, so replacing a
  pathname with a symlink cannot redirect them. New directories use mode 0700
  and new files 0600, further restricted by the process umask.
- On Windows, the existing pywin32 dependency opens each directory with
  `FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT`, rejects reparse
  points (including junctions), and holds all ancestor handles with read sharing
  only. This prevents write/delete/rename handles from replacing or converting
  directory components while the file is created. Python 3.14's `mkdir(0700)`
  creates private Windows directories; files inherit their directory ACL.
  Binary mode preserves the original bytes.
- On both platforms, exclusive creation refuses existing files, directories,
  symlinks, and hard links. A collision raises `FileExistsError`; it never
  overwrites, deletes, or retries against the existing object. One concurrent
  write for a given ID can succeed. Different IDs are independent.
- Short writes are completed in a loop. Zero-progress writes and write/sync
  errors close the file and remove the newly created partial file. Handles close
  on all normal exception paths. Files are synced before a storage key returns.
  Abrupt process termination or cleanup failure can leave an orphan and is not a
  transaction with the later database write. Database/extraction rollback is
  outside this store fix.

An actor with the application's OS identity or administrative control can move
or alter owned storage independently of the application. The store does not
claim to defend against that authority. A POSIX descriptor keeps pointing at its
original directory if it is renamed; the race regression verifies that writes
never follow an injected symlink to an outside target. Operators must keep the
configured root stable for stored path strings to remain usable. Network
filesystems with weaker exclusive-create/sharing semantics are not supported by
this security contract.

The platform mechanisms follow the official
[Python filesystem API](https://docs.python.org/3.14/library/os.html) and
[Windows CreateFile sharing/reparse rules](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew).

## Validation

Store regressions cover POSIX/Windows/UNC and Unicode client names, unchanged
bytes, existing and dangling symlinks, replaced roots, legacy references,
concurrent collisions, permissions, short writes, write/sync failure cleanup,
closed file descriptors, stalled writes, and a deterministic POSIX path-swap
race. HTTP regression tests confirm original names remain metadata, HTML names
are escaped, and upload bytes are stored under the server key. Existing tests
cover upload, assignment, and case-content display.

Local verification on macOS with Python 3.14:

```sh
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev ty check
uv run --extra dev pytest -q
```

Result: Ruff, formatting, and type checks passed; pytest reported **123 passed,
1 skipped**. Existing FastAPI lifecycle deprecation warnings remain unchanged.

The native Windows sharing regression is platform-gated and must also be run on
Windows with pywin32 installed. macOS cannot validate Windows kernel/ACL behavior.
Upload byte/content limits and profile-image storage remain separate work in
[SEC-03 / issue #4](https://github.com/benednied/goldenage/issues/4).
