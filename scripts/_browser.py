"""Shared browser launch for the discovery scripts.

Some sites fingerprint the browser and reject headless outright, and their
challenge is intermittent — so a launch may need to be headed, reuse the saved
profile, and be retried before a "blocked" verdict means anything.
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, "/app")
from crawler import blockcheck  # noqa: E402

PROFILE_SRC = os.getenv("BROWSER_PROFILE_DIR", "/data/browser-profile")
ARGS = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]


def _profile_copy() -> str | None:
    """Work on a copy so a live crawl's lock on the profile cannot block us."""
    if not os.path.isdir(PROFILE_SRC):
        return None
    work = "/tmp/profile-copy"
    if not os.path.isdir(work):
        shutil.copytree(PROFILE_SRC, work, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("Singleton*", "*Cache*", "GPUCache"))
    return work


def open_page(pw, *, use_profile: bool = True):
    """Return (closer, context, page)."""
    headless = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
    profile = _profile_copy() if use_profile else None
    common = dict(locale="hr-HR", timezone_id="Europe/Zagreb",
                  viewport={"width": 1440, "height": 900})

    if profile:
        ctx = pw.chromium.launch_persistent_context(
            profile, headless=headless, args=ARGS, **common)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        return ctx.close, ctx, page

    browser = pw.chromium.launch(headless=headless, args=ARGS)
    ctx = browser.new_context(**common)
    return browser.close, ctx, ctx.new_page()


def goto_unblocked(page, url: str, attempts: int = 3, host: str = ""):
    """Load a URL, retrying while a challenge is served.

    The challenge is not deterministic, so one blocked attempt proves nothing.
    Returns (response, reason) — reason is None when real content arrived.
    """
    reason = None
    for i in range(1, attempts + 1):
        resp = page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        reason = blockcheck.detect(page, resp)
        if not reason:
            if i > 1:
                print(f"  (cleared on attempt {i})")
            return resp, None
        print(f"  attempt {i}/{attempts}: blocked ({reason})")
        page.wait_for_timeout(4000)
    return None, reason
