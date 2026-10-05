"""Critical user journeys exercised in a real browser."""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.browser


def test_login_keyboard_order_and_error_state_are_accessible(
    page: object, browser_app, axe
) -> None:
    page.goto(f"{browser_app.base_url}/login")
    page.wait_for_load_state("domcontentloaded")
    _assert_no_axe_violations(page, axe, "login")

    assert page.locator("html").get_attribute("lang") == "en"
    assert page.get_by_label("Email or username").count() == 1
    assert page.get_by_label("Password").count() == 1

    page.keyboard.press("Tab")
    assert page.locator('input[name="credential_provider"][value="local"]').evaluate(
        "element => element === document.activeElement"
    )
    page.keyboard.press("Tab")
    assert page.locator('input[name="credential_provider"][value="ldap"]').evaluate(
        "element => element === document.activeElement"
    )

    page.get_by_label("Email or username").fill(browser_app.email)
    page.get_by_label("Password").fill("wrong-password")
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("alert").wait_for()
    assert "Invalid email or password." in page.get_by_role("alert").inner_text()
    _assert_no_axe_violations(page, axe, "login-error")


def test_worklist_and_detail_are_accessible_at_desktop_and_narrow_widths(
    page: object, browser_app, axe
) -> None:
    _sign_in(page, browser_app)
    assert page.get_by_role("heading", name="Due activities").is_visible()
    assert page.get_by_role("link", name=re.compile("Example contract renewal")).is_visible()
    _assert_no_axe_violations(page, axe, "worklist")

    page.get_by_role("link", name=re.compile("Example contract renewal")).click()
    page.locator("#detail-panel h2").wait_for()
    assert page.locator("#detail-panel h2").inner_text() == "Example contract renewal"
    assert page.locator("#detail-panel h2").evaluate(
        "element => element === document.activeElement"
    )
    _assert_no_axe_violations(page, axe, "detail")

    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator(".workspace").evaluate(
        "element => getComputedStyle(element).gridTemplateColumns.split(' ').length === 1"
    )
    _assert_no_axe_violations(page, axe, "detail-narrow")


def test_resolution_error_keeps_context_and_is_announced(page: object, browser_app, axe) -> None:
    _sign_in(page, browser_app)
    page.get_by_role("link", name=re.compile("Example contract renewal")).click()
    page.locator("#detail-panel form").wait_for()

    page.get_by_role("button", name="Resolve with a clear next state").click()
    error = page.get_by_role("alert")
    error.wait_for()
    assert "requires closing the case" in error.inner_text()
    assert page.locator("#worklist-pane").is_visible()
    assert page.locator("#detail-panel").evaluate(
        "element => element.contains(document.activeElement)"
    )
    _assert_no_axe_violations(page, axe, "resolution-error")


def test_successful_htmx_mutation_announces_updated_state(page: object, browser_app, axe) -> None:
    _sign_in(page, browser_app)
    page.get_by_role("link", name=re.compile("Example contract renewal")).click()
    page.locator("#detail-panel form").wait_for()

    page.get_by_label("What happens next?").fill("Send the revised clause language")
    page.get_by_label("Follow-up due").fill("2030-01-16T10:00")
    page.get_by_role("button", name="Resolve with a clear next state").click()

    message = page.get_by_role("status", name="Activity resolved. The worklist has been updated.")
    message.wait_for()
    assert page.url == f"{browser_app.base_url}/worklist"
    assert page.locator("#workspace").evaluate(
        "element => element.contains(document.activeElement)"
    )
    assert page.get_by_text("Send the revised clause language").is_visible()
    _assert_no_axe_violations(page, axe, "resolved")


def _sign_in(page: object, browser_app) -> None:
    page.goto(f"{browser_app.base_url}/login")
    page.get_by_label("Email or username").fill(browser_app.email)
    page.get_by_label("Password").fill(browser_app.password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{browser_app.base_url}/worklist")
    page.wait_for_function("() => window.htmx !== undefined")


def _assert_no_axe_violations(page: object, axe: object, state: str) -> None:
    results = axe.run(page)
    violations = results.response.get("violations", [])
    assert not violations, _format_axe_violations(state, violations)


def _format_axe_violations(state: str, violations: list[dict[str, object]]) -> str:
    lines = [f"axe violations in {state}:"]
    for violation in violations:
        lines.append(f"- {violation['id']}: {violation['help']} ({violation['helpUrl']})")
        for node in violation.get("nodes", []):
            lines.append(f"  {node['target']}: {node['html']}")
    return "\n".join(lines)
