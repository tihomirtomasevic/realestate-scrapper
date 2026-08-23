"""Scenario tests for dedup scoring.

Run either way:
    python3 -m crawler.test_dedup
    python3 crawler/test_dedup.py
"""
try:
    from . import dedup
except ImportError:          # executed as a plain script, not as a package module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import dedup

AGENCY_FOOTER = (
    "Agencijska provizija iznosi 1.5% od kupoprodajne cijene. "
    "Nekretnine d.o.o., Zagreb. Sve informacije na upit."
)

SCENARIOS = []


def scen(name, a, b, expect):
    SCENARIOS.append((name, a, b, expect))


# 1. Repost: same seller re-lists after 30 days to bump to the top.
scen(
    "repost (same seller, same photos, edited text)",
    dict(area_m2=64.3, price_eur=189000, rooms=2, floor="drugi kat", location_norm="Tresnjevka",
         seller_type="private", seller_name="Ivan H.", seller_phone="091/234-5678",
         phashes=["ff00aa11bb22cc33", "1122334455667788", "aabbccddeeff0011"],
         clean_text="Prodaje se stan na Tresnjevci, 64.3 m2, drugi kat, lift, balkon, uknjizen."),
    dict(area_m2=64.3, price_eur=189000, rooms=2, floor="2", location_norm="Tresnjevka",
         seller_type="private", seller_name="Ivan H.", seller_phone="0912345678",
         phashes=["ff00aa11bb22cc33", "1122334455667788", "aabbccddeeff0011"],
         clean_text="Prodaje se stan na Tresnjevci, 64.3 m2, drugi kat, lift, balkon, uknjizen. Hitno!"),
    "merge",
)

# 2. Cross-agency: two agencies, the owner's photos, completely different copy.
scen(
    "cross-agency same flat (different copy, shared photos)",
    dict(area_m2=64.3, price_eur=189000, rooms=2, location_norm="Tresnjevka",
         seller_name="Agencija A", seller_phone="011111111111",
         phashes=["ff00aa11bb22cc33", "1122334455667788"],
         clean_text="Nudimo na prodaju uknjizen dvosoban stan, drugi kat, lift."),
    dict(area_m2=64.3, price_eur=185000, rooms=2, location_norm="Tresnjevka",
         seller_name="Agencija B", seller_phone="022222222222",
         phashes=["ff00aa11bb22cc33", "1122334455667789"],
         clean_text="Ekskluzivna ponuda! Prostran stan u mirnoj ulici, blizu skole i vrtica."),
    "merge",
)

# 3. THE FALSE POSITIVE: two different units in the same new building.
#    Same agency, same round area, similar copy, ONE shared exterior photo.
scen(
    "same building, different flats (must NOT auto-merge)",
    dict(area_m2=65.0, price_eur=195000, rooms=2, floor="drugi kat", location_norm="Tresnjevka",
         seller_type="agency", seller_name="Novogradnja d.o.o.", seller_phone="033333333333",
         phashes=["deadbeefdeadbeef", "1111111111111111"],
         clean_text="Novogradnja Tresnjevka, stan 65 m2, drugi kat, terasa, garazno mjesto."),
    dict(area_m2=65.0, price_eur=197000, rooms=2, floor="cetvrti kat", location_norm="Tresnjevka",
         seller_type="agency", seller_name="Novogradnja d.o.o.", seller_phone="033333333333",
         phashes=["deadbeefdeadbeef", "2222222222222222"],
         clean_text="Novogradnja Tresnjevka, stan 65 m2, cetvrti kat, terasa, garazno mjesto."),
    # Near-identical template text, but the floor conflict plus only a shared
    # building exterior settles it: different units. Note strong interior-photo
    # overlap CAN still outweigh a floor conflict and reach 'review' -- see below.
    "distinct",
)

# 4. Location gate: everything matches, different city -> forced distinct.
scen(
    "location gate overrides all other evidence",
    dict(area_m2=64.3, price_eur=189000, rooms=2, location_norm="Tresnjevka",
         seller_name="X", seller_phone="044444444444",
         phashes=["ff00aa11bb22cc33", "1122334455667788"],
         clean_text="Identican tekst oglasa za stan od 64.3 m2."),
    dict(area_m2=64.3, price_eur=189000, rooms=2, location_norm="Split",
         seller_name="X", seller_phone="044444444444",
         phashes=["ff00aa11bb22cc33", "1122334455667788"],
         clean_text="Identican tekst oglasa za stan od 64.3 m2."),
    "distinct",
)

