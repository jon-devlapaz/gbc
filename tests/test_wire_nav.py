"""Playwright smoke tests for wire navigation.

Spawns uvicorn as a subprocess against a tmp `~/.claude/` fixture tree, exercises
real click-through paths in a headless Chromium to catch nav regressions.

Run explicitly with `.venv/bin/pytest tests/test_wire_nav.py -v` —
these tests are slower (~2s each) than the unit suite.
"""
from __future__ import annotations
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import pytest


PORT = 7899  # avoid user's dev port 7878


def _port_open(port: int) -> bool:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wire")
    claude = tmp / ".claude"
    projects = claude / "projects" / "-Users-jondev-dev-socratink-prod-socratink-app"
    projects.mkdir(parents=True)
    (claude / "skills").mkdir()
    (claude / "skills" / "demo").mkdir()
    (claude / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\n---\nhello\n")

    # One session fixture
    ev = [
        {"sessionId": "wire-uuid-1", "type": "user", "timestamp": "2026-01-01T00:00:00Z",
         "cwd": "/Users/jondev/dev/socratink/prod/socratink-app",
         "message": {"role": "user", "content": "wire test: find the auth bug"}},
    ]
    (projects / "wire-uuid-1.jsonl").write_text("\n".join(json.dumps(e) for e in ev))

    data = tmp / "data"; data.mkdir()

    env = os.environ.copy()
    env["CLAUDE_TOOL_CLAUDE_ROOT"] = str(claude)
    env["CLAUDE_TOOL_DATA_DIR"] = str(data)
    env["CLAUDE_TOOL_DISABLE_REASONER"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    # wait for the port
    deadline = time.time() + 15
    while time.time() < deadline:
        if _port_open(PORT):
            break
        if proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            raise RuntimeError(f"uvicorn exited before becoming ready:\n{out}")
        time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("uvicorn did not open the port in time")

    yield f"http://127.0.0.1:{PORT}"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_home_has_topnav_links(page, live_server):
    page.goto(live_server)
    assert page.locator("nav.topnav a", has_text="Home").is_visible()
    assert page.locator("nav.topnav a", has_text="Sessions").is_visible()
    assert page.locator("nav.topnav a", has_text="Families").is_visible()


def test_nav_home_to_sessions_to_home(page, live_server):
    page.goto(live_server)
    page.click("nav.topnav a:has-text('Sessions')")
    page.wait_for_url("**/sessions")
    assert "Sessions" in page.content()
    # Back to home via topnav
    page.click("nav.topnav a:has-text('Home')")
    page.wait_for_url(live_server + "/")
    assert page.locator("h2:has-text('Sessions')").or_(page.locator("#scan-spinner")).count() > 0


def test_scan_view_still_has_topnav(page, live_server):
    """Regression: after clicking 'New scan', the review view swaps into <main>.
    Header + topnav remain, so user can navigate back to Home or Sessions.
    """
    page.goto(live_server)
    page.click("button:has-text('New scan')")
    # Wait for htmx swap — review renders "Scan #N"
    page.wait_for_selector("h2:has-text('Scan #')")
    # Topnav links are still present + clickable
    assert page.locator("nav.topnav a:has-text('Home')").is_visible()
    page.click("nav.topnav a:has-text('Home')")
    page.wait_for_url(live_server + "/")


def test_session_detail_breadcrumb_back(page, live_server):
    page.goto(live_server + "/sessions")
    # First session in fixture
    page.click("a:has-text('wire test: find the auth bug')")
    page.wait_for_url("**/sessions/wire-uuid-1")
    assert "wire-uuid-1" in page.url
    # Breadcrumb back
    page.click("a.crumb-back:has-text('Sessions')")
    page.wait_for_url("**/sessions")


def test_families_page_reachable_and_renders_form(page, live_server):
    page.goto(live_server)
    page.click("nav.topnav a:has-text('Families')")
    page.wait_for_url("**/families")
    assert page.locator("form[action='/families']").is_visible()
    assert page.locator("form[action='/sessions/redact']").is_visible()
