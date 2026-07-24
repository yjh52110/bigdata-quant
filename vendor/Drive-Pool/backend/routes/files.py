import io
import logging
import mimetypes

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile, status

logger = logging.getLogger(__name__)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.models import DriveAccount, File
from services.auth_service import verify_token
from services.drive_service import (
    delete_drive_file,
    stream_file,
    list_shared_files,
    list_shared_folder_children,
    list_trash_files,
    move_file,
    pick_best_account,
    remove_shared_file,
    rename_file,
    restore_file,
    share_file,
    sync_files_from_drives,
    trash_drive_file,
    unshare_file,
    upload_file,
)

router = APIRouter(prefix="/files", tags=["files"])


class RenameRequest(BaseModel):
    new_name: str


class MoveRequest(BaseModel):
    new_parent_drive_file_id: str


def _file_to_dict(f: File) -> dict:
    return {
        "id": f.id,
        "file_name": f.file_name,
        "drive_file_id": f.drive_file_id,
        "account_index": f.account_index,
        "size": f.size,
        "mime_type": f.mime_type,
        "has_thumbnail": f.thumbnail_link is not None,
        "parent_drive_file_id": f.parent_drive_file_id,
        "created_at": f.created_at.isoformat(),
    }


@router.post("/sync")
def sync_files(background_tasks: BackgroundTasks, db: Session = Depends(get_db), _=Depends(verify_token)):
    background_tasks.add_task(sync_files_from_drives, db)
    return {"ok": True}


@router.get("")
def list_files(db: Session = Depends(get_db), _=Depends(verify_token)):
    connected_indices = [
        a.account_index for a in db.query(DriveAccount).filter(DriveAccount.is_connected == True).all()
    ]
    files = (
        db.query(File)
        .filter(File.account_index.in_(connected_indices))
        .order_by(File.created_at.desc())
        .all()
    )
    return [_file_to_dict(f) for f in files]


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile,
    parent_folder_id: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(verify_token),
):
    best_index = pick_best_account(db)
    if best_index is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No connected Drive accounts with available space",
        )

    account = db.query(DriveAccount).filter(DriveAccount.account_index == best_index).first()
    mime_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
    content = await file.read()
    stream = io.BytesIO(content)

    result = upload_file(account, stream, file.filename, mime_type, parent_folder_id or None)

    db_file = File(
        file_name=file.filename,
        drive_file_id=result["drive_file_id"],
        account_index=best_index,
        size=result["size"],
        mime_type=result["mime_type"],
        thumbnail_link=result.get("thumbnail_link"),
        parent_drive_file_id=result.get("parent_drive_file_id"),
    )
    db.add(db_file)
    db.commit()
    db.refresh(db_file)

    return _file_to_dict(db_file)



@router.get("/{file_id}/download")
def get_download(file_id: int, db: Session = Depends(get_db), _=Depends(verify_token)):
    file = db.query(File).filter(File.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account = db.query(DriveAccount).filter(DriveAccount.account_index == file.account_index).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not connected")

    return StreamingResponse(
        stream_file(account, file.drive_file_id),
        media_type=file.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file.file_name}"'},
    )


