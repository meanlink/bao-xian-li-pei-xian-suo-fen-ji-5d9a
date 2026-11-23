CREATE TABLE IF NOT EXISTS evidence_withdrawals (
    id SERIAL PRIMARY KEY,
    evidence_source TEXT NOT NULL CHECK (evidence_source IN ('official_record', 'original_media', 'issuer_confirmation')),
    evidence_reference TEXT NOT NULL CHECK (char_length(btrim(evidence_reference)) BETWEEN 3 AND 120),
    withdrawal_note TEXT NOT NULL CHECK (char_length(btrim(withdrawal_note)) BETWEEN 6 AND 500),
    withdrawn_by INTEGER NOT NULL REFERENCES users(id),
    withdrawn_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One registration per issuer credential. A retried request returns the original record.
CREATE UNIQUE INDEX IF NOT EXISTS evidence_withdrawals_credential
    ON evidence_withdrawals (evidence_source, evidence_reference);

-- One row per clue (and therefore per claim) touched by a withdrawal registration.
CREATE TABLE IF NOT EXISTS evidence_withdrawal_impacts (
    id SERIAL PRIMARY KEY,
    withdrawal_id INTEGER NOT NULL REFERENCES evidence_withdrawals(id) ON DELETE CASCADE,
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    clue_id INTEGER NOT NULL REFERENCES risk_clues(id) ON DELETE CASCADE,
    outcome TEXT NOT NULL CHECK (outcome IN ('invalidated', 'returned', 'retained')),
    previous_review_status TEXT NOT NULL,
    previous_clue_status TEXT NOT NULL,
    previous_review_note TEXT,
    previous_reviewed_by INTEGER REFERENCES users(id),
    previous_reviewed_at TIMESTAMPTZ,
    review_revision INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (withdrawal_id, clue_id)
);

CREATE INDEX IF NOT EXISTS evidence_withdrawal_impacts_claim
    ON evidence_withdrawal_impacts (claim_id, withdrawal_id);
