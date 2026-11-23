from src.constants.enums import CLOSED_CLAIM_STATUSES
from src.utils.audit import record_in_transaction
from src.utils.db import get_conn

# Outcome of one clue/claim touched by a credential withdrawal.
OUTCOME_INVALIDATED = "invalidated"  # an accepted decision was revoked
OUTCOME_RETURNED = "returned"        # submission/decision sent back to awaiting evidence
OUTCOME_RETAINED = "retained"        # closed claim keeps its original decision


def is_credential_withdrawn(conn, evidence_source, evidence_reference):
    """True once the issuer has withdrawn this credential for every case."""
    return conn.execute(
        """SELECT 1 FROM evidence_withdrawals
           WHERE evidence_source=%s AND evidence_reference=%s""",
        (evidence_source, evidence_reference),
    ).fetchone() is not None


def lock_credential(conn, evidence_source, evidence_reference):
    """Serialise submissions and withdrawals for the same issuer credential.

    Held until the transaction ends. Submission takes it before the claim lock,
    withdrawal takes it before locking claims, so a review cannot be inserted
    after the withdrawal scan has already looked and missed it.
    """
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"{evidence_source}:{evidence_reference}",),
    )


def lock_claim_credentials(conn, claim_id):
    """Take credential locks an adjudication depends on, before the claim lock.

    A withdrawal registers its credential before locking claims. Adjudication
    takes the same credential lock first, so closing a claim and withdrawing a
    credential it used are ordered one way or the other: either the withdrawal
    lands first and the unresolved check blocks closing, or the claim closes
    first and the withdrawal records a retained decision. Locks are sorted to
    keep a stable order.
    """
    rows = conn.execute(
        """SELECT DISTINCT v.evidence_source, v.evidence_reference
           FROM clue_reviews v JOIN risk_clues r ON r.id=v.clue_id
           WHERE r.claim_id=%s
           ORDER BY v.evidence_source, v.evidence_reference""",
        (claim_id,),
    ).fetchall()
    for row in rows:
        lock_credential(conn, row["evidence_source"], row["evidence_reference"])


def _withdrawal_dict(row):
    return {
        "id": row["id"],
        "evidence_source": row["evidence_source"],
        "evidence_reference": row["evidence_reference"],
        "withdrawal_note": row["withdrawal_note"],
        "withdrawn_by": row["withdrawn_by"],
        "withdrawn_by_name": row["withdrawn_by_name"],
        "withdrawn_at": row["withdrawn_at"],
    }


def _impacts(conn, withdrawal_id):
    return [
        dict(row) for row in conn.execute(
            """SELECT i.*, c.claim_no, r.clue_type
               FROM evidence_withdrawal_impacts i
               JOIN claims c ON c.id=i.claim_id
               JOIN risk_clues r ON r.id=i.clue_id
               WHERE i.withdrawal_id=%s
               ORDER BY c.claim_no, i.clue_id""",
            (withdrawal_id,),
        ).fetchall()
    ]


def _review_note(evidence_reference, withdrawal_note):
    # review_note must stay within 6..500 characters.
    return f"发证方撤回凭证 {evidence_reference}：{withdrawal_note}"[:500]


def _apply(conn, review, user_id, note):
    if review["claim_status"] in CLOSED_CLAIM_STATUSES:
        return OUTCOME_RETAINED
    if review["status"] == "resolved":
        conn.execute(
            """UPDATE clue_reviews
               SET status='returned', reviewed_by=%s, reviewed_at=now(), review_note=%s
               WHERE clue_id=%s""",
            (user_id, note, review["clue_id"]),
        )
        conn.execute("UPDATE risk_clues SET status='open' WHERE id=%s", (review["clue_id"],))
        return OUTCOME_INVALIDATED
    if review["status"] == "pending":
        conn.execute(
            """UPDATE clue_reviews
               SET status='returned', reviewed_by=%s, reviewed_at=now(), review_note=%s
               WHERE clue_id=%s""",
            (user_id, note, review["clue_id"]),
        )
        return OUTCOME_RETURNED
    # Already awaiting supplemental evidence; only the withdrawal record is added.
    return OUTCOME_RETURNED


