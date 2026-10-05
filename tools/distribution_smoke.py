"""Exercise an installed GoldenAge distribution outside the source checkout."""

from __future__ import annotations

import argparse
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def clean_environment() -> dict[str, str]:
    """Return an environment that cannot import the source checkout or dotenv files."""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for name in (
        "DATABASE_URL",
        "GOLDENAGE_ARTIFACT_DIR",
        "GOLDENAGE_LOCAL_FIRST_MODE",
        "GOLDENAGE_LOCAL_FIRST_DB",
        "GOLDENAGE_SQLITE_PATH",
    ):
        environment.pop(name, None)
    environment["GOLDENAGE_DISABLE_DOTENV"] = "1"
    return environment


def run_module(
    module: str,
    arguments: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> None:
    """Run an installed module and include its output on failure."""
    result = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{module} failed with exit code {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def smoke_sqlite(work_directory: Path, environment: dict[str, str]) -> None:
    """Apply the installed SQLite migrations twice and compare migration history."""
    database_path = work_directory / "goldenage.sqlite3"
    arguments = ["--path", str(database_path)]
    run_module("goldenage.bootstrap_sqlite", arguments, cwd=work_directory, environment=environment)
    with sqlite3.connect(database_path) as connection:
        before = connection.execute("SELECT name FROM schema_migration ORDER BY name").fetchall()
    run_module("goldenage.bootstrap_sqlite", arguments, cwd=work_directory, environment=environment)
    with sqlite3.connect(database_path) as connection:
        after = connection.execute("SELECT name FROM schema_migration ORDER BY name").fetchall()
    if not before or before != after or len({name for (name,) in after}) != len(after):
        raise RuntimeError(
            f"SQLite migration history changed on repeat run: {before!r} -> {after!r}"
        )
    print(f"SQLite bootstrap: {len(after)} migrations applied once")


def smoke_postgres(
    work_directory: Path,
    environment: dict[str, str],
    dsn: str,
) -> None:
    """Apply the installed PostgreSQL migrations twice and compare migration history."""
    arguments = ["--dsn", dsn]
    run_module(
        "goldenage.bootstrap_postgres", arguments, cwd=work_directory, environment=environment
    )
    before = postgres_migration_names(dsn, environment, work_directory)
    run_module(
        "goldenage.bootstrap_postgres", arguments, cwd=work_directory, environment=environment
    )
    after = postgres_migration_names(dsn, environment, work_directory)
    if not before or before != after or len(set(after)) != len(after):
        raise RuntimeError(
            f"PostgreSQL migration history changed on repeat run: {before!r} -> {after!r}"
        )
    print(f"PostgreSQL bootstrap: {len(after)} migrations applied once")


def postgres_migration_names(
    dsn: str,
    environment: dict[str, str],
    work_directory: Path,
) -> list[str]:
    """Read PostgreSQL migration history using the installed psycopg dependency."""
    probe = (
        "import psycopg; "
        "connection = psycopg.connect(__import__('sys').argv[1]); "
        "print('\\n'.join(row[0] for row in connection.execute("
        "'SELECT name FROM schema_migration ORDER BY name').fetchall())); "
        "connection.close()"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, dsn],
        cwd=work_directory,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in result.stdout.splitlines() if name]


def free_port() -> int:
    """Reserve an available local TCP port for the short-lived app process."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def smoke_web(work_directory: Path, environment: dict[str, str]) -> None:
    """Start the installed app from a non-source working directory and fetch HTML/CSS."""
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "goldenage.web.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=work_directory,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        wait_for_server(process, url)
        page = fetch(url + "/worklist")
        stylesheet = fetch(url + "/static/style.css")
        if "GoldenAge" not in page or ".worklist" not in stylesheet:
            raise RuntimeError("installed app returned incomplete HTML or static content")
        print("Web smoke: rendered /worklist and served /static/style.css")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def wait_for_server(process: subprocess.Popen[str], url: str) -> None:
    """Wait for Uvicorn to accept requests, or surface an early process failure."""
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            raise RuntimeError(f"Uvicorn exited early: {output}")
        try:
            fetch(url + "/")
        except (OSError, URLError) as error:
            last_error = error
            time.sleep(0.1)
        else:
            return
    raise RuntimeError(f"Uvicorn did not start: {last_error}")


def fetch(url: str) -> str:
    """Fetch a UTF-8 HTTP response body."""
    with urlopen(url, timeout=2) as response:
        return response.read().decode("utf-8")


def main() -> None:
    """Run the installed-package smoke checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-dsn", default=None)
    args = parser.parse_args()
    environment = clean_environment()
    with tempfile.TemporaryDirectory(prefix="goldenage-distribution-smoke-") as temporary_directory:
        work_directory = Path(temporary_directory)
        smoke_sqlite(work_directory, environment)
        smoke_web(work_directory, environment)
        if args.postgres_dsn:
            smoke_postgres(work_directory, environment, args.postgres_dsn)


if __name__ == "__main__":
    main()
