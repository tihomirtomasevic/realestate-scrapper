"""Runs duplicate detection over the database.

Stages (see PLAN.md): blocking -> pairwise scoring -> conservative clustering.
`dedup.py` holds the pure decision logic and its tests; this module is the part
that talks to Postgres.
"""
from __future__ import annotations

import json
import logging
import os

from . import dedup

log = logging.getLogger("crawler.dedup")


def _thresholds() -> tuple[float, float, int]:
    return (
        float(os.getenv("DEDUP_MERGE_AT", dedup.MERGE_AT)),
        float(os.getenv("DEDUP_REVIEW_AT", dedup.REVIEW_AT)),
        int(os.getenv("DEDUP_RUNAWAY_SIZE", dedup.RUNAWAY_SIZE)),
    )


def candidate_pairs(conn, listing_ids: list[int] | None = None) -> list[tuple[int, int]]:
    """Pairs sharing at least one blocking key.

    A self-join here is the whole reason blocking exists: it keeps this from
    becoming an all-pairs comparison as the table grows.
    """
    sql = """
        SELECT DISTINCT a.listing_id AS la, b.listing_id AS lb
        FROM blocking_keys a
        JOIN blocking_keys b
          ON a.key_type = b.key_type
         AND a.key_value = b.key_value
         AND a.listing_id < b.listing_id
    """
    params: tuple = ()
    if listing_ids:
        # Only pairs touching a listing we just crawled.
        sql += " WHERE a.listing_id = ANY(%s) OR b.listing_id = ANY(%s)"
        params = (listing_ids, listing_ids)
    return [(r["la"], r["lb"]) for r in conn.execute(sql, params).fetchall()]