def _lock_affected_claims(conn, evidence_source, evidence_reference):
    """Lock every claim referencing the credential, in a stable order.

    Review submissions, decisions and adjudication all take the claim row lock
    first, so re-scanning until no new claims appear serialises them with us.
    """
    locked = set()
    while True:
        rows = conn.execute(
            """SELECT DISTINCT r.claim_id
               FROM clue_reviews v JOIN risk_clues r ON r.id=v.clue_id
               WHERE v.evidence_source=%s AND v.evidence_reference=%s""",
            (evidence_source, evidence_reference),
        ).fetchall()
        pending = sorted({row["claim_id"] for row in rows} - locked)
        if not pending:
            return
        for claim_id in pending:
            conn.execute("SELECT id FROM claims WHERE id=%s FOR UPDATE", (claim_id,))
        locked.update(pending)


def register_withdrawal(payload, user_id):
    with get_conn() as conn:
        lock_credential(conn, payload.evidence_source, payload.evidence_reference)
        inserted = conn.execute(
            """INSERT INTO evidence_withdrawals
                   (evidence_source, evidence_reference, withdrawal_note, withdrawn_by)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (evidence_source, evidence_reference) DO NOTHING
               RETURNING id""",
            (payload.evidence_source, payload.evidence_reference, payload.withdrawal_note, user_id),
        ).fetchone()

        if inserted is None:
            existing = conn.execute(
                """SELECT w.*, u.display_name AS withdrawn_by_name
                   FROM evidence_withdrawals w JOIN users u ON u.id=w.withdrawn_by
                   WHERE w.evidence_source=%s AND w.evidence_reference=%s""",
                (payload.evidence_source, payload.evidence_reference),
            ).fetchone()
            return {
                "withdrawal": _withdrawal_dict(existing),
                "impacts": _impacts(conn, existing["id"]),
                "duplicate": True,
            }

        withdrawal_id = inserted["id"]
        _lock_affected_claims(conn, payload.evidence_source, payload.evidence_reference)

        reviews = conn.execute(
            """SELECT v.clue_id, v.status, v.revision, v.recommendation, v.review_note,
                      v.reviewed_by, v.reviewed_at,
                      r.claim_id, r.clue_type, r.status AS clue_status,
                      c.claim_no, c.status AS claim_status
               FROM clue_reviews v
               JOIN risk_clues r ON r.id=v.clue_id
               JOIN claims c ON c.id=r.claim_id
               WHERE v.evidence_source=%s AND v.evidence_reference=%s
               ORDER BY r.claim_id, v.clue_id""",
            (payload.evidence_source, payload.evidence_reference),
        ).fetchall()

        note = _review_note(payload.evidence_reference, payload.withdrawal_note)
        for review in reviews:
            outcome = _apply(conn, review, user_id, note)
            conn.execute(
                """INSERT INTO evidence_withdrawal_impacts
                       (withdrawal_id, claim_id, clue_id, outcome, previous_review_status,
                        previous_clue_status, previous_review_note, previous_reviewed_by,
                        previous_reviewed_at, review_revision)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (withdrawal_id, review["claim_id"], review["clue_id"], outcome,
                 review["status"], review["clue_status"], review["review_note"],
                 review["reviewed_by"], review["reviewed_at"], review["revision"]),
            )
            record_in_transaction(conn, user_id, "evidence_credential_withdrawn", review["claim_id"], {
                "withdrawal_id": withdrawal_id,
                "clue_id": review["clue_id"],
                "evidence_source": payload.evidence_source,
                "evidence_reference": payload.evidence_reference,
                "outcome": outcome,
                "previous_review_status": review["status"],
            })

        withdrawal = conn.execute(
            """SELECT w.*, u.display_name AS withdrawn_by_name
               FROM evidence_withdrawals w JOIN users u ON u.id=w.withdrawn_by
               WHERE w.id=%s""",
            (withdrawal_id,),
        ).fetchone()
        return {
            "withdrawal": _withdrawal_dict(withdrawal),
            "impacts": _impacts(conn, withdrawal_id),
            "duplicate": False,
        }


def list_withdrawals():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT w.*, u.display_name AS withdrawn_by_name
               FROM evidence_withdrawals w JOIN users u ON u.id=w.withdrawn_by
               ORDER BY w.withdrawn_at DESC, w.id DESC LIMIT 50"""
        ).fetchall()
        return [
            {**_withdrawal_dict(row), "impacts": _impacts(conn, row["id"])}
            for row in rows
        ]
