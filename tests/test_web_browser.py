import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import Page, expect, sync_playwright

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.getenv("GOLDENAGE_BROWSER_TESTS") != "1",
        reason="set GOLDENAGE_BROWSER_TESTS=1 to run real-browser coverage",
    ),
]

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT_DIR = Path(os.getenv("GOLDENAGE_SCREENSHOT_DIR", "/tmp/goldenage-frontend-screenshots"))


def test_worklist_and_intake_flow_at_desktop_and_mobile(tmp_path: Path) -> None:
    fixture_path = tmp_path / "mail-fixture.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "candidate_id": "browser-mail-1",
                    "account_name": "alex@example.com",
                    "mailbox_name": "Inbox",
                    "subject": "Acme contract renewal",
                    "sender_name": "Max Mustermann",
                    "sender_email": "max@acme.example",
                    "sent_at": "2026-04-12T09:30:00+00:00",
                    "preview_text": "Please review the latest renewal draft.",
                    "unread": True,
                    "raw_source": (
                        "From: Max Mustermann <max@acme.example>\n"
                        "To: Alex Example <alex@example.com>\n"
                        "Subject: Acme contract renewal\n"
                        "Date: Sun, 12 Apr 2026 09:30:00 +0000\n"
                        "Message-ID: <browser-mail-1@example.com>\n"
                        "\n"
                        "Please review the latest renewal draft.\n"
                    ),
                }
            ]
        ),
        encoding="utf-8",
    )

    with _serve(
        {
            "GOLDENAGE_ARTIFACT_DIR": str(tmp_path / "artifacts"),
            "GOLDENAGE_MAIL_FIXTURE_PATH": str(fixture_path),
        }
    ) as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            desktop = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                reduced_motion="reduce",
            )
            desktop_page = desktop.new_page()
            desktop_failures = _track_browser_failures(desktop_page)

            desktop_page.goto(f"{base_url}/worklist", wait_until="networkidle")
            desktop_page.wait_for_function("typeof window.htmx === 'object'")
            expect(desktop_page.locator(".work-item").first).to_be_visible()
            _assert_no_horizontal_overflow(desktop_page)

            desktop_page.locator(".work-item").first.click()
            expect(desktop_page).to_have_url(re.compile(r"/worklist\?case_id=[0-9a-f-]+$"))
            expect(desktop_page.locator(".work-item.is-selected")).to_have_count(1)
            expect(desktop_page.locator(".work-item.is-selected")).to_have_attribute(
                "aria-current", "true"
            )

            resolution_url = desktop_page.locator("[data-resolution-form]").get_attribute("action")
            desktop_page.evaluate(
                """
                ([url]) => htmx.ajax('POST', url, {
                  target: '#detail-panel',
                  swap: 'innerHTML',
                  values: {resolution_path: 'conflicting-path'},
                })
                """,
                [resolution_url],
            )
            expect(desktop_page.locator("#detail-panel [role=alert]")).to_have_text(
                "Choose one resolution path."
            )
            _discard_expected_htmx_status(desktop_failures, 400)
            desktop_page.locator(".work-item").first.click()
            expect(desktop_page.locator("#detail-panel [role=alert]")).to_have_count(0)

            close_path = desktop_page.get_by_role("radio", name="Close the case")
            close_path.check()
            expect(desktop_page.locator("[data-resolution-follow-up]")).to_be_hidden()
            follow_up_path = desktop_page.get_by_role("radio", name="Schedule a follow-up")
            follow_up_path.check()
            expect(desktop_page.locator("[data-resolution-follow-up]")).to_be_visible()
            expect(desktop_page.locator('input[name="next_step"]')).to_have_attribute(
                "required", ""
            )

            file_input = desktop_page.locator('.intake-dropzone input[type="file"]')
            desktop_page.get_by_role("button", name="Upload mail").click()
            expect(desktop_page.locator("[data-file-name]")).to_have_text(
                "Select an Outlook .msg file before uploading."
            )
            expect(file_input).to_have_attribute("aria-invalid", "true")
            expect(desktop_page.locator(".file-picker-button")).to_have_css(
                "outline-style", "solid"
            )
            file_input.set_input_files(
                {
                    "name": "selected-mail.msg",
                    "mimeType": "application/vnd.ms-outlook",
                    "buffer": b"browser test selection",
                }
            )
            expect(desktop_page.locator("[data-file-name]")).to_have_text(
                "Selected: selected-mail.msg"
            )
            expect(file_input).not_to_have_attribute("aria-invalid", "true")
            file_input.focus()
            expect(desktop_page.locator(".file-picker-button")).to_have_css(
                "outline-style", "solid"
            )

            desktop_page.evaluate(
                """
                () => htmx.ajax('POST', '/mail/desktop-mail/search', {
                  target: '#intake-panel',
                  swap: 'innerHTML',
                  values: {result_limit: 'not-a-number'},
                })
                """
            )
            expect(desktop_page.locator("#intake-panel [role=alert]")).to_have_text(
                "Check the form fields and try again."
            )
            _discard_expected_htmx_status(desktop_failures, 422)

            desktop_page.get_by_role("button", name="Search mail").click()
            expect(
                desktop_page.locator("#intake-panel h4").get_by_text(
                    "Acme contract renewal", exact=True
                )
            ).to_be_visible()
            desktop_page.get_by_role("button", name="Import into intake").click()
            expect(desktop_page.locator("#intake-panel .message")).to_contain_text(
                "Mail message imported"
            )
            expect(desktop_page.locator(".intake-active")).to_be_visible()
            expect(desktop_page.get_by_text("Unassigned Intake", exact=True)).to_be_visible()
            _assert_no_horizontal_overflow(desktop_page)

            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            desktop_page.screenshot(
                path=SCREENSHOT_DIR / "worklist-intake-desktop.png",
                full_page=True,
            )
            assert desktop_failures == []
            assert all(
                "unpkg.com" not in url
                for url in desktop_page.evaluate(
                    "performance.getEntriesByType('resource').map(entry => entry.name)"
                )
            )

            mobile = browser.new_context(
                viewport={"width": 390, "height": 844},
                reduced_motion="reduce",
            )
            mobile_page = mobile.new_page()
            mobile_failures = _track_browser_failures(mobile_page)
            mobile_page.goto(f"{base_url}/worklist", wait_until="networkidle")
            mobile_page.locator(".work-item").first.click()
            expect(mobile_page.locator(".work-item.is-selected")).to_have_count(1)
            _assert_no_horizontal_overflow(mobile_page)
            mobile_page.screenshot(
                path=SCREENSHOT_DIR / "worklist-mobile-390.png",
                full_page=True,
            )
            assert mobile_failures == []

            mobile.close()
            desktop.close()
            browser.close()


