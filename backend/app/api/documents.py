"""Authenticated PDF upload, status, file access, retry, and deletion."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.api.ownership import owned_document
from app.db import get_db, get_session_factory
from app.models import Document, DocumentJob, MessageSource, utc_now
from app.schemas import DocumentRead, DocumentSearch, DocumentUpdate, SearchHit
from app.security import AuthContext, get_current_auth, require_csrf
from app.services.pdf_files import DocumentProblem, clean_filename, save_upload, storage_key, stored_path
from app.services.rag import search_documents as semantic_search
from app.services.vector_index import IndexProblem

router = APIRouter(prefix="/api/documents", tags=["documents"])
SEARCH_HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie", "Accept-Query": "application/json"}


def get_search_session_factory() -> sessionmaker[Session]:
    return get_session_factory()


def require_search_json(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not content_type:
        raise HTTPException(status_code=400, detail="Content-Type is required", headers=SEARCH_HEADERS)
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="application/json is required", headers=SEARCH_HEADERS)


# Reserve this literal path before /{document_id}; otherwise GET /search
# matches that route and returns a UUID validation error instead of 405.
@router.api_route(
    "/search", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"],
    include_in_schema=False,
)
def unsupported_search_method() -> None:
    raise HTTPException(status_code=405, detail="Method Not Allowed", headers={"Allow": "QUERY", **SEARCH_HEADERS})


@router.api_route(
    "/search", methods=["QUERY"], response_model=list[SearchHit],
    dependencies=[Depends(require_search_json)],
    # FastAPI's built-in OpenAPI body-method list predates QUERY.
    openapi_extra={"requestBody": {"required": True, "content": {
        "application/json": {"schema": {"$ref": "#/components/schemas/DocumentSearch"}},
    }}},
)
def search_documents(
    payload: DocumentSearch,
    response: Response,
    auth: AuthContext = Depends(get_current_auth),
    factory: sessionmaker[Session] = Depends(get_search_session_factory),
) -> list[dict]:
    # Sync routes run in FastAPI's threadpool, including model/vector I/O.
    response.headers.update(SEARCH_HEADERS)
    try:
        return [source.public() for source in semantic_search(
            factory, auth.user.id, payload.query, payload.document_ids, payload.limit,
        )]
    except IndexProblem as exc:
        raise HTTPException(status_code=503, detail="vector_store_unavailable", headers=SEARCH_HEADERS) from exc


@router.get("", response_model=list[DocumentRead])
def list_documents(
    response: Response,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> list[Document]:
    # Accept-Query is scoped to /search, advertised on that resource above.
    response.headers["Link"] = '</api/documents/search>; rel="search"; type="application/json"'
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
    document.display_name = payload.display_name
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
    document = owned_document(db, document_id, auth.user.id, for_update=True)
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
    document = owned_document(db, document_id, auth.user.id, for_update=True)
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
