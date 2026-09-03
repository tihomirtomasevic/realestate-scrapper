"""Condition gate — decide which properties are excluded before scoring.

Three states are hard exclusions rather than low scores: an unfinished shell,
a ruin, and a house sold "for adaptation". They are filtered out of the feed by
default and never reach the scorer, which also keeps them off the LLM bill.

WHY AN LLM AND NOT A KEYWORD LIST
Measured over 159 properties, a keyword list and this gate disagreed on 27:

  * 4 the keywords wrongly excluded. "pruza mogucnost raznih ideja pri
    UREDENJU" is an upside, and "za OBNOVU su koristeni visokokvalitetni
    materijali" describes work already finished. Both trip a stem match.
  * 23 the keywords missed. Seven were houses sold off-plan -- "Zavrsetak
    radova predviden: 08/2026" -- which share no vocabulary with "roh bau"
    yet are just as unfinished.

The direction of a phrase, not its stem, carries the meaning, so the rules
below are stated explicitly in the prompt.

SEVERITY IS THE SOFT FIELD
The booleans reproduce exactly across runs; `severity` and `scope` do not. Re-
running 12 unchanged ads returned identical flags but flipped one severity
(partial -> full) and one scope (whole -> part). Batched inference changes the
order of floating-point reductions, so temperature=0 is not bit-reproducible and
a near-tied enum choice can land either way. A quote proves work is needed, not
how much, so nothing verifies these two fields the way it verifies the flags.

This is contained rather than fixed: `pending()` keys on the ad text, so a
listing is classified once and only re-run when its copy actually changes. The
flip is only reachable by forcing a re-classification. Treat severity as
advisory -- and if the cosmetic/partial split ever looks wrong, distrust it
before distrusting the flags.

TRUST BOUNDARY
Every flag must be backed by a span the model copied out of the ad. The span is
checked against the source text here, in code; anything not found verbatim is
dropped and its flag cleared. On a smoke test with no real text to quote the
model invented a fluent supporting sentence, so this is not hypothetical --
it is the one defence that does not depend on the model behaving.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger(__name__)

# Bump when the prompt or schema changes so stored verdicts can be re-run.
PROMPT_VERSION = "gate-2"

FLAGS = ("unfinished", "ruin", "needs_adaptation")

SYSTEM = """Ti si asistent koji iz oglasa za nekretnine izvlaci ISKLJUCIVO cinjenice.

Oznaci tri stanja objekta:
- unfinished: objekt NIJE dovrsen (roh bau, u izgradnji, u fazi adaptacije,
  nedovrsen, ili se prodaje s planiranim zavrsetkom radova u buducnosti).
- ruin: rusevina, za rusenje, objekt nije useljiv bez potpune obnove.
- needs_adaptation: oglas trazi da KUPAC obavi adaptaciju, renovaciju ili uredenje.

