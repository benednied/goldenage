"""Fixtures for isolated, data-minimal Playwright checks."""

from __future__ import annotations

import hashlib
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import UUID

import pytest

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - exercised by the quality-only job
    sync_playwright = None

try:
    from axe_playwright_python.sync_playwright import Axe
except ModuleNotFoundError:  # pragma: no cover - exercised by the quality-only job
    Axe = None

from goldenage.adapters.sqlite import (  # noqa: E402
    SQLiteActivityRepository,
    SQLiteCaseRepository,
    SQLiteLocalUserRepository,
)
from goldenage.bootstrap_sqlite import ensure_sqlite_bootstrapped  # noqa: E402
from goldenage.domain.models import Activity, CaseFile  # noqa: E402


@dataclass(frozen=True, slots=True)
class BrowserApp:
    """Address and synthetic credentials for one isolated browser run."""

    base_url: str
    email: str
    password: str
    case_id: UUID


def pytest_configure(config: pytest.Config) -> None:
    """Register the browser marker without requiring a pytest plugin."""
    config.addinivalue_line("markers", "browser: requires a real Playwright browser")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]):
    """Expose the test result to the page fixture for failure-only artifacts."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(scope="session")
def browser() -> Iterator[object]:
    """Launch one headless Chromium process for the browser suite."""
    if sync_playwright is None:
        pytest.skip("browser dependencies are not installed")
    with sync_playwright() as playwright:
        browser_instance = playwright.chromium.launch()
        yield browser_instance
        browser_instance.close()


@pytest.fixture(scope="session")
def axe() -> object:
    """Return the bundled axe-core runner."""
    if Axe is None:
        pytest.skip("browser accessibility dependencies are not installed")
    return Axe()


@pytest.fixture()
def page(browser: object, request: pytest.FixtureRequest) -> Iterator[object]:
    """Create a fresh browser context and retain trace/screenshot only on failure."""
    artifact_dir = Path(os.environ.get("PLAYWRIGHT_ARTIFACT_DIR", "test-results"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    test_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", request.node.nodeid)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.tracing.start(screenshots=True, snapshots=True, sources=False)
    current_page = context.new_page()
    yield current_page

    failed = bool(getattr(request.node, "rep_call", None) and request.node.rep_call.failed)
    if failed:
        current_page.screenshot(path=str(artifact_dir / f"{test_slug}.png"), full_page=True)
        context.tracing.stop(path=str(artifact_dir / f"{test_slug}.trace.zip"))
    else:
        context.tracing.stop()
    context.close()


@pytest.fixture()
def browser_app(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[BrowserApp]:
    """Start an app backed by a fresh SQLite database and synthetic records."""
    database_path = tmp_path / "goldenage.sqlite3"
    artifact_path = tmp_path / "artifacts"
    ensure_sqlite_bootstrapped(database_path, Path(__file__).parents[2] / "sql" / "sqlite")

    account_id = UUID("11111111-1111-1111-1111-111111111111")
    case_id = UUID("22222222-2222-2222-2222-222222222222")
    activity_id = UUID("33333333-3333-3333-3333-333333333333")
    email = "browser@example.test"
    password = "browser-test-password"
    password_hash = _test_password_hash(password)
    SQLiteLocalUserRepository(database_path).create_user(
        account_id=account_id,
        email=email,
        display_name="Browser Test User",
        password_hash=password_hash,
        profile_image_path=None,
    )

    fixed_now = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    SQLiteCaseRepository(database_path).save_case(
        CaseFile(
            id=case_id,
            title="Example contract renewal",
            company="Example Test GmbH",
            primary_contact="Alex Example",
            status="open",
            last_activity_at=fixed_now - timedelta(days=1),
        )
    )
    SQLiteActivityRepository(database_path).save_activity(
        Activity(
            id=activity_id,
            case_id=case_id,
            description="Review the renewal clause",
            kind="follow_up",
            due_at=fixed_now - timedelta(days=1),
            created_at=fixed_now - timedelta(days=2),
            created_by=account_id,
        )
    )

    port = _free_port()
    artifact_dir = Path(os.environ.get("PLAYWRIGHT_ARTIFACT_DIR", "test-results"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    test_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", request.node.nodeid)
    server_log = (artifact_dir / f"{test_slug}.server.log").open("w", encoding="utf-8")
    environment = os.environ.copy()
    pythonpath = environment.get("PYTHONPATH")
    environment.update(
        {
            "GOLDENAGE_DISABLE_DOTENV": "1",
            "GOLDENAGE_LOCAL_FIRST_MODE": "sqlite3",
            "GOLDENAGE_SQLITE_PATH": str(database_path),
            "GOLDENAGE_ARTIFACT_DIR": str(artifact_path),
            "GOLDENAGE_AUTH_SECRET": "browser-test-auth-secret",
            "GOLDENAGE_LOCAL_TIMEZONE": "UTC",
            "PYTHONPATH": os.pathsep.join(
                value for value in (str(Path(__file__).parents[2] / "src"), pythonpath) if value
            ),
        }
    )
    server = subprocess.Popen(
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
        env=environment,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base_url}/login")
        yield BrowserApp(base_url=base_url, email=email, password=password, case_id=case_id)
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        server_log.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError, URLError:
            time.sleep(0.1)
    raise RuntimeError(f"Browser test server did not become ready: {url}")


def _test_password_hash(password: str) -> str:
    salt = "browser-test-salt"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 1).hex()
    return f"pbkdf2_sha256$1${salt}${digest}"
