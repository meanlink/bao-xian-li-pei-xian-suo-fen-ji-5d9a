from src.constants.enums import CLUE_REVIEW_CLAIM_STATUSES, CLUE_REVIEW_OPEN_STATUSES
from src.services.evidence_withdrawal_service import is_credential_withdrawn, lock_credential
from src.utils.audit import record_in_transaction
from src.utils.db import get_conn


class MissingClue(LookupError):
    pass


def list_clue_reviews(claim_id=None):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT r.id AS clue_id, r.claim_id, c.claim_no, c.status AS claim_status,
                   r.clue_type, r.severity, r.description, r.evidence, r.status AS clue_status,
                   v.status AS review_status, v.evidence_source, v.evidence_reference, v.finding,
                   v.recommendation, v.revision, v.submitted_by, v.submitted_at, v.review_note,
                   v.reviewed_at, investigator.display_name AS submitter, manager.display_name AS reviewer,
                   (w.id IS NOT NULL) AS credential_withdrawn
            FROM risk_clues r JOIN claims c ON c.id = r.claim_id
            LEFT JOIN clue_reviews v ON v.clue_id = r.id
            LEFT JOIN evidence_withdrawals w
                   ON w.evidence_source = v.evidence_source AND w.evidence_reference = v.evidence_reference
            LEFT JOIN users investigator ON investigator.id = v.submitted_by
            LEFT JOIN users manager ON manager.id = v.reviewed_by
            WHERE (%(claim)s::integer IS NULL OR r.claim_id = %(claim)s)
            ORDER BY CASE v.status WHEN 'pending' THEN 0 WHEN 'returned' THEN 1 ELSE 2 END,
                     CASE r.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                     r.id DESC
            """,
            {"claim": claim_id},
        ).fetchall()


def get_review_for_update(conn, clue_id):
    # Take the claim row lock first, then the clue, matching the order used by
    # credential withdrawal, so the two paths cannot deadlock.
    clue_row = conn.execute("SELECT claim_id FROM risk_clues WHERE id = %s", (clue_id,)).fetchone()
    if clue_row is None:
        raise MissingClue("风险线索不存在")
    claim = conn.execute(
        "SELECT status AS claim_status FROM claims WHERE id = %s FOR UPDATE",
        (clue_row["claim_id"],),
    ).fetchone()
    clue = conn.execute(
        "SELECT status AS clue_status FROM risk_clues WHERE id = %s FOR UPDATE",
        (clue_id,),
    ).fetchone()
    if claim["claim_status"] not in CLUE_REVIEW_CLAIM_STATUSES or clue["clue_status"] != "open":
        raise PermissionError("仅调查中或待核赔案件的待核查线索可以进行证据复核")
    review = conn.execute("SELECT * FROM clue_reviews WHERE clue_id = %s", (clue_id,)).fetchone()
    return {"claim_id": clue_row["claim_id"], **claim, **clue}, review


def submit_review(clue_id, payload, user_id):
    with get_conn() as conn:
        # Take the credential lock before the claim lock so a concurrent
        # withdrawal cannot scan the credential between our check and insert.
        lock_credential(conn, payload.evidence_source, payload.evidence_reference)
        clue, review = get_review_for_update(conn, clue_id)
        if is_credential_withdrawn(conn, payload.evidence_source, payload.evidence_reference):
            raise PermissionError("该凭证已被发证方撤回，不能送审")
        if review and review["status"] != "returned":
            raise PermissionError("证据已提交，请等待风险主管处理")
        if review and review["submitted_by"] != user_id:
            raise PermissionError("请由原提交调查员补正证据")
        if payload.expected_revision != (review["revision"] if review else 0):
            raise PermissionError("复核轮次已更新，请刷新后再提交")
        fields = payload.model_dump(exclude={"expected_revision"})
        item = conn.execute(
            """
            INSERT INTO clue_reviews (clue_id, status, evidence_source, evidence_reference,
                                      finding, recommendation, submitted_by)
            VALUES (%(clue)s, 'pending', %(evidence_source)s, %(evidence_reference)s,
                    %(finding)s, %(recommendation)s, %(user)s)
            ON CONFLICT (clue_id) DO UPDATE SET status = 'pending',
                evidence_source = excluded.evidence_source, evidence_reference = excluded.evidence_reference,
                finding = excluded.finding, recommendation = excluded.recommendation,
                submitted_at = now(), revision = clue_reviews.revision + 1,
                reviewed_by = NULL, reviewed_at = NULL, review_note = NULL
            RETURNING clue_id, status, revision, recommendation
            """,
            {**fields, "clue": clue_id, "user": user_id},
        ).fetchone()
        record_in_transaction(conn, user_id, "clue_review_submitted", clue["claim_id"], {
            **item, **fields,
        })
        return item


def decide_review(clue_id, payload, user_id):
    with get_conn() as conn:
        review_ref = conn.execute(
            "SELECT evidence_source, evidence_reference FROM clue_reviews WHERE clue_id = %s",
            (clue_id,),
        ).fetchone()
        if review_ref:
            # Wait for any in-flight withdrawal of this credential, then re-read.
            lock_credential(conn, review_ref["evidence_source"], review_ref["evidence_reference"])
        clue, review = get_review_for_update(conn, clue_id)
        if not review or review["status"] != "pending":
            raise PermissionError("只有待主管复核的证据可以处理")
        if review["revision"] != payload.expected_revision:
            raise PermissionError("复核轮次已更新，请重新查看最新证据")
        if review["submitted_by"] == user_id:
            raise PermissionError("提交人不能复核自己的证据")
        if is_credential_withdrawn(conn, review["evidence_source"], review["evidence_reference"]):
            raise PermissionError("该凭证已被发证方撤回，不能采纳")
        accepted = payload.action == "accept"
        item = conn.execute(
            """
            UPDATE clue_reviews SET status = %s, reviewed_by = %s, reviewed_at = now(), review_note = %s
            WHERE clue_id = %s RETURNING clue_id, status, revision, recommendation
            """,
            ("resolved" if accepted else "returned", user_id, payload.note, clue_id),
        ).fetchone()
        if accepted:
            conn.execute("UPDATE risk_clues SET status = %s WHERE id = %s", (review["recommendation"], clue_id))
        record_in_transaction(conn, user_id, "clue_review_decided", clue["claim_id"], {
            **item, "action": payload.action, "note": payload.note,
        })
        return item


def unresolved_review_count(conn, claim_id):
    return conn.execute(
        """
        SELECT count(*) AS count FROM clue_reviews v JOIN risk_clues r ON r.id = v.clue_id
        WHERE r.claim_id = %s AND v.status = ANY(%s)
        """,
        (claim_id, list(CLUE_REVIEW_OPEN_STATUSES)),
    ).fetchone()["count"]