# 5. Unrelated flats from the same agency -> boilerplate must not fuse them.
scen(
    "unrelated flats, same agency (boilerplate stripped)",
    dict(area_m2=42.0, price_eur=120000, rooms=1, floor="prizemlje", location_norm="Maksimir",
         seller_type="agency", seller_name="Nekretnine d.o.o.", seller_phone="055555555555",
         phashes=["0000000000000000"],
         clean_text="Garsonijera kod Maksimira, prizemlje, potrebna adaptacija."),
    dict(area_m2=88.0, price_eur=260000, rooms=3, floor="peti kat", location_norm="Maksimir",
         seller_type="agency", seller_name="Nekretnine d.o.o.", seller_phone="055555555555",
         phashes=["ffffffffffffffff"],
         clean_text="Trosoban stan, peti kat, pogled na park, novija zgrada."),
    # A shared AGENCY phone identifies nothing, and area/rooms/floor all
    # contradict -- this must not reach the review queue at all.
    "distinct",
)


# 6. Conflicting evidence: photos say same flat, floor says otherwise (likely a
#    typo in one ad). Must NOT be discarded silently -- a human should look.
scen(
    "photos match but floor conflicts (needs a human)",
    dict(area_m2=64.3, price_eur=189000, rooms=2, floor="drugi kat", location_norm="Tresnjevka",
         seller_type="agency", seller_name="Agencija A", seller_phone="066666666666",
         phashes=["ff00aa11bb22cc33", "1122334455667788", "aabbccddeeff0011"],
         clean_text="Uknjizen dvosoban stan s balkonom."),
    dict(area_m2=64.3, price_eur=189000, rooms=2, floor="treci kat", location_norm="Tresnjevka",
         seller_type="agency", seller_name="Agencija B", seller_phone="077777777777",
         phashes=["ff00aa11bb22cc33", "1122334455667788", "aabbccddeeff0011"],
         clean_text="Prodaje se dvosoban stan, balkon, lift."),
    "review",
)


def main() -> int:
    failures = 0
    print(f"{'scenario':<52} {'score':>6}  {'got':<9} {'want':<9}")
    print("-" * 82)
    for name, a, b, expect in SCENARIOS:
        ev = dedup.score_pair(a, b)
        got = ev["decision"]
        ok = got == expect
        failures += not ok
        print(f"{name:<52} {ev['score']:>6.2f}  {got:<9} {expect:<9} {'ok' if ok else 'FAIL'}")

    # Boilerplate stripping actually reduces similarity between unrelated ads.
    t1 = "Garsonijera kod Maksimira, prizemlje. " + AGENCY_FOOTER
    t2 = "Trosoban stan, peti kat, pogled na park. " + AGENCY_FOOTER
    raw = dedup.jaccard(dedup.shingles(t1), dedup.shingles(t2))
    stripped = dedup.jaccard(
        dedup.shingles(dedup.strip_boilerplate(t1, [AGENCY_FOOTER])),
        dedup.shingles(dedup.strip_boilerplate(t2, [AGENCY_FOOTER])),
    )
    print(f"\nboilerplate: jaccard {raw:.3f} -> {stripped:.3f} after stripping")
    if stripped >= raw:
        print("FAIL: stripping did not reduce similarity")
        failures += 1

    # Diacritics and comma decimals must normalize.
    assert dedup.fold("Trešnjevka") == "tresnjevka"
    assert dedup.parse_area("65,50 m²") == 65.5
    assert dedup.normalize_phone("091/234-5678") == dedup.normalize_phone("+385 91 234 5678")

    # Blocking keys must be stable across processes (no salted hash()).
    keys = dedup.blocking_keys(dict(area_m2=64.3, location_norm="Tresnjevka",
                                    clean_text="stan na tresnjevci", phashes=["ff00aa11bb22cc33"]))
    print(f"blocking keys generated: {len(keys)} ({sorted({k for k, _ in keys})})")

    # Runaway guard.
    groups = dedup.cluster([(1, 2), (2, 3), (3, 4), (4, 5), (5, 6)], list(range(1, 8)))
    flagged = [g for g in groups.values() if g["status"] == "flagged"]
    print(f"runaway guard: {len(flagged)} cluster flagged (size {len(flagged[0]['members'])})"
          if flagged else "FAIL: runaway not flagged")
    failures += not flagged

    print("\n" + ("ALL PASS" if not failures else f"{failures} FAILURE(S)"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
