"""Private upload paths and page-preserving PDF extraction."""

from dataclasses import dataclass
from pathlib import Path
import os
import re
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.config import get_settings


class DocumentProblem(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ExtractedChunk:
    id: UUID
    page_number: int
    ordinal: int
    content: str


def clean_filename(filename: str | None) -> str:
    name = (filename or "document.pdf").replace(chr(92), "/").split("/")[-1]
    name = "".join(char for char in name if char.isprintable() and char not in ("/", chr(92)))
    name = name.strip(" .")[:255]
    if not name.lower().endswith(".pdf"):
        raise DocumentProblem("not_pdf")
    return name


def storage_key(user_id: UUID, document_id: UUID) -> str:
    return f"{user_id}/{document_id}.pdf"


def stored_path(key: str) -> Path:
    parts = key.split("/")
    if len(parts) != 2:
        raise DocumentProblem("invalid_storage_key")
    try:
        UUID(parts[0])
        UUID(parts[1].removesuffix(".pdf"))
    except ValueError as exc:
        raise DocumentProblem("invalid_storage_key") from exc
    if not parts[1].endswith(".pdf"):
        raise DocumentProblem("invalid_storage_key")
    root = Path(get_settings().upload_root).resolve()
    candidate = (root / parts[0] / parts[1]).resolve()
    if not candidate.is_relative_to(root):
        raise DocumentProblem("invalid_storage_key")
    return candidate


def save_upload(upload: UploadFile, key: str) -> int:
    destination = stored_path(key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".uploading")
    size = 0
    try:
        with temporary.open("xb") as output:
            first = True
            while block := upload.file.read(1024 * 1024):
                if first:
                    first = False
                    if not block.startswith(b"%PDF-"):
                        raise DocumentProblem("not_pdf")
                size += len(block)
                if size > get_settings().max_upload_bytes:
                    raise DocumentProblem("too_large")
                output.write(block)
            if size == 0:
                raise DocumentProblem("empty_file")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        return size
    finally:
        temporary.unlink(missing_ok=True)


def extract_chunks(path: Path, document_id: UUID, generation: int) -> tuple[int, list[ExtractedChunk]]:
    settings = get_settings()
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise DocumentProblem("encrypted_pdf")
        page_count = len(reader.pages)
        if page_count == 0:
            raise DocumentProblem("empty_pdf")
        if page_count > settings.max_pdf_pages:
            raise DocumentProblem("too_many_pages")
        chunks: list[ExtractedChunk] = []
        characters = 0
        for page_number, page in enumerate(reader.pages, start=1):
            text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
            characters += len(text)
            if characters > settings.max_pdf_characters:
                raise DocumentProblem("too_much_text")
            cursor = 0
            ordinal = 0
            while cursor < len(text):
                end = min(cursor + 900, len(text))
                if end < len(text):
                    boundary = text.rfind(" ", cursor + 600, end)
                    if boundary > cursor + 600:
                        end = boundary
                content = text[cursor:end].strip()
                if content:
                    chunks.append(ExtractedChunk(
                        id=uuid5(NAMESPACE_URL, f"{document_id}:{generation}:{page_number}:{ordinal}"),
                        page_number=page_number,
                        ordinal=ordinal,
                        content=content,
                    ))
                    ordinal += 1
                cursor = end if end == len(text) else max(end - 150, cursor + 1)
        if not chunks:
            raise DocumentProblem("image_only_pdf")
        return page_count, chunks
    except DocumentProblem:
        raise
    except (PdfReadError, OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise DocumentProblem("corrupt_pdf") from exc
