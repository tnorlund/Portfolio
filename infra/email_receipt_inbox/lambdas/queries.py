"""Read-side queries for the email-receipt replica.

Vendored verbatim from receipts-email/emlrec/queries.py (the primary). The
replica Lambda answers the same read tools as the local stdio server, so keep
this file byte-identical to the source module apart from this docstring.
Regenerate with: cp ~/receipts-email/emlrec/queries.py <here> and re-add
this header.
"""
import json
import re


def _row(r):
    d = dict(r)
    for k in list(d):
        if k.endswith("_cents") and d[k] is not None:
            d[k[:-6]] = round(d[k] / 100, 2)
            del d[k]
    if d.get("extra"):
        try:
            d["extra"] = json.loads(d["extra"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def email_receipt_summaries(conn, merchant=None, grp=None, start_date=None,
                            end_date=None, min_total=None, max_total=None,
                            include_superseded=False, include_inflows=False,
                            limit=200, offset=0):
    where, args = ["1=1"], []
    if merchant:
        where.append("merchant_name LIKE ?")
        args.append(f"%{merchant}%")
    if grp:
        where.append("grp = ?")
        args.append(grp)
    if start_date:
        where.append("date >= ?")
        args.append(start_date)
    if end_date:
        where.append("date <= ?")
        args.append(end_date)
    if min_total is not None:
        where.append("grand_total_cents >= ?")
        args.append(int(min_total * 100))
    if max_total is not None:
        where.append("grand_total_cents <= ?")
        args.append(int(max_total * 100))
    if not include_superseded:
        where.append("superseded_by IS NULL")
    if not include_inflows:
        where.append("direction = 'outflow'")
    w = " AND ".join(where)
    agg = conn.execute(
        f"""SELECT COUNT(*) AS count, SUM(grand_total_cents) AS total_cents,
                   SUM(tax_cents) AS tax_cents, SUM(tip_cents) AS tip_cents
            FROM email_receipts WHERE {w}""", args).fetchone()
    rows = conn.execute(
        f"""SELECT message_id, grp, merchant_name, merchant_platform, date, order_id,
                   grand_total_cents, subtotal_cents, tax_cents, tip_cents, total_kind,
                   payment_type, card_last4, recon_scope, item_count
            FROM email_receipts WHERE {w}
            ORDER BY date DESC LIMIT ? OFFSET ?""", args + [limit, offset]).fetchall()
    count = agg["count"] or 0
    total = (agg["total_cents"] or 0) / 100
    return {
        "count": count,
        "total_spending": round(total, 2),
        "total_tax": round((agg["tax_cents"] or 0) / 100, 2),
        "total_tip": round((agg["tip_cents"] or 0) / 100, 2),
        "average_receipt": round(total / count, 2) if count else None,
        "filters": {"merchant": merchant, "group": grp,
                    "start_date": start_date, "end_date": end_date},
        "summaries": [_row(r) for r in rows],
    }


def email_receipt(conn, message_id):
    r = conn.execute("SELECT * FROM email_receipts WHERE message_id = ?",
                     (message_id,)).fetchone()
    if not r:
        like = conn.execute(
            "SELECT * FROM email_receipts WHERE message_id LIKE ? LIMIT 1",
            (f"%{message_id}%",)).fetchone()
        if not like:
            return {"error": f"no email receipt for message_id {message_id}"}
        r = like
    items = conn.execute(
        """SELECT line_no, description, quantity, unit_price_cents, total_cents, kind
           FROM receipt_items WHERE message_id = ? ORDER BY line_no""",
        (r["message_id"],)).fetchall()
    msg = conn.execute(
        """SELECT subject, from_addr, mbox_file, byte_offset, byte_length
           FROM messages WHERE message_id = ?""", (r["message_id"],)).fetchone()
    out = _row(r)
    out["items"] = [_row(i) for i in items]
    out["email"] = dict(msg) if msg else None
    out["matches"] = [dict(m) for m in conn.execute(
        """SELECT m.txn_id, m.score, m.status, c.description, c.txn_date,
                  c.amount_cents / -100.0 AS charged
           FROM matches m JOIN chase_transactions c ON c.txn_id = m.txn_id
           WHERE m.ref_kind = 'email' AND m.ref = ?""", (r["message_id"],))]
    return out


def search_receipts(conn, query, limit=25):
    """LIKE search over merchant, order id, and item descriptions — both sources."""
    q = f"%{query}%"
    email = conn.execute(
        """SELECT DISTINCT 'email' AS source, er.message_id AS ref, er.grp,
                  er.merchant_name, er.date, er.grand_total_cents, er.item_count
           FROM email_receipts er
           LEFT JOIN receipt_items ri ON ri.message_id = er.message_id
           WHERE er.merchant_name LIKE ? OR er.order_id LIKE ?
              OR ri.description LIKE ? OR er.extra LIKE ?
           ORDER BY er.date DESC LIMIT ?""", (q, q, q, q, limit)).fetchall()
    paper = conn.execute(
        """SELECT DISTINCT 'paper' AS source,
                  pr.image_id || ':' || pr.receipt_id AS ref, 'paper' AS grp,
                  pr.merchant_name, pr.date, pr.grand_total_cents, pr.item_count
           FROM paper_receipts pr
           LEFT JOIN paper_receipt_items pi
                  ON pi.image_id = pr.image_id AND pi.receipt_id = pr.receipt_id
           WHERE pr.merchant_name LIKE ? OR pi.description LIKE ?
           ORDER BY pr.date DESC LIMIT ?""", (q, q, limit)).fetchall()
    rows = [_row(r) for r in email] + [_row(r) for r in paper]
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return {"query": query, "count": len(rows), "results": rows[:limit]}


def _canon_expr(alias):
    return (f"COALESCE((SELECT canonical FROM merchant_canonical mc "
            f"WHERE mc.raw = {alias}.merchant_name), {alias}.merchant_name)")


def list_merchants(conn, grp=None, min_count=1, source="both"):
    out = {}
    if source in ("email", "both"):
        for r in conn.execute(
                f"""SELECT {_canon_expr('er')} m, COUNT(*) n, SUM(grand_total_cents) t
                    FROM email_receipts er
                    WHERE merchant_name IS NOT NULL AND superseded_by IS NULL
                      AND (? IS NULL OR grp = ?)
                    GROUP BY 1""", (grp, grp)):
            out[r["m"]] = {"merchant": r["m"], "email_receipts": r["n"],
                           "email_total": round((r["t"] or 0) / 100, 2),
                           "paper_receipts": 0, "paper_total": 0}
    if source in ("paper", "both"):
        for r in conn.execute(
                f"""SELECT {_canon_expr('pr')} m, COUNT(*) n, SUM(grand_total_cents) t
                    FROM paper_receipts pr
                    WHERE merchant_name IS NOT NULL GROUP BY 1"""):
            e = out.setdefault(r["m"], {"merchant": r["m"], "email_receipts": 0,
                                        "email_total": 0, "paper_receipts": 0,
                                        "paper_total": 0})
            e["paper_receipts"] = r["n"]
            e["paper_total"] = round((r["t"] or 0) / 100, 2)
    for v in out.values():
        cat = conn.execute(
            "SELECT category FROM merchant_canonical WHERE canonical = ? AND category IS NOT NULL LIMIT 1",
            (v["merchant"],)).fetchone()
        v["category"] = cat["category"] if cat else None
    rows = [v for v in out.values()
            if v["email_receipts"] + v["paper_receipts"] >= min_count]
    rows.sort(key=lambda v: -(v["email_receipts"] + v["paper_receipts"]))
    return {"merchants": rows}


def spend_summary(conn, start_date=None, end_date=None, period="month"):
    """Unified spend by period x source (email receipts / paper receipts / chase card)."""
    fmt = "%Y-%m" if period == "month" else "%Y"
    buckets = {}

    def bucket(date, source, cents_):
        if not date or cents_ is None:
            return
        if start_date and date < start_date or end_date and date > end_date:
            return
        key = date[:7] if period == "month" else date[:4]
        b = buckets.setdefault(key, {"period": key, "email": 0, "paper": 0, "chase_card": 0,
                                     "email_n": 0, "paper_n": 0, "chase_n": 0})
        b[source] += cents_
        b[source.replace("_card", "") + "_n" if source == "chase_card" else source + "_n"] += 1

    for r in conn.execute("""SELECT date, grand_total_cents FROM email_receipts
                             WHERE superseded_by IS NULL AND direction='outflow'
                               AND recon_scope != 'off_ledger' AND date IS NOT NULL"""):
        bucket(r["date"], "email", r["grand_total_cents"])
    for r in conn.execute("SELECT date, grand_total_cents FROM paper_receipts WHERE date IS NOT NULL"):
        bucket(r["date"], "paper", r["grand_total_cents"])
    for r in conn.execute("""SELECT txn_date, -amount_cents AS a FROM chase_transactions
                             WHERE is_card_purchase = 1"""):
        bucket(r["txn_date"], "chase_card", r["a"])

    rows = []
    for k in sorted(buckets):
        b = buckets[k]
        rows.append({"period": k,
                     "email_receipts": {"n": b["email_n"], "total": round(b["email"] / 100, 2)},
                     "paper_receipts": {"n": b["paper_n"], "total": round(b["paper"] / 100, 2)},
                     "chase_card_spend": {"n": b["chase_n"], "total": round(b["chase_card"] / 100, 2)}})
    return {"period": period, "rows": rows}


def coverage(conn, period="month", account=None, receiptable_only=False,
             start_date=None, end_date=None):
    """The headline metric: % of Chase card spend covered by a matched receipt
    (email or paper). Excludes tagged txns (dad/ignored/cash/off_ledger)."""
    where, args = ["c.is_card_purchase = 1", "g.tag IS NULL"], []
    if account:
        where.append("c.account = ?")
        args.append(account)
    if receiptable_only:
        where.append("c.txn_class = 'in-person'")
    if start_date:
        where.append("c.txn_date >= ?")
        args.append(start_date)
    if end_date:
        where.append("c.txn_date <= ?")
        args.append(end_date)
    key = "substr(c.txn_date, 1, 7)" if period == "month" else "substr(c.txn_date, 1, 4)"
    rows = conn.execute(
        f"""SELECT {key} AS period, c.account,
                   COUNT(*) AS txns,
                   SUM(-c.amount_cents) AS spend_cents,
                   SUM(CASE WHEN m.txn_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_txns,
                   SUM(CASE WHEN m.txn_id IS NOT NULL THEN -c.amount_cents ELSE 0 END) AS matched_cents
            FROM chase_transactions c
            LEFT JOIN txn_tags g ON g.txn_id = c.txn_id
            LEFT JOIN (SELECT DISTINCT txn_id FROM matches
                       WHERE status IN ('auto', 'confirmed')) m ON m.txn_id = c.txn_id
            WHERE {' AND '.join(where)}
            GROUP BY 1, 2 ORDER BY 1, 2""", args).fetchall()
    out = []
    for r in rows:
        spend = r["spend_cents"] or 0
        out.append({
            "period": r["period"], "account": r["account"],
            "txns": r["txns"], "matched_txns": r["matched_txns"],
            "spend": round(spend / 100, 2),
            "matched_spend": round((r["matched_cents"] or 0) / 100, 2),
            "coverage_by_count": round(r["matched_txns"] / r["txns"], 3) if r["txns"] else None,
            "coverage_by_spend": round((r["matched_cents"] or 0) / spend, 3) if spend else None,
        })
    return {"period": period, "receiptable_only": receiptable_only, "rows": out}


def unmatched(conn, kind="txns", account=None, start_date=None, end_date=None, limit=50):
    if kind == "txns":
        where, args = ["c.is_card_purchase = 1", "g.tag IS NULL", "m.txn_id IS NULL"], []
        if account:
            where.append("c.account = ?")
            args.append(account)
        if start_date:
            where.append("c.txn_date >= ?")
            args.append(start_date)
        if end_date:
            where.append("c.txn_date <= ?")
            args.append(end_date)
        rows = conn.execute(
            f"""SELECT c.txn_id, c.account, c.txn_date, c.description,
                       c.amount_cents / -100.0 AS amount, c.txn_class
                FROM chase_transactions c
                LEFT JOIN txn_tags g ON g.txn_id = c.txn_id
                LEFT JOIN (SELECT DISTINCT txn_id FROM matches
                           WHERE status IN ('auto', 'confirmed')) m ON m.txn_id = c.txn_id
                WHERE {' AND '.join(where)}
                ORDER BY c.txn_date DESC LIMIT ?""", args + [limit]).fetchall()
        return {"kind": kind, "count": len(rows), "rows": [dict(r) for r in rows]}
    # unmatched email receipts
    rows = conn.execute(
        """SELECT er.message_id, er.grp, er.merchant_name, er.date,
                  er.grand_total_cents / 100.0 AS grand_total, er.recon_scope
           FROM email_receipts er
           LEFT JOIN matches m ON m.ref_kind = 'email' AND m.ref = er.message_id
                AND m.status IN ('auto', 'confirmed')
           WHERE m.ref IS NULL AND er.superseded_by IS NULL
             AND er.direction = 'outflow' AND er.recon_scope = 'chase'
             AND (? IS NULL OR er.date >= ?) AND (? IS NULL OR er.date <= ?)
           ORDER BY er.date DESC LIMIT ?""",
        (start_date, start_date, end_date, end_date, limit)).fetchall()
    return {"kind": kind, "count": len(rows), "rows": [dict(r) for r in rows]}


_SQL_DENY = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace)\b", re.I)


def query_sql(conn, sql, limit=500):
    if _SQL_DENY.search(sql) or not re.match(r"\s*(select|with)\b", sql, re.I):
        return {"error": "read-only: only SELECT/WITH queries are allowed"}
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchmany(limit)
    return {"columns": cols, "row_count": len(rows),
            "rows": [list(r) for r in rows], "truncated": len(rows) == limit}


def ingest_status(conn):
    out = {"messages_by_group": {}, "receipts_by_group": {}, "classifications": {}}
    for r in conn.execute("SELECT grp, classification, COUNT(*) n FROM messages GROUP BY 1, 2"):
        out["messages_by_group"].setdefault(r["grp"], {})[r["classification"]] = r["n"]
        out["classifications"][r["classification"]] = \
            out["classifications"].get(r["classification"], 0) + r["n"]
    for r in conn.execute(
            """SELECT grp, COUNT(*) n,
                      SUM(CASE WHEN currency='USD' AND direction='outflow'
                          THEN grand_total_cents ELSE 0 END) t,
                      MIN(date) lo, MAX(date) hi
               FROM email_receipts WHERE superseded_by IS NULL GROUP BY grp"""):
        out["receipts_by_group"][r["grp"]] = {
            "receipts": r["n"], "total": round((r["t"] or 0) / 100, 2),
            "date_range": f"{r['lo']}..{r['hi']}"}
    for key in ("paper_receipts", "chase_transactions", "matches", "parse_failures"):
        out[key] = conn.execute(f"SELECT COUNT(*) FROM {key}").fetchone()[0]
    for r in conn.execute("SELECT key, value FROM meta"):
        out[r["key"]] = r["value"]
    return out