Za svaku oznaku koju postavis na true odredi i:
- severity: "cosmetic" = objekt je useljiv, treba samo osvjezenje (npr. "uz manje
  uredenje", "lagana adaptacija"); "partial" = dio objekta trazi ozbiljne radove;
  "full" = trazi kompletnu adaptaciju ili obnovu.
- scope: "whole" = odnosi se na cijeli objekt; "part" = odnosi se samo na dio
  (npr. samo potkrovlje, samo jedna etaza).

KLJUCNA PRAVILA:
1. SMJER, NE KORIJEN RIJECI. "za adaptaciju", "za renovaciju", "potrebna obnova"
   = posao TEK TREBA obaviti -> true. "renovirana", "adaptirana", "obnovljena",
   "za obnovu su koristeni materijali" = posao je VEC obavljen -> false.
2. "starina" cesto znaci samo stara kamena kuca, sto NIJE mana. Oznaci ruin samo
   ako se spominje rusevina, rusenje ili neuseljivost.
3. quote MORA biti doslovan isjecak iz teksta oglasa, kopiran znak po znak.
   Ako takvog isjecka nema, quote je prazan string i oznaka je false.
   NIKADA ne izmisljaj tekst koji nije u oglasu."""


def _flag_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "v": {"type": "boolean"},
            "quote": {"type": "string"},
            "severity": {"type": "string", "enum": ["cosmetic", "partial", "full", ""]},
            "scope": {"type": "string", "enum": ["whole", "part", ""]},
        },
        "required": ["v", "quote", "severity", "scope"],
        "additionalProperties": False,
    }


SCHEMA = {
    "name": "condition_gate",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {f: _flag_schema() for f in FLAGS},
        "required": list(FLAGS),
        "additionalProperties": False,
    },
}

_SEVERITY_RANK = {"cosmetic": 1, "partial": 2, "full": 3}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _norm(s: str) -> str:
    """Fold case, diacritics and runs of whitespace for the substring check.

    Agencies paste ad copy through editors that swap quote characters and
    collapse newlines, so an otherwise honest quote can differ from the source
    by punctuation alone. Folding avoids rejecting those.
    """
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


class GateError(RuntimeError):
    pass


def _endpoint() -> str:
    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    if not base:
        raise GateError("LLM_BASE_URL is not set")
    return f"{base}/chat/completions"


def classify(text: str, *, model: str | None = None, timeout: float | None = None) -> dict:
    """Ask the model about one ad and return only claims it could evidence."""
    model = model or os.getenv("LLM_MODEL", "")
    if not model:
        raise GateError("LLM_MODEL is not set")
    req_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "OGLAS:\n" + text[:6000]},
        ],
        "response_format": {"type": "json_schema", "json_schema": SCHEMA},
        "max_tokens": 700,
        "temperature": 0,
    }
    # A reasoning model spends the whole budget thinking and returns nothing.
    # Qwen3.8-27B burned all 700 tokens on reasoning for a three-sentence ad and
    # stopped on `length` with an empty message, so every listing failed as an
    # unparseable response. Measured 2026-09-03, the two documented ways to turn
    # thinking off are both ignored here: `enable_thinking: false` passed through
    # chat_template_kwargs, and a `/no_think` suffix on the user turn, each still
    # returned 700 reasoning tokens. Only the top-level `reasoning_effort` took
    # effect — same ad, 0 reasoning tokens, 124 completion tokens, 18s instead of
    # 105s. Left unset for models with no thinking mode, which have no use for it.
    effort = os.getenv("LLM_REASONING_EFFORT", "").strip()
    if effort:
        req_body["reasoning_effort"] = effort
    body = json.dumps(req_body).encode("utf-8")

    req = urllib.request.Request(_endpoint(), body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(
                req, timeout=timeout or float(os.getenv("LLM_TIMEOUT", "300"))) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        # The status line alone does not say what was wrong; the body carries the
        # reason (e.g. an exhausted context slot), which is what you need to act.
        detail = exc.read().decode("utf-8", "replace")[:300].strip()
        raise GateError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GateError(f"{_endpoint()}: {exc}") from exc

    try:
        raw = json.loads(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, ValueError) as exc:
        raise GateError(f"unparseable response: {exc}") from exc

    return _verify(raw, text, model)


def _verify(raw: dict, text: str, model: str) -> dict:
    """Drop every claim whose evidence is not literally in the ad."""
    hay = _norm(text)
    out = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "text_hash": text_hash(text),
        "evidence": {},
        "severity": None,
        "scope": None,
        "fabricated": [],
    }
    worst = 0
    for flag in FLAGS:
        item = raw.get(flag) or {}
        quote = (item.get("quote") or "").strip()
        value = bool(item.get("v"))

        if value and not quote:
            log.debug("%s claimed without a quote — dropped", flag)
            value = False
        elif value and _norm(quote) not in hay:
            # The model produced a sentence that reads like the ad but is not in
            # it. Treat the whole claim as unfounded rather than keeping a label
            # we cannot show a reason for.
            out["fabricated"].append(flag)
            log.warning("fabricated evidence for %s: %r", flag, quote[:80])
            value = False

        out[flag] = value
        if value:
            out["evidence"][flag] = quote
            rank = _SEVERITY_RANK.get(item.get("severity") or "", 0)
            if rank > worst:
                worst, out["severity"] = rank, item["severity"]
            # "part" only survives if every flag agrees it is partial; one
            # whole-property defect makes the property defective.
            scope = item.get("scope") or ""
            if scope == "whole" or out["scope"] is None:
                out["scope"] = scope or None

    if any(out[f] for f in FLAGS) and out["severity"] is None:
        # Evidenced but unrated: assume it matters rather than silently
        # downgrading it to cosmetic, which would let it through the filter.
        out["severity"] = "full"
    return out


def save(conn, listing_id: int, verdict: dict) -> None:
    conn.execute(
        """
        INSERT INTO listing_gate (listing_id, text_hash, model, prompt_version,
                                  unfinished, ruin, needs_adaptation,
                                  severity, scope, evidence, checked_at)
        VALUES (%(listing_id)s, %(text_hash)s, %(model)s, %(prompt_version)s,
                %(unfinished)s, %(ruin)s, %(needs_adaptation)s,
                %(severity)s, %(scope)s, %(evidence)s, now())
        ON CONFLICT (listing_id) DO UPDATE SET
            text_hash        = EXCLUDED.text_hash,
            model            = EXCLUDED.model,
            prompt_version   = EXCLUDED.prompt_version,
            unfinished       = EXCLUDED.unfinished,
            ruin             = EXCLUDED.ruin,
            needs_adaptation = EXCLUDED.needs_adaptation,
            severity         = EXCLUDED.severity,
            scope            = EXCLUDED.scope,
            evidence         = EXCLUDED.evidence,
            checked_at       = now()
        """,
        {**{k: verdict[k] for k in FLAGS},
         "listing_id": listing_id,
         "text_hash": verdict["text_hash"],
         "model": verdict["model"],
         "prompt_version": verdict["prompt_version"],
         "severity": verdict["severity"],
         "scope": verdict["scope"],
         "evidence": json.dumps(verdict["evidence"], ensure_ascii=False)},
    )


def pending(conn, limit: int | None = None) -> list[tuple[int, str]]:
    """Listings whose current text has no verdict from this prompt version.

    Keyed on the text, not the observation: a price change makes a new
    observation but says nothing new about condition, and re-running the model
    for it would be pure waste.
    """
    rows = conn.execute(
        """
        SELECT l.id,
               COALESCE(o.title, '') || ' :: ' || COALESCE(o.description, '') AS text
        FROM listings l
        JOIN v_current o ON o.listing_id = l.id
        LEFT JOIN listing_gate g ON g.listing_id = l.id
        WHERE l.status = 'active'
          AND COALESCE(o.description, '') <> ''
          AND (g.listing_id IS NULL
               OR g.prompt_version <> %s
               OR g.text_hash <> substr(encode(sha256(
                    (COALESCE(o.title,'') || ' :: ' || COALESCE(o.description,''))::bytea
                  ), 'hex'), 1, 32))
        ORDER BY l.id
        """,
        (PROMPT_VERSION,),
    ).fetchall()
    rows = [(r["id"], r["text"]) for r in rows]
    return rows[:limit] if limit else rows


def run(conn, limit: int | None = None, workers: int | None = None) -> dict:
    """Classify every listing whose ad text has no current verdict.

    The calls run concurrently because each one is ~15s of waiting on the model
    and the listings are independent. Only the HTTP calls are parallel: results
    are written from this thread on the single connection, which costs nothing
    (a commit is sub-millisecond against a 15s call) and avoids handing a
    psycopg connection to threads that would have to share it.

    GATE_WORKERS must stay at or below the number of context slots the server
    can actually hand out concurrently. That is NOT always the `PARALLEL` number
    reported by `lms ps`: measured against LM Studio reporting PARALLEL=4, three
    concurrent requests succeed and the fourth fails with "Context size has been
    exceeded" no matter how small max_tokens is. Overshooting does not queue --
    it fails the request outright, so the default is deliberately conservative.
    """
    todo = pending(conn, limit)
    # pending() opened a read transaction; the calls below take minutes, and
    # holding it open that long blocks vacuum on the observation tables.
    conn.commit()

    workers = max(1, workers or int(os.getenv("GATE_WORKERS", "3")))
    log.info("condition gate: %d listing(s) to classify, %d worker(s)",
             len(todo), workers)
    done = failed = fabricated = gated = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(classify, text): listing_id
                   for listing_id, text in todo}
        for future in as_completed(futures):
            listing_id = futures[future]
            try:
                verdict = future.result()
            except GateError as exc:
                failed += 1
                log.warning("listing %s: %s", listing_id, exc)
                continue
            if verdict["fabricated"]:
                fabricated += 1
            try:
                save(conn, listing_id, verdict)
                conn.commit()
            except Exception:
                conn.rollback()
                failed += 1
                log.exception("listing %s: could not store verdict", listing_id)
                continue
            done += 1
            if any(verdict[f] for f in FLAGS):
                gated += 1
            if done % 25 == 0:
                log.info("condition gate: %d/%d classified", done, len(todo))

    log.info("condition gate: %d classified, %d flagged, %d failed, %d with bad evidence",
             done, gated, failed, fabricated)
    return {"classified": done, "flagged": gated,
            "failed": failed, "fabricated": fabricated}
