"""Durable PDF indexing and deletion worker."""

from datetime import timedelta
import logging
import time
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import get_session_factory
from app.models import Document, DocumentChunk, DocumentJob, utc_now
from app.services.pdf_files import DocumentProblem, extract_chunks, stored_path
from app.services.vector_index import (
    IndexProblem,
    collection_name,
    delete_all_document_points,
    embed,
    embedding_digest,
    ensure_collection,
    upsert_chunks,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("smart-learning-worker")


def claim_job(factory: sessionmaker[Session]) -> UUID | None:
    now = utc_now()
    settings = get_settings()
    with factory() as db:
        while True:
            job = db.scalar(
                select(DocumentJob)
                .where(or_(
                    and_(DocumentJob.status == "pending", DocumentJob.next_attempt_at <= now),
                    and_(DocumentJob.status == "running", DocumentJob.lease_until < now),
                ))
                .order_by(DocumentJob.next_attempt_at, DocumentJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.status == "running" and job.attempts >= settings.worker_max_attempts:
                # The lease expired without _fail_job running, so the worker
                # crashed or was killed while processing. Stop retrying a PDF
                # that keeps taking the only worker down with it.
                document = db.scalar(
                    select(Document).where(Document.id == job.document_id).with_for_update()
                )
                _settle_failure(job, document, "processing_interrupted", retryable=False)
                db.commit()
                logger.warning("Document job %s abandoned after %s interrupted attempts", job.id, job.attempts)
                continue
            job.status = "running"
            job.attempts += 1
            job.lease_until = now + timedelta(seconds=settings.worker_lease_seconds)
            job.updated_at = now
            db.commit()
            return job.id


def renew_lease(factory: sessionmaker[Session], job_id: UUID) -> None:
    with factory() as db:
        job = db.get(DocumentJob, job_id)
        if job is None or job.status != "running":
            raise IndexProblem("job_cancelled", retryable=False)
        job.lease_until = utc_now() + timedelta(seconds=get_settings().worker_lease_seconds)
        job.updated_at = utc_now()
        db.commit()


def _index_document(factory: sessionmaker[Session], job_id: UUID) -> None:
    with factory() as db:
        job = db.get(DocumentJob, job_id)
        document = db.scalar(
            select(Document).where(Document.id == job.document_id).with_for_update()
        ) if job else None
        if document is None or document.status == "deleting" or document.index_generation != job.generation:
            if job is not None:
                job.status = "completed"
                db.commit()
            return
        document.status = "indexing"
        document.error_code = None
        db.commit()
        document_id = document.id
        user_id = document.user_id
        generation = job.generation
        path = stored_path(document.storage_key)

    if not path.is_file():
        raise DocumentProblem("file_missing")
    page_count, extracted = extract_chunks(path, document_id, generation)
    renew_lease(factory, job_id)
    digest = embedding_digest()
    name = collection_name(digest)
    first_batch = extracted[:get_settings().embedding_batch_size]
    first_vectors = embed([item.content for item in first_batch], query=False)
    ensure_collection(name, len(first_vectors[0]))
    delete_all_document_points(user_id, document_id)

    with factory() as db:
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        db.add_all(
            DocumentChunk(
                id=item.id,
                document_id=document_id,
                user_id=user_id,
                generation=generation,
                page_number=item.page_number,
                ordinal=item.ordinal,
                content=item.content,
            )
            for item in extracted
        )
        db.commit()

    batch_size = get_settings().embedding_batch_size
    for offset in range(0, len(extracted), batch_size):
        renew_lease(factory, job_id)
        batch = extracted[offset:offset + batch_size]
        vectors = first_vectors if offset == 0 else embed([item.content for item in batch], query=False)
        if len(vectors[0]) != len(first_vectors[0]):
            raise IndexProblem("embedding_dimension_mismatch", retryable=False)
        upsert_chunks(name, user_id, document_id, generation, digest, batch, vectors)

    if embedding_digest() != digest:
        raise IndexProblem("embedding_digest_changed")

    with factory() as db:
        document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
        job = db.get(DocumentJob, job_id)
        if document is not None and document.status != "deleting" and document.index_generation == generation:
            document.status = "indexed"
            document.error_code = None
            document.page_count = page_count
            document.embedding_digest = digest
            document.index_collection = name
            document.updated_at = utc_now()
        if job is not None:
            job.status = "completed"
            job.lease_until = None
            job.updated_at = utc_now()
        db.commit()


def _delete_document(factory: sessionmaker[Session], job_id: UUID) -> None:
    with factory() as db:
        job = db.get(DocumentJob, job_id)
        document = db.get(Document, job.document_id) if job else None
        if document is None:
            return
        document_id = document.id
        user_id = document.user_id
        path = stored_path(document.storage_key)
    renew_lease(factory, job_id)
    delete_all_document_points(user_id, document_id)
    path.unlink(missing_ok=True)
    with factory() as db:
        document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if document is not None and document.status == "deleting":
            db.delete(document)
            db.commit()


def _fail_job(factory: sessionmaker[Session], job_id: UUID, code: str, retryable: bool) -> None:
    with factory() as db:
        job = db.get(DocumentJob, job_id)
        if job is None:
            return
        document = db.scalar(
            select(Document).where(Document.id == job.document_id).with_for_update()
        )
        if document is None:
            return
        _settle_failure(job, document, code, retryable)
        db.commit()


def _settle_failure(
    job: DocumentJob,
    document: Document | None,
    code: str,
    retryable: bool,
) -> None:
    """Record a job failure; the caller holds the document row lock and commits."""
    now = utc_now()
    job.lease_until = None
    job.updated_at = now
    # A failure from an obsolete index must not undo deletion or a newer
    # generation. Serialize this decision with the document API's locks.
    if document is None or (job.kind == "index" and (
        document.status == "deleting" or document.index_generation != job.generation
    )):
        job.status = "completed"
        job.error_code = None
        return
    job.error_code = code
    if retryable and job.attempts < get_settings().worker_max_attempts:
        job.status = "pending"
        job.next_attempt_at = now + timedelta(seconds=min(300, 5 * (2 ** (job.attempts - 1))))
        if job.kind == "index":
            document.status = "pending"
    else:
        job.status = "failed"
        if job.kind == "index":
            document.status = "failed"
    document.error_code = code
    document.updated_at = now


def run_once(factory: sessionmaker[Session] | None = None) -> bool:
    factory = factory or get_session_factory()
    job_id = claim_job(factory)
    if job_id is None:
        return False
    try:
        with factory() as db:
            job = db.get(DocumentJob, job_id)
            kind = job.kind
        if kind == "index":
            _index_document(factory, job_id)
        elif kind == "delete":
            _delete_document(factory, job_id)
        else:
            raise IndexProblem("invalid_job_kind", retryable=False)
        logger.info("Processed document job %s", job_id)
    except DocumentProblem as exc:
        _fail_job(factory, job_id, exc.code, retryable=False)
        logger.warning("Document job %s failed: %s", job_id, exc.code)
    except IndexProblem as exc:
        _fail_job(factory, job_id, exc.code, retryable=exc.retryable)
        logger.warning("Document job %s failed: %s", job_id, exc.code)
    except Exception:
        _fail_job(factory, job_id, "processing_error", retryable=True)
        logger.exception("Document job %s failed unexpectedly", job_id)
    return True


def main() -> None:
    settings = get_settings()
    logger.info("Document worker started")
    while True:
        try:
            worked = run_once()
        except Exception:
            logger.exception("Worker polling failed")
            worked = False
        if not worked:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
