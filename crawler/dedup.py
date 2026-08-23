"""Duplicate detection: blocking -> pairwise scoring -> conservative clustering.

Why not embeddings as the primary signal: "novogradnja, Trešnjevka, 65m2, 2s+db"
describes twenty different flats in one building. They are semantically ~identical
and genuinely different listings. Near-duplicate TEXT matching (MinHash over
character shingles) detects lightly-edited copies -- which is what a repost is --
without collapsing distinct units. Embeddings stay an optional tiebreak
(`embed_cosine`), not a driver.

Errors are asymmetric: a false merge silently HIDES a listing from you, a false
split just shows the same flat twice. Thresholds are biased toward splitting.
"""
from __future__ import annotations

import re
import unicodedata
from zlib import crc32

# ── Croatian text normalization ─────────────────────────────────────────────
# đ/Đ do not decompose under NFKD, so they need an explicit mapping.
_EXPLICIT = str.maketrans({"đ": "d", "Đ": "D", "ß": "ss"})


def fold(text: str) -> str:
    """Lowercase, strip diacritics (Trešnjevka -> tresnjevka), squash whitespace."""
    if not text:
        return ""
    text = text.translate(_EXPLICIT)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


def location_key(raw) -> str | None:
    """Reduce a location to its most specific place name.

    Sources describe the same spot at different granularity — one returns
    ["COUNTRY","Region","Municipality","Village"], another the string
    "Region, Municipality, Village". Comparing those whole makes two
    ads for the same house look like different places, which both breaks the
    blocking key and trips the location gate. The last component is the
    narrowest one both agree on.
    """
    if raw is None:
        return None
    parts = list(raw) if isinstance(raw, (list, tuple)) else str(raw).split(",")
    parts = [fold(str(p)) for p in parts]
    parts = [p for p in parts if p and p not in ("hrvatska", "croatia")]
    return parts[-1] if parts else None


def parse_area(raw) -> float | None:
    """'65,50 m²' | '65.5 m2' | 64.3 -> 65.5 / 64.3. Croatian uses comma decimals."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(raw).replace(" ", ""))
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def is_precise(area: float | None) -> bool:
    """64.3 is a fingerprint; 65.0 is not -- round numbers are weak evidence."""
    if area is None:
        return False
    return abs(area - round(area)) > 0.05


# Croatian floor labels. In a new building, floor is THE discriminator between
# otherwise identical units, so it must parse to something comparable.
_FLOOR_WORDS = {
    "podrum": -2, "suteren": -1, "prizemlje": 0, "visoko prizemlje": 0,
    "prvi": 1, "drugi": 2, "treci": 3, "cetvrti": 4, "peti": 5, "sesti": 6,
    "sedmi": 7, "osmi": 8, "deveti": 9, "deseti": 10,
    "potkrovlje": "attic", "penthouse": "attic", "tavan": "attic",
}


def parse_floor(raw):
    """'drugi kat' | '2' | 'prizemlje' | 'potkrovlje' -> 2 | 2 | 0 | 'attic'."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    t = fold(str(raw))
    if not t:
        return None
    for word, val in _FLOOR_WORDS.items():
        if word in t:
            return val
    m = re.search(r"(-?\d+)", t)
    return int(m.group(1)) if m else None


def normalize_phone(raw: str | None) -> str | None:
    """Croatian numbers to a comparable form: 091/234-5678 -> 385912345678."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    digits = digits.lstrip("0")
    if not digits.startswith("385"):
        digits = "385" + digits
    return digits if len(digits) >= 11 else None


# ── Text near-duplicate (MinHash over character shingles) ───────────────────
SHINGLE_K = 5
_SEEDS = tuple(crc32(f"adcrawler-minhash-{i}".encode()) for i in range(64))


def shingles(text: str, k: int = SHINGLE_K) -> set[str]:
    t = fold(text)
    if len(t) < k:
        return {t} if t else set()
    return {t[i : i + k] for i in range(len(t) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def minhash(sh: set[str]) -> list[int]:
    """Stable across processes: crc32, never Python's salted hash()."""
    if not sh:
        return []
    hashed = [crc32(s.encode()) for s in sh]
    return [min(h ^ seed for h in hashed) for seed in _SEEDS]


def text_bands(sig: list[int], rows: int = 4) -> list[str]:
    """LSH bands -> blocking keys. Two similar texts collide in >=1 band."""
    return [
        f"{i}:{'-'.join(str(x) for x in sig[i:i + rows])}"
        for i in range(0, len(sig) - rows + 1, rows)
    ]


