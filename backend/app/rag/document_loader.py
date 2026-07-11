"""Text extraction for supported knowledge-document formats."""

from io import BytesIO
from zipfile import BadZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from backend.app.core.exceptions import ApplicationError

SUPPORTED_FILE_TYPES = {"txt", "md", "docx", "pdf"}
NEWLINE = chr(10)


def _normalize_text(text: str) -> str:
    return NEWLINE.join(line.rstrip() for line in text.splitlines()).strip()


def load_document_text(content: bytes, file_type: str) -> str:
    normalized_type = file_type.lower()
    if normalized_type not in SUPPORTED_FILE_TYPES:
        raise ApplicationError(
            "DOCUMENT_TYPE_NOT_SUPPORTED",
            "Document type is not supported",
            415,
            {"file_type": normalized_type},
        )
    try:
        if normalized_type in {"txt", "md"}:
            text = content.decode("utf-8-sig")
        elif normalized_type == "docx":
            document = Document(BytesIO(content))
            text = NEWLINE.join(paragraph.text for paragraph in document.paragraphs)
        else:
            reader = PdfReader(BytesIO(content))
            text = NEWLINE.join(page.extract_text() or "" for page in reader.pages)
    except (UnicodeDecodeError, ValueError, KeyError, OSError, PackageNotFoundError, PyPdfError, BadZipFile) as exc:
        raise ApplicationError(
            "DOCUMENT_PARSE_FAILED",
            "Document text extraction failed",
            422,
        ) from exc

    normalized_text = _normalize_text(text)
    if not normalized_text:
        raise ApplicationError(
            "DOCUMENT_PARSE_FAILED",
            "Document contains no extractable text",
            422,
        )
    return normalized_text