def load_listings(conn, ids: list[int]) -> dict[int, dict]:
    """Current state of each listing, shaped for dedup.score_pair."""
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT l.id,
               o.title, o.description, o.price_eur, o.area_m2, o.rooms, o.floor,
               o.location_norm, o.seller_type, o.seller_name, o.seller_phone,
               COALESCE(ARRAY(
                   SELECT i.phash FROM images i
                   WHERE i.listing_id = l.id AND i.phash IS NOT NULL AND i.phash <> ''
                   ORDER BY i.position NULLS LAST, i.id
               ), '{}') AS phashes,
               COALESCE(ARRAY(
                   SELECT sb.snippet FROM seller_boilerplate sb
                   WHERE sb.seller_name = o.seller_name AND sb.source = l.source
               ), '{}') AS boilerplate
        FROM listings l
        JOIN v_current o ON o.listing_id = l.id
        WHERE l.id = ANY(%s)
        """,
        (ids,),
    ).fetchall()

    out: dict[int, dict] = {}
    for r in rows:
        rec = dict(r)
        rec["price_eur"] = float(r["price_eur"]) if r["price_eur"] is not None else None
        rec["area_m2"] = float(r["area_m2"]) if r["area_m2"] is not None else None
        rec["rooms"] = float(r["rooms"]) if r["rooms"] is not None else None
        # Boilerplate must be stripped before any text comparison, or unrelated
        # ads from one agency look near-identical through their shared footer.
        rec["clean_text"] = dedup.strip_boilerplate(
            r["description"] or "", r["boilerplate"] or [])
        out[r["id"]] = rec
    return out


def score_and_store(conn, pairs: list[tuple[int, int]]) -> dict:
    merge_at, review_at, _ = _thresholds()
    ids = sorted({i for pair in pairs for i in pair})
    data = load_listings(conn, ids)
    counts = {"merge": 0, "review": 0, "distinct": 0, "scored": 0}

    for a_id, b_id in pairs:
        a, b = data.get(a_id), data.get(b_id)
        if not a or not b:
            continue
        ev = dedup.score_pair(a, b)

        # Honour the configured thresholds rather than the module defaults.
        if ev["location_conflict"]:
            decision = "distinct"
        elif ev["score"] >= merge_at:
            decision = "merge"
        elif ev["score"] >= review_at:
            decision = "review"
        else:
            decision = "distinct"

        conn.execute(
            """
            INSERT INTO dedup_pairs (
                listing_a, listing_b, score, decision, img_match_count,
                area_delta, area_precise, price_delta_pct, text_jaccard,
                phone_match, same_seller, floor_conflict, location_conflict,
                evidence_json)
            VALUES (%(a)s, %(b)s, %(score)s, %(decision)s, %(imgs)s,
                    %(area_delta)s, %(area_precise)s, %(price_delta)s, %(text)s,
                    %(phone)s, %(seller)s, %(floor)s, %(loc)s, %(ev)s)
            ON CONFLICT (listing_a, listing_b) DO UPDATE SET
                score = EXCLUDED.score,
                -- A human decision outranks any later rescore.
                decision = CASE WHEN dedup_pairs.human_label IS NULL
                                THEN EXCLUDED.decision ELSE dedup_pairs.decision END,
                img_match_count = EXCLUDED.img_match_count,
                area_delta = EXCLUDED.area_delta,
                area_precise = EXCLUDED.area_precise,
                price_delta_pct = EXCLUDED.price_delta_pct,
                text_jaccard = EXCLUDED.text_jaccard,
                phone_match = EXCLUDED.phone_match,
                same_seller = EXCLUDED.same_seller,
                floor_conflict = EXCLUDED.floor_conflict,
                location_conflict = EXCLUDED.location_conflict,
                evidence_json = EXCLUDED.evidence_json
            """,
            {"a": a_id, "b": b_id, "score": ev["score"], "decision": decision,
             "imgs": ev["img_match_count"], "area_delta": ev.get("area_delta"),
             "area_precise": bool(ev.get("area_precise")),
             "price_delta": ev.get("price_delta_pct"), "text": ev.get("text_jaccard"),
             "phone": bool(ev.get("phone_match")), "seller": bool(ev.get("same_seller")),
             "floor": bool(ev.get("floor_conflict")), "loc": bool(ev["location_conflict"]),
             "ev": json.dumps(ev)},
        )
        counts[decision] += 1
        counts["scored"] += 1

    conn.commit()
    return counts


def rebuild_clusters(conn) -> dict:
    """Union-find over confirmed-and-scored merge edges.

    Transitivity is the hazard here: A~B and B~C does not imply A~C. Groups that
    grow past the runaway guard are marked 'flagged' rather than trusted, and a
    human 'different' label is treated as a hard barrier.
    """
    _, _, runaway = _thresholds()

    edges = [
        (r["listing_a"], r["listing_b"])
        for r in conn.execute(
            """
            SELECT listing_a, listing_b FROM dedup_pairs
            WHERE human_label = 'same'
               OR (decision = 'merge' AND human_label IS NULL)
            """
        ).fetchall()
    ]
    all_ids = [r["id"] for r in conn.execute("SELECT id FROM listings").fetchall()]
    groups = dedup.cluster(edges, all_ids)

    assigned = flagged = 0
    for group in groups.values():
        members = group["members"]
        if len(members) < 2:
            continue

        # Reuse an existing cluster id where members already have one, so
        # human-confirmed clusters keep their identity across reruns.
        existing = [
            r["cluster_id"] for r in conn.execute(
                "SELECT DISTINCT cluster_id FROM listings "
                "WHERE id = ANY(%s) AND cluster_id IS NOT NULL", (members,)
            ).fetchall()
        ]
        if existing:
            cluster_id = min(existing)
            stale = [c for c in existing if c != cluster_id]
            if stale:
                conn.execute(
                    "UPDATE listings SET cluster_id = %s WHERE cluster_id = ANY(%s)",
                    (cluster_id, stale))
                conn.execute("DELETE FROM clusters WHERE id = ANY(%s)", (stale,))
        else:
            cluster_id = conn.execute(
                "INSERT INTO clusters (status) VALUES ('auto') RETURNING id"
            ).fetchone()["id"]

        conn.execute("UPDATE listings SET cluster_id = %s WHERE id = ANY(%s)",
                     (cluster_id, members))

        status = "flagged" if len(members) > runaway else None
        if status:
            flagged += 1
        conn.execute(
            """
            UPDATE clusters SET
                size = %(size)s,
                -- Casts required: Postgres cannot infer a bare parameter's type
                -- from `$n IS NOT NULL` alone (AmbiguousParameter).
                status = CASE
                    WHEN %(status)s::text IS NOT NULL THEN %(status)s::text
                    -- never downgrade a cluster a human confirmed
                    WHEN status = 'confirmed' THEN 'confirmed'
                    ELSE 'auto' END,
                canonical_listing_id = (
                    SELECT id FROM listings WHERE cluster_id = %(cid)s
                    ORDER BY first_seen, id LIMIT 1),
                updated_at = now()
            WHERE id = %(cid)s
            """,
            {"cid": cluster_id, "size": len(members), "status": status},
        )
        assigned += len(members)

    # Listings no longer in any multi-member group must not keep a stale id.
    grouped = [i for g in groups.values() if len(g["members"]) > 1 for i in g["members"]]
    conn.execute(
        "UPDATE listings SET cluster_id = NULL "
        "WHERE cluster_id IS NOT NULL AND NOT (id = ANY(%s))", (grouped or [0],))
    conn.execute(
        "DELETE FROM clusters WHERE id NOT IN "
        "(SELECT DISTINCT cluster_id FROM listings WHERE cluster_id IS NOT NULL)")
    conn.commit()

    return {"clusters": sum(1 for g in groups.values() if len(g["members"]) > 1),
            "listings_clustered": assigned, "flagged": flagged}


def run(conn, listing_ids: list[int] | None = None) -> dict:
    pairs = candidate_pairs(conn, listing_ids)
    log.info("dedup: %d candidate pair(s) from blocking keys", len(pairs))
    scored = score_and_store(conn, pairs)
    clusters = rebuild_clusters(conn)
    result = {**scored, **clusters}
    log.info("dedup: %s", result)
    return result
