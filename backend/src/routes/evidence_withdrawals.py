from fastapi import APIRouter, Depends

from src.schemas.evidence_withdrawals import CredentialWithdrawal
from src.services.evidence_withdrawal_service import list_withdrawals, register_withdrawal
from src.utils.auth import current_user, require_roles

router = APIRouter(tags=["evidence-withdrawals"])


@router.get("/evidence-withdrawals")
def withdrawals(user=Depends(current_user)):
    return {"items": list_withdrawals()}


@router.post("/evidence-withdrawals")
def register(payload: CredentialWithdrawal, user=Depends(require_roles("risk_manager"))):
    return register_withdrawal(payload, user["id"])
