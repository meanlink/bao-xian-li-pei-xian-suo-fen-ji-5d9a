from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints


class CredentialWithdrawal(BaseModel):
    evidence_source: Literal["official_record", "original_media", "issuer_confirmation"]
    evidence_reference: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]
    withdrawal_note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=500)]
