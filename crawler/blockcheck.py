"""Decide whether a page is a bot challenge rather than real content.

Naive substring matching on raw HTML is wrong: a normal page routinely mentions
"captcha" inside an inline anti-bot or contact-form script, which produces a
false "blocked" verdict on a page that loaded perfectly. Judge on what a reader
would actually see, and prefer positive evidence that content arrived.
"""
from __future__ import annotations

# Titles a challenge page actually uses. Matched against <title> only.
CHALLENGE_TITLES = (
    "captcha", "just a moment", "attention required", "access denied",
    "security check", "are you a human", "robot", "verifying you are human",
    "one moment", "checking your browser",
)

# Phrases in VISIBLE text. Only meaningful on a page with little other content.
CHALLENGE_TEXT = (
    "verify you are human", "are you a human", "unusual traffic",
    "access to this page has been denied", "checking your browser",
    "please enable javascript and cookies", "potvrdite da niste robot",
)

# A challenge page is nearly empty; a results page is not.
MAX_CHALLENGE_TEXT = 1200


def detect(page, resp=None) -> str | None:
    """Return a reason string when the page looks like a challenge, else None."""
    if resp is not None and getattr(resp, "status", None) in (403, 429):
        return f"HTTP {resp.status}"

    title = (page.title() or "").strip()
    tl = title.lower()
    for marker in CHALLENGE_TITLES:
        if marker in tl:
            return f"title {title[:60]!r}"

    try:
        text = page.eval_on_selector("body", "e => e.innerText") or ""
    except Exception:
        return None                     # no body yet; caller decides

    stripped = " ".join(text.split())
    # Only treat challenge phrasing as decisive on a near-empty page: a real
    # listing page may legitimately mention robots or cookies somewhere.
    if len(stripped) <= MAX_CHALLENGE_TEXT:
        low = stripped.lower()
        for marker in CHALLENGE_TEXT:
            if marker in low:
                return f"challenge text on a {len(stripped)}-char page"
        if len(stripped) < 200:
            return f"page has only {len(stripped)} chars of visible text"

    return None


def content_evidence(page, host: str) -> dict:
    """Positive signals that real content arrived — better than any blocklist."""
    try:
        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href') || '')")
    except Exception:
        links = []
    try:
        text = page.eval_on_selector("body", "e => e.innerText") or ""
    except Exception:
        text = ""
    try:
        imgs = page.eval_on_selector_all(
            "img", "els => els.filter(e => e.naturalWidth > 120).length")
    except Exception:
        imgs = 0

    return {
        "links": len(links),
        "internal_links": sum(1 for h in links if h.startswith("/") or host in h),
        "text_chars": len(" ".join(text.split())),
        "real_images": imgs,
    }