def strip_boilerplate(text: str, snippets) -> str:
    """Remove recurring per-seller footers before comparing.

    Without this, two UNRELATED flats from the same agency score high on any
    text metric, because they share the agency's standard footer.
    """
    out = text or ""
    for s in sorted(snippets or [], key=len, reverse=True):
        if s:
            out = out.replace(s, " ")
    return re.sub(r"\s+", " ", out).strip()


# ── Image perceptual hashes ─────────────────────────────────────────────────
PHASH_MAX_DIST = 8


def phash_distance(a: str, b: str) -> int:
    """Hamming distance between two hex phashes."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def phash_bands(phash: str, bands: int = 4) -> list[str]:
    """Split a 64-bit hash into 16-bit bands; near-identical images share one."""
    v = int(phash, 16)
    width = 64 // bands
    return [f"{i}:{(v >> (i * width)) & ((1 << width) - 1):04x}" for i in range(bands)]


def count_image_matches(a_hashes, b_hashes) -> int:
    """Greedy 1:1 pairing of images within the Hamming threshold."""
    used, matches = set(), 0
    for ha in a_hashes or []:
        for j, hb in enumerate(b_hashes or []):
            if j not in used and phash_distance(ha, hb) <= PHASH_MAX_DIST:
                used.add(j)
                matches += 1
                break
    return matches


# ── Stage 1: blocking keys ──────────────────────────────────────────────────
def blocking_keys(listing: dict) -> list[tuple[str, str]]:
    """Cheap keys for candidate generation. Any shared key => a candidate pair."""
    keys: list[tuple[str, str]] = []

    area = parse_area(listing.get("area_m2"))
    place = (listing.get("location_norm")
             or location_key(listing.get("location_raw")) or "")
    if area:
        keys.append(("area_city", f"{round(area)}|{place}"))
        # Agencies measure the same house differently (net vs gross, rounding),
        # so an exact-area bucket alone misses genuine duplicates. A coarse
        # band lets neighbouring measurements meet; both edges are emitted so
        # values either side of a boundary still collide.
        band = area / 10.0
        for b in {int(band), round(band)}:
            keys.append(("area_band", f"{b}|{place}"))

    # Price is the sturdiest numeric field across agencies: they quote the
    # owner's asking figure verbatim, while area gets re-measured.
    price = listing.get("price_eur")
    if price:
        keys.append(("price_place", f"{round(float(price))}|{place}"))

    for ph in listing.get("phashes") or []:
        keys += [("phash_band", b) for b in phash_bands(ph)]

    phone = normalize_phone(listing.get("seller_phone"))
    if phone:
        keys.append(("phone", phone))

    text = listing.get("clean_text") or listing.get("description") or ""
    if text:
        keys += [("text_band", b) for b in text_bands(minhash(shingles(text)))]

    return keys


# ── Stage 2/3: pairwise scoring and decision ────────────────────────────────
MERGE_AT = 0.75
REVIEW_AT = 0.45


def score_pair(a: dict, b: dict) -> dict:
    """Weighted evidence. Location is a GATE, not a weight: a contradiction
    forces 'distinct' no matter how well everything else lines up."""
    ev: dict = {}
    score = 0.0

    # GATE — different known neighbourhoods can never be the same property.
    loc_a = fold(a.get("location_norm") or "")
    loc_b = fold(b.get("location_norm") or "")
    conflict = bool(loc_a and loc_b and loc_a != loc_b)
    ev["location_conflict"] = int(conflict)

    # Images: the strongest signal. Two+ matches is near-certainty; a single
    # match is often just a shared building exterior, so it is worth far less.
    imgs = count_image_matches(a.get("phashes"), b.get("phashes"))
    ev["img_match_count"] = imgs
    if imgs >= 2:
        score += 0.55
    elif imgs == 1:
        score += 0.25

    # Area: exact match only counts fully when the number is precise.
    # A contradiction beyond tolerance is evidence AGAINST, not merely absent.
    area_a, area_b = parse_area(a.get("area_m2")), parse_area(b.get("area_m2"))
    precise = is_precise(area_a) and is_precise(area_b)
    ev["area_precise"] = int(precise)
    if area_a and area_b:
        delta = abs(area_a - area_b)
        ev["area_delta"] = round(delta, 2)
        pct = delta / max(area_a, area_b)
        if delta <= 0.15:
            score += 0.30 if precise else 0.12
        elif pct <= 0.02:
            score += 0.10
        elif pct <= 0.06:
            # Within measurement noise between agencies — neither evidence for
            # nor against. Penalising here was hiding real cross-agency
            # duplicates whose stated areas differed by a few percent.
            pass
        else:
            score -= 0.25
    else:
        ev["area_delta"] = None

    # Floor: in one building this is the discriminator between identical units.
    fa, fb = parse_floor(a.get("floor")), parse_floor(b.get("floor"))
    ev["floor_conflict"] = int(fa is not None and fb is not None and fa != fb)
    if ev["floor_conflict"]:
        score -= 0.45
    elif fa is not None and fa == fb:
        score += 0.05

    # Phone: nails private-seller reposts -- but an AGENCY number is the same
    # switchboard on every one of their listings, so it identifies nothing.
    pa, pb = normalize_phone(a.get("seller_phone")), normalize_phone(b.get("seller_phone"))
    both_agency = a.get("seller_type") == "agency" and b.get("seller_type") == "agency"
    ev["phone_match"] = int(bool(pa and pa == pb))
    if ev["phone_match"] and not both_agency:
        score += 0.45

    # Text near-duplicate, on boilerplate-stripped copy.
    ta = shingles(a.get("clean_text") or a.get("description") or "")
    tb = shingles(b.get("clean_text") or b.get("description") or "")
    tj = jaccard(ta, tb)
    ev["text_jaccard"] = round(tj, 3)
    if tj >= 0.80:
        score += 0.35
    elif tj >= 0.50:
        score += 0.15

    # Price. Being *near* is weak — genuine duplicates differ by the agency fee.
    # Being EXACT is not: two different houses rarely carry the same asking
    # price to the euro, and when two agencies list the same owner's property
    # they quote the owner's number verbatim.
    # Measured on real data: 179 pairs share an exact price + settlement and
    # only ~20% are true duplicates — agencies price at round numbers, so
    # "270000 in one small town" alone covered 8 unrelated houses. Worth a nudge and
    # a blocking key, not much more. The discriminating part is the conjunction
    # with area and room count below.
    pra, prb = a.get("price_eur"), b.get("price_eur")
    exact_price = False
    if pra and prb:
        pct = abs(pra - prb) / max(pra, prb)
        ev["price_delta_pct"] = round(pct, 4)
        exact_price = pct <= 0.002
        if exact_price:
            score += 0.12
        elif pct <= 0.03:
            score += 0.08
    else:
        ev["price_delta_pct"] = None

    # Agreement across several independent fields at once is worth more than
    # the sum of its parts: each could coincide alone, but price AND area AND
    # room count matching exactly is a different order of coincidence. Requires
    # a shared specific location, so it cannot fire across towns.
    # Any explicit contradiction voids it: the argument is "everything agrees",
    # which is simply untrue once a field disagrees. Without this guard a floor
    # conflict could be outvoted and silently auto-merged.
    exact_area = bool(
        area_a and area_b
        and abs(area_a - area_b) / max(area_a, area_b) <= 0.02)
    same_rooms = bool(a.get("rooms") and a.get("rooms") == b.get("rooms"))
    same_place = bool(loc_a and loc_a == loc_b)
    no_conflict = not ev["floor_conflict"]
    ev["exact_trio"] = int(bool(
        exact_price and exact_area and same_rooms and same_place and no_conflict))
    if ev["exact_trio"]:
        score += 0.18

    if a.get("rooms") and b.get("rooms"):
        if a["rooms"] == b["rooms"]:
            score += 0.05
        else:
            score -= 0.20
    ev["same_seller"] = int(bool(a.get("seller_name") and a.get("seller_name") == b.get("seller_name")))
    if ev["same_seller"]:
        score += 0.05

    ev["score"] = round(score, 3)
    if conflict:
        ev["decision"] = "distinct"
    elif score >= MERGE_AT:
        ev["decision"] = "merge"
    elif score >= REVIEW_AT:
        ev["decision"] = "review"
    else:
        ev["decision"] = "distinct"
    return ev


# ── Stage 4: clustering with a runaway guard ────────────────────────────────
RUNAWAY_SIZE = 5


def cluster(pairs, listing_ids):
    """Union-find over 'merge' edges only.

    Transitivity is the hazard: A~B and B~C does not imply A~C. Clusters that
    grow past RUNAWAY_SIZE are returned flagged for review rather than trusted.
    """
    parent = {i: i for i in listing_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    groups: dict[int, list[int]] = {}
    for i in listing_ids:
        groups.setdefault(find(i), []).append(i)
    return {
        root: {"members": sorted(m), "status": "flagged" if len(m) > RUNAWAY_SIZE else "auto"}
        for root, m in groups.items()
    }
