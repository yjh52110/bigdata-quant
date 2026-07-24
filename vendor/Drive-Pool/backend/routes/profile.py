import io
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.models import DriveAccount, Profile
from services.auth_service import verify_token
from services.drive_service import download_file, get_or_create_profile_folder, upload_file

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileUpdate(BaseModel):
    display_name: str | None = None
    bio: str | None = None


def _get_or_create_profile(db: Session) -> Profile:
    profile = db.query(Profile).first()
    if not profile:
        profile = Profile()
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.get("")
def get_profile(db: Session = Depends(get_db), _=Depends(verify_token)):
    profile = _get_or_create_profile(db)
    return {
        "display_name": profile.display_name,
        "bio": profile.bio,
        "has_avatar": profile.avatar_drive_file_id is not None,
    }


@router.put("")
def update_profile(body: ProfileUpdate, db: Session = Depends(get_db), _=Depends(verify_token)):
    profile = _get_or_create_profile(db)
    if body.display_name is not None:
        profile.display_name = body.display_name
    if body.bio is not None:
        profile.bio = body.bio
    db.commit()
    return {"ok": True}


@router.post("/avatar", status_code=status.HTTP_200_OK)
async def upload_avatar(file: UploadFile, db: Session = Depends(get_db), _=Depends(verify_token)):
    account = db.query(DriveAccount).filter(DriveAccount.is_connected == True).first()
    if not account:
        raise HTTPException(status_code=503, detail="No connected accounts")

    mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "image/jpeg"
    content = await file.read()

    folder_id = get_or_create_profile_folder(account)
    result = upload_file(account, io.BytesIO(content), "_drivepool_avatar_", mime_type, parent_folder_id=folder_id)

    profile = _get_or_create_profile(db)
    profile.avatar_drive_file_id = result["drive_file_id"]
    profile.avatar_account_index = account.account_index
    db.commit()

    return {"ok": True}


@router.get("/avatar")
def get_avatar(db: Session = Depends(get_db), _=Depends(verify_token)):
    profile = db.query(Profile).first()
    if not profile or not profile.avatar_drive_file_id:
        raise HTTPException(status_code=404, detail="No avatar set")

    account = db.query(DriveAccount).filter(
        DriveAccount.account_index == profile.avatar_account_index
    ).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not available")

    content = download_file(account, profile.avatar_drive_file_id)
    return StreamingResponse(io.BytesIO(content), media_type="image/jpeg")
