"""Review queue for pairs the scorer was not confident about.

Your labels here are the training data for retuning the weights — that is why
dedup_pairs stores each signal as its own column.
"""
from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..schemas import LinkRequest, ReviewLabel, ReviewPair

router = APIRouter(prefix="/api/dedup", tags=["dedup"])


@router.get("/review", response_model=list[ReviewPair])
async def review_queue(limit: int = Query(50, ge=1, le=200)):
    rows = await db.fetch_all(
        """
        SELECT p.id AS pair_id, p.score, p.listing_a, p.listing_b,
               p.img_match_count, p.text_jaccard, p.floor_conflict, p.phone_match,
               p.evidence_json AS evidence,
               la.url AS url_a, lb.url AS url_b,
               oa.title AS title_a, ob.title AS title_b,
               oa.price_eur AS price_a, ob.price_eur AS price_b,
               oa.area_m2 AS area_a, ob.area_m2 AS area_b
        FROM dedup_pairs p
        JOIN listings la ON la.id = p.listing_a
        JOIN listings lb ON lb.id = p.listing_b
        JOIN v_current oa ON oa.listing_id = la.id
        JOIN v_current ob ON ob.listing_id = lb.id
        WHERE p.decision = 'review' AND p.human_label IS NULL
        ORDER BY p.score DESC
        LIMIT %(limit)s
        """,
        {"limit": limit},
    )
    return [ReviewPair(**r) for r in rows]


@router.post("/link", response_model=dict)
async def link_listings(body: LinkRequest):
    """Mark two listings as the same property by hand.

    Some duplicates carry no machine-detectable signal: different agencies
    photograph the house separately, write their own copy and state slightly
    different areas, leaving only a round asking price that dozens of unrelated
    ads share. A human recognises it instantly. Recording that here both fixes
    the data and stores a labelled example for re-tuning the weights.
    """
    a, b = sorted((body.listing_a, body.listing_b))
    if a == b:
        raise HTTPException(422, "a listing cannot duplicate itself")

    found = await db.fetch_all(
        "SELECT id FROM listings WHERE id = ANY(%(ids)s)", {"ids": [a, b]})
    if len(found) != 2:
        raise HTTPException(404, "one or both listings do not exist")

    async with db.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO dedup_pairs (listing_a, listing_b, score, decision,
                                         human_label, labeled_at, evidence_json)
                VALUES (%(a)s, %(b)s, 1.0, 'merge', %(label)s, now(),
                        '{"source": "manual"}'::jsonb)
                ON CONFLICT (listing_a, listing_b) DO UPDATE
                    SET human_label = EXCLUDED.human_label,
                        labeled_at = now(),
                        decision = CASE WHEN %(label)s = 'same'
                                        THEN 'merge' ELSE 'distinct' END
                """,
                {"a": a, "b": b, "label": body.label},
            )
            if body.label == "same":
                await _merge(conn, a, b)
            else:
                await conn.execute(
                    "UPDATE listings SET cluster_id = NULL WHERE id = %(b)s "
                    "AND cluster_id = (SELECT cluster_id FROM listings WHERE id = %(a)s)",
                    {"a": a, "b": b})
    return {"ok": True, "listing_a": a, "listing_b": b, "label": body.label}


@router.post("/review/{pair_id}")
async def label_pair(pair_id: int, body: ReviewLabel):
    if body.label not in ("same", "different"):
        raise HTTPException(422, "label must be 'same' or 'different'")

    pair = await db.fetch_one(
        "SELECT listing_a, listing_b FROM dedup_pairs WHERE id = %(id)s", {"id": pair_id})
    if not pair:
        raise HTTPException(404, "pair not found")

    async with db.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE dedup_pairs SET human_label = %(label)s, labeled_at = now() "
                "WHERE id = %(id)s",
                {"label": body.label, "id": pair_id},
            )
            if body.label == "same":
                await _merge(conn, pair["listing_a"], pair["listing_b"])
            else:
                # Split only what this pair asserts; other members of either
                # cluster keep their own (independently scored) membership.
                await conn.execute(
                    "UPDATE listings SET cluster_id = NULL WHERE id = %(b)s "
                    "AND cluster_id = (SELECT cluster_id FROM listings WHERE id = %(a)s)",
                    {"a": pair["listing_a"], "b": pair["listing_b"]},
                )
    return {"ok": True, "label": body.label}


async def _merge(conn, a: int, b: int) -> None:
    """Put both listings in one cluster, keeping the older cluster id."""
    cur = await conn.execute(
        "SELECT id, cluster_id FROM listings WHERE id IN (%(a)s, %(b)s)",
        {"a": a, "b": b},
    )
    rows = {r["id"]: r["cluster_id"] for r in await cur.fetchall()}
    ca, cb = rows.get(a), rows.get(b)

    if ca and cb and ca != cb:
        keep, drop = min(ca, cb), max(ca, cb)
        await conn.execute(
            "UPDATE listings SET cluster_id = %(keep)s WHERE cluster_id = %(drop)s",
            {"keep": keep, "drop": drop})
        await conn.execute("DELETE FROM clusters WHERE id = %(drop)s", {"drop": drop})
        target = keep
    elif ca or cb:
        target = ca or cb
        await conn.execute(
            "UPDATE listings SET cluster_id = %(c)s WHERE id IN (%(a)s, %(b)s)",
            {"c": target, "a": a, "b": b})
    else:
        cur = await conn.execute(
            "INSERT INTO clusters (status) VALUES ('confirmed') RETURNING id")
        target = (await cur.fetchone())["id"]
        await conn.execute(
            "UPDATE listings SET cluster_id = %(c)s WHERE id IN (%(a)s, %(b)s)",
            {"c": target, "a": a, "b": b})

    await conn.execute(
        """
        UPDATE clusters SET
            status = 'confirmed',
            size = (SELECT COUNT(*) FROM listings WHERE cluster_id = %(c)s),
            canonical_listing_id = COALESCE(canonical_listing_id,
                (SELECT id FROM listings WHERE cluster_id = %(c)s
                 ORDER BY first_seen, id LIMIT 1)),
            updated_at = now()
        WHERE id = %(c)s
        """,
        {"c": target},
    )