def test_login_at_desktop_and_mobile(tmp_path: Path) -> None:
    with _serve(
        {
            "GOLDENAGE_LOCAL_FIRST_MODE": "sqlite3",
            "GOLDENAGE_SQLITE_PATH": str(tmp_path / "goldenage.sqlite3"),
            "GOLDENAGE_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        }
    ) as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            setup = browser.new_context(viewport={"width": 1200, "height": 900})
            setup_page = setup.new_page()
            setup_page.goto(f"{base_url}/onboarding")
            setup_page.get_by_label("Display name").fill("Browser User")
            setup_page.get_by_label("Email").fill("browser@example.com")
            setup_page.get_by_label("Password", exact=True).fill("secret-passphrase")
            setup_page.get_by_label("Confirm password").fill("secret-passphrase")
            setup_page.get_by_role("button", name="Create local workspace user").click()
            expect(setup_page).to_have_url(re.compile(r"/worklist$"))
            setup.close()

            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            desktop = browser.new_context(viewport={"width": 1440, "height": 1000})
            desktop_page = desktop.new_page()
            desktop_failures = _track_browser_failures(desktop_page)
            desktop_page.goto(f"{base_url}/login", wait_until="networkidle")
            expect(desktop_page.get_by_role("heading", name="Sign in to GoldenAge")).to_be_visible()
            expect(desktop_page.locator("video")).to_have_count(0)
            expect(desktop_page.get_by_text("LDAP", exact=False)).to_have_count(0)
            _assert_no_horizontal_overflow(desktop_page)
            desktop_page.screenshot(
                path=SCREENSHOT_DIR / "login-desktop.png",
                full_page=True,
            )
            assert desktop_failures == []

            mobile = browser.new_context(viewport={"width": 390, "height": 844})
            mobile_page = mobile.new_page()
            mobile_failures = _track_browser_failures(mobile_page)
            mobile_page.goto(f"{base_url}/login", wait_until="networkidle")
            mobile_page.get_by_label("Email").fill("browser@example.com")
            mobile_page.get_by_label("Password").fill("wrong-password")
            mobile_page.get_by_role("button", name="Sign in").click()
            expect(mobile_page.get_by_role("alert")).to_have_text("Invalid email or password.")
            expect(mobile_page.get_by_label("Email")).to_have_value("browser@example.com")
            _assert_no_horizontal_overflow(mobile_page)
            mobile_page.screenshot(
                path=SCREENSHOT_DIR / "login-mobile-390.png",
                full_page=True,
            )
            assert mobile_failures == []

            mobile.close()
            desktop.close()
            browser.close()


def _track_browser_failures(page: Page) -> list[str]:
    failures: list[str] = []

    def track_console(message) -> None:
        if message.type == "error" and not message.text.startswith("Failed to load resource"):
            failures.append(f"console: {message.text}")

    def track_asset_response(response) -> None:
        asset_types = {"font", "image", "media", "script", "stylesheet"}
        if response.status >= 400 and response.request.resource_type in asset_types:
            failures.append(f"asset: {response.url} ({response.status})")

    page.on("console", track_console)
    page.on("response", track_asset_response)
    page.on(
        "requestfailed",
        lambda request: failures.append(f"request: {request.url} ({request.failure})"),
    )
    return failures


def _assert_no_horizontal_overflow(page: Page) -> None:
    dimensions = page.evaluate(
        """
        () => ({
          viewport: window.innerWidth,
          document: document.documentElement.scrollWidth,
          body: document.body.scrollWidth,
        })
        """
    )
    assert dimensions["document"] <= dimensions["viewport"] + 1
    assert dimensions["body"] <= dimensions["viewport"] + 1


def _discard_expected_htmx_status(failures: list[str], status_code: int) -> None:
    marker = f"Response Status Error Code {status_code}"
    matching = [failure for failure in failures if marker in failure]
    assert len(matching) == 1
    failures.remove(matching[0])
    assert failures == []


@contextmanager
def _serve(extra_env: dict[str, str]) -> Iterator[str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "GOLDENAGE_DISABLE_DOTENV": "1",
            "GOLDENAGE_OUTLOOK_SYNC_ENABLED": "0",
            **extra_env,
        }
    )
    for name in (
        "DATABASE_URL",
        "GOLDENAGE_LOCAL_FIRST_MODE",
        "GOLDENAGE_SQLITE_PATH",
        "GOLDENAGE_MAIL_FIXTURE_PATH",
    ):
        if name not in extra_env:
            env.pop(name, None)
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
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server(base_url, process)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _wait_for_server(base_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Uvicorn exited with status {process.returncode}.")
        try:
            with urlopen(f"{base_url}/", timeout=0.5) as response:
                if response.status < 500:
                    return
        except URLError:
            time.sleep(0.1)
    raise RuntimeError("Uvicorn did not start within 20 seconds.")


def _free_port() -> int:
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return server_socket.getsockname()[1]
