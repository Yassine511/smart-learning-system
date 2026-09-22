"""Authenticated PDF upload, status, file access, retry, and deletion."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.ownership import owned_document
from app.db import get_db
from app.models import Document, DocumentJob, MessageSource, utc_now
from app.schemas import DocumentRead, DocumentUpdate
from app.security import AuthContext, get_current_auth, require_csrf
from app.services.pdf_files import DocumentProblem, clean_filename, save_upload, storage_key, stored_path

router = APIRouter(prefix="/api/documents", tags=["documents"])


def clean_display_name(display_name: str) -> str:
    clean = display_name.strip()
    if not clean:
        raise HTTPException(status_code=422, detail="Document name cannot be blank")
    return clean


@router.get("", response_model=list[DocumentRead])
def list_documents(
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> list[Document]:
    return list(
        db.scalars(
            select(Document)
            .where(Document.user_id == auth.user.id)
            .order_by(Document.created_at.desc())
        )
    )


@router.post("", response_model=DocumentRead, status_code=status.HTTP_202_ACCEPTED)
def upload_document(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Document:
    try:
        filename = clean_filename(file.filename)
    except DocumentProblem as exc:
        raise HTTPException(status_code=415, detail=exc.code) from exc
    document_id = uuid4()
    key = storage_key(auth.user.id, document_id)
    try:
        size = save_upload(file, key)
    except DocumentProblem as exc:
        status_code = 413 if exc.code == "too_large" else 415
        raise HTTPException(status_code=status_code, detail=exc.code) from exc
    document = Document(
        id=document_id,
        user_id=auth.user.id,
        original_filename=filename,
        display_name=filename,
        storage_key=key,
        status="pending",
        size_bytes=size,
        index_generation=1,
    )
    job = DocumentJob(document_id=document_id, kind="index", generation=1, status="pending")
    try:
        db.add_all([document, job])
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        stored_path(key).unlink(missing_ok=True)
        raise
    finally:
        file.file.close()
    return document


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Document:
    return owned_document(db, document_id, auth.user.id)


@router.get("/{document_id}/file")
def download_document(
    document_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> FileResponse:
    document = owned_document(db, document_id, auth.user.id)
    if document.status == "deleting":
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        path = stored_path(document.storage_key)
    except DocumentProblem as exc:
        raise HTTPException(status_code=404, detail="Document file unavailable") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Document file unavailable")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=document.original_filename,
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: UUID,
    payload: DocumentUpdate,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Document:
    document = owned_document(db, document_id, auth.user.id)
    if document.status == "deleting":
        raise HTTPException(status_code=409, detail="Document is being deleted")
    document.display_name = clean_display_name(payload.display_name)
    document.updated_at = utc_now()
    db.commit()
    db.refresh(document)
    return document


@router.post("/{document_id}/retry", response_model=DocumentRead, status_code=202)
def retry_document(
    document_id: UUID,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Document:
    document = owned_document(db, document_id, auth.user.id)
    document = db.scalar(
        select(Document).where(
            Document.id == document_id, Document.user_id == auth.user.id
        ).with_for_update()
    )
    if document.status not in {"failed", "indexed"}:
        raise HTTPException(status_code=409, detail="Document is not ready for reindexing")
    document.index_generation += 1
    document.status = "pending"
    document.error_code = None
    document.updated_at = utc_now()
    db.add(DocumentJob(
        document_id=document.id,
        kind="index",
        generation=document.index_generation,
        status="pending",
    ))
    db.commit()
    db.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    document = owned_document(db, document_id, auth.user.id)
    document = db.scalar(
        select(Document).where(
            Document.id == document_id, Document.user_id == auth.user.id
        ).with_for_update()
    )
    if document.status == "deleting":
        active = db.scalar(
            select(DocumentJob.id).where(
                DocumentJob.document_id == document.id,
                DocumentJob.kind == "delete",
                DocumentJob.status.in_(("pending", "running")),
            ).limit(1)
        )
        if active is None:
            document.error_code = None
            db.add(DocumentJob(
                document_id=document.id, kind="delete",
                generation=document.index_generation, status="pending",
            ))
            db.commit()
        return
    document.status = "deleting"
    document.updated_at = utc_now()
    db.execute(
        update(MessageSource)
        .where(MessageSource.document_id == document.id)
        .values(unavailable=True, quote="")
    )
    db.add(DocumentJob(
        document_id=document.id,
        kind="delete",
        generation=document.index_generation,
        status="pending",
    ))
    db.commit()