@router.get("/{file_id}/view")
def get_view(file_id: int, db: Session = Depends(get_db), _=Depends(verify_token)):
    file = db.query(File).filter(File.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account = db.query(DriveAccount).filter(DriveAccount.account_index == file.account_index).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not connected")

    return StreamingResponse(
        stream_file(account, file.drive_file_id),
        media_type=file.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{file.file_name}"'},
    )


@router.patch("/{file_id}/rename")
def rename(
    file_id: int,
    body: RenameRequest,
    db: Session = Depends(get_db),
    _=Depends(verify_token),
):
    file = db.query(File).filter(File.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account = db.query(DriveAccount).filter(DriveAccount.account_index == file.account_index).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    rename_file(account, file.drive_file_id, body.new_name)
    file.file_name = body.new_name
    db.commit()

    return _file_to_dict(file)


@router.patch("/{file_id}/move")
def move_file_route(
    file_id: int,
    body: MoveRequest,
    db: Session = Depends(get_db),
    _=Depends(verify_token),
):
    file = db.query(File).filter(File.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account = db.query(DriveAccount).filter(DriveAccount.account_index == file.account_index).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    try:
        move_file(account, file.drive_file_id, body.new_parent_drive_file_id, file.parent_drive_file_id)
    except Exception as e:
        logger.exception("move_file failed for file_id=%s", file_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    file.parent_drive_file_id = None if body.new_parent_drive_file_id == "root" else body.new_parent_drive_file_id
    db.commit()
    return _file_to_dict(file)


@router.post("/{file_id}/share")
def share_file_route(file_id: int, db: Session = Depends(get_db), _=Depends(verify_token)):
    file = db.query(File).filter(File.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    account = db.query(DriveAccount).filter(DriveAccount.account_index == file.account_index).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not connected")
    try:
        link = share_file(account, file.drive_file_id)
        return {"link": link}
    except Exception as e:
        logger.exception("share_file failed for file_id=%s", file_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{file_id}/share", status_code=status.HTTP_204_NO_CONTENT)
def unshare_file_route(file_id: int, db: Session = Depends(get_db), _=Depends(verify_token)):
    file = db.query(File).filter(File.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    account = db.query(DriveAccount).filter(DriveAccount.account_index == file.account_index).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not connected")
    try:
        unshare_file(account, file.drive_file_id)
    except Exception as e:
        logger.exception("unshare_file failed for file_id=%s", file_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: int, db: Session = Depends(get_db), _=Depends(verify_token)):
    file = db.query(File).filter(File.id == file_id).first()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    account = db.query(DriveAccount).filter(DriveAccount.account_index == file.account_index).first()
    if account and account.is_connected:
        try:
            trash_drive_file(account, file.drive_file_id)
        except Exception:
            pass

    db.delete(file)
    db.commit()


@router.get("/shared/{account_index}/{drive_file_id}/download")
def download_shared_file(account_index: int, drive_file_id: str, db: Session = Depends(get_db), _=Depends(verify_token)):
    account = db.query(DriveAccount).filter(DriveAccount.account_index == account_index).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not connected")
    return StreamingResponse(
        stream_file(account, drive_file_id),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{drive_file_id}"'},
    )


@router.get("/shared/{account_index}/{folder_id}/children")
def list_shared_children(account_index: int, folder_id: str, db: Session = Depends(get_db), _=Depends(verify_token)):
    account = db.query(DriveAccount).filter(DriveAccount.account_index == account_index).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not connected")
    try:
        return list_shared_folder_children(account, folder_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/shared/{account_index}/{drive_file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shared_file(account_index: int, drive_file_id: str, db: Session = Depends(get_db), _=Depends(verify_token)):
    account = db.query(DriveAccount).filter(DriveAccount.account_index == account_index).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not connected")
    try:
        remove_shared_file(account, drive_file_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shared")
def list_shared(db: Session = Depends(get_db), _=Depends(verify_token)):
    accounts = db.query(DriveAccount).filter(DriveAccount.is_connected == True).all()
    results = []
    for account in accounts:
        try:
            results.extend(list_shared_files(account))
        except Exception:
            pass
    return sorted(results, key=lambda x: x.get("created_at", ""), reverse=True)


@router.get("/trash")
def list_trash(db: Session = Depends(get_db), _=Depends(verify_token)):
    accounts = db.query(DriveAccount).filter(DriveAccount.is_connected == True).all()
    results = []
    for account in accounts:
        try:
            results.extend(list_trash_files(account))
        except Exception:
            pass
    return sorted(results, key=lambda x: x.get("trashed_at", ""), reverse=True)


@router.post("/trash/{account_index}/{drive_file_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_trash_file(account_index: int, drive_file_id: str, db: Session = Depends(get_db), _=Depends(verify_token)):
    account = db.query(DriveAccount).filter(DriveAccount.account_index == account_index).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not connected")
    try:
        restore_file(account, drive_file_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trash/{account_index}/{drive_file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trash_file(account_index: int, drive_file_id: str, db: Session = Depends(get_db), _=Depends(verify_token)):
    account = db.query(DriveAccount).filter(DriveAccount.account_index == account_index).first()
    if not account or not account.is_connected:
        raise HTTPException(status_code=503, detail="Account not connected")
    try:
        delete_drive_file(account, drive_file_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
