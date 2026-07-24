from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models.models import DriveAccount, File
from services.auth_service import verify_token
from services.drive_service import get_all_quotas

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("")
def list_accounts(db: Session = Depends(get_db), _=Depends(verify_token)):
    quotas = get_all_quotas(db)
    connected_indices = {q["account_index"] for q in quotas}

    disconnected = (
        db.query(DriveAccount)
        .filter(DriveAccount.is_connected == False)
        .all()
    )

    for acc in disconnected:
        if acc.account_index not in connected_indices:
            quotas.append({
                "account_index": acc.account_index,
                "email": acc.email,
                "is_connected": False,
                "used": 0,
                "limit": 0,
                "free": 0,
            })

    return sorted(quotas, key=lambda x: x["account_index"])


@router.delete("/{account_index}")
def disconnect_account(
    account_index: int,
    db: Session = Depends(get_db),
    _=Depends(verify_token),
):
    account = db.query(DriveAccount).filter(DriveAccount.account_index == account_index).first()
    if account:
        db.query(File).filter(File.account_index == account_index).delete()
        account.is_connected = False
        account.refresh_token = None
        account.access_token = None
        account.token_expiry = None
        db.commit()
    return {"ok": True}
