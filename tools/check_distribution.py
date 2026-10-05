"""Check that built GoldenAge archives contain all runtime resources."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ARCHIVE_SUFFIXES = (".whl", ".tar.gz")
RESOURCE_DIRECTORIES = (
    Path("web/templates"),
    Path("web/static"),
    Path("resources/sql"),
)


def runtime_resources(package_root: Path) -> tuple[str, ...]:
    """Return package-relative runtime files from the source tree."""
    resources: list[str] = []
    for directory in RESOURCE_DIRECTORIES:
        source_directory = package_root / directory
        if not source_directory.is_dir():
            raise ValueError(f"missing runtime resource directory: {source_directory}")
        resources.extend(
            path.relative_to(package_root).as_posix()
            for path in source_directory.rglob("*")
            if path.is_file()
        )
    return tuple(sorted(resources))


def archive_members(archive: Path) -> tuple[str, ...]:
    """Return POSIX member names from a wheel or source distribution."""
    if archive.name.endswith(".whl"):
        with zipfile.ZipFile(archive) as handle:
            return tuple(handle.namelist())
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, mode="r:gz") as handle:
            return tuple(member.name for member in handle.getmembers())
    raise ValueError(f"unsupported distribution archive: {archive}")


def missing_resources(archive: Path, package_resources: tuple[str, ...]) -> tuple[str, ...]:
    """Return source resource paths that are absent from an archive."""
    members = {PurePosixPath(member) for member in archive_members(archive)}
    is_wheel = archive.name.endswith(".whl")
    missing: list[str] = []
    for resource in package_resources:
        resource_path = PurePosixPath("goldenage", resource)
        if is_wheel:
            present = resource_path in members
        else:
            present = any(
                member.parts[-len(resource_path.parts) :] == resource_path.parts
                for member in members
            )
        if not present:
            missing.append(resource_path.as_posix())
    return tuple(missing)


def check_directory(distribution_directory: Path, package_root: Path) -> None:
    """Validate every wheel and source distribution in a directory."""
    archives = sorted(
        path
        for path in distribution_directory.iterdir()
        if path.is_file() and path.name.endswith(ARCHIVE_SUFFIXES)
    )
    if not archives:
        raise ValueError(f"no distribution archives found in {distribution_directory}")

    resources = runtime_resources(package_root)
    for archive in archives:
        missing = missing_resources(archive, resources)
        if missing:
            formatted = ", ".join(missing)
            raise ValueError(f"{archive.name} is missing runtime resources: {formatted}")
        print(f"{archive.name}: {len(resources)} runtime resources present")


def main() -> None:
    """Run the archive resource check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution_directory", type=Path)
    parser.add_argument(
        "--package-root",
        type=Path,
        default=Path("src/goldenage"),
        help="Source package directory used as the runtime resource manifest.",
    )
    args = parser.parse_args()
    check_directory(args.distribution_directory, args.package_root)


if __name__ == "__main__":
    main()
