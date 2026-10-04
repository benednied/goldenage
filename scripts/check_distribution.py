"""Validate the metadata and contents of GoldenAge distribution artifacts."""

from __future__ import annotations

import argparse
import re
import tarfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

EXPECTED_NAME = "goldenage"
FORBIDDEN_MEMBER_PARTS = (
    ".git",
    ".venv",
    ".env",
    ".pem",
    ".key",
    "credentials",
)


def _metadata_bytes(path: Path) -> bytes:
    if path.name.endswith(".whl"):
        with ZipFile(path) as archive:
            candidates = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            if len(candidates) != 1:
                raise ValueError(f"{path.name}: expected one wheel METADATA file")
            return archive.read(candidates[0])

    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, mode="r:gz") as archive:
            candidates = [
                member
                for member in archive.getmembers()
                if member.isfile() and member.name.endswith("/PKG-INFO")
            ]
            if len(candidates) != 1:
                raise ValueError(f"{path.name}: expected one sdist PKG-INFO file")
            extracted = archive.extractfile(candidates[0])
            if extracted is None:
                raise ValueError(f"{path.name}: could not read PKG-INFO")
            return extracted.read()

    raise ValueError(f"unsupported distribution: {path.name}")


def _distribution_metadata(path: Path) -> tuple[str, str]:
    message = BytesParser(policy=policy.compat32).parsebytes(_metadata_bytes(path))
    name = message.get("Name")
    version = message.get("Version")
    if not name or not version:
        raise ValueError(f"{path.name}: metadata must contain Name and Version")
    return name, version


def _assert_safe_members(path: Path) -> None:
    if path.name.endswith(".whl"):
        with ZipFile(path) as archive:
            members = archive.namelist()
    else:
        with tarfile.open(path, mode="r:gz") as archive:
            members = [member.name for member in archive.getmembers()]

    for member in members:
        lowered = member.casefold()
        if any(part in lowered for part in FORBIDDEN_MEMBER_PARTS):
            raise ValueError(f"{path.name}: forbidden private/generated member {member!r}")


def check_distribution(directory: Path, expected_version: str) -> None:
    artifacts = [path for path in directory.iterdir() if path.is_file()]
    wheels = [path for path in artifacts if path.name.endswith(".whl")]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(
            f"expected exactly one wheel and one sdist in {directory}, "
            f"found {len(wheels)} wheels and {len(sdists)} sdists"
        )

    for path in (*wheels, *sdists):
        name, version = _distribution_metadata(path)
        if re.sub(r"[-_.]+", "-", name).casefold() != EXPECTED_NAME:
            raise ValueError(f"{path.name}: unexpected distribution name {name!r}")
        if version != expected_version:
            raise ValueError(
                f"{path.name}: metadata version {version!r} does not match {expected_version!r}"
            )
        _assert_safe_members(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    check_distribution(args.directory, args.version)
    print(f"validated {args.directory} for goldenage {args.version}")


if __name__ == "__main__":
    main()
