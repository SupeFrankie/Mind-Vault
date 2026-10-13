"""Upload validation. Treats every upload as untrusted.

Layers: (1) extension allowlist, (2) size limit, (3) magic-byte check that the
content matches the claimed extension. Browser-supplied Content-Type is ignored.
Limitation: no malware scanning is available; files are never executed or rendered
as HTML, and are served as attachments with nosniff.
"""
import os
import zipfile

from django.conf import settings
from django.core.exceptions import ValidationError

# extension -> content type WE assign when serving (never the browser's claim)
ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
ZIP_BASED = {".docx", ".pptx"}
ZIP_REQUIRED_MEMBER = {".docx": "word/document.xml", ".pptx": "ppt/presentation.xml"}
MAGIC = {".pdf": [b"%PDF-"], ".png": [b"\x89PNG\r\n\x1a\n"], ".jpg": [b"\xff\xd8\xff"], ".jpeg": [b"\xff\xd8\xff"]}


def get_extension(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def validate_upload(uploaded) -> str:
    """Validate an UploadedFile. Returns the server-assigned content type."""
    ext = get_extension(uploaded.name)
    if ext not in ALLOWED_TYPES:
        allowed = ", ".join(sorted(ALLOWED_TYPES))
        raise ValidationError(f"File type '{ext or 'unknown'}' is not allowed. Allowed: {allowed}.")
    if uploaded.size == 0:
        raise ValidationError("The file is empty.")
    if uploaded.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(f"File is too large (limit {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    uploaded.seek(0)
    head = uploaded.read(16)
    uploaded.seek(0)
    if ext in MAGIC and not any(head.startswith(m) for m in MAGIC[ext]):
        raise ValidationError("File contents do not match its extension.")
    if ext in ZIP_BASED:
        try:
            with zipfile.ZipFile(uploaded) as zf:
                if ZIP_REQUIRED_MEMBER[ext] not in zf.namelist():
                    raise ValidationError("File contents do not match its extension.")
        except zipfile.BadZipFile:
            raise ValidationError("File is corrupted or not a valid Office document.")
        finally:
            uploaded.seek(0)
    if ext in {".txt", ".md"}:
        sample = uploaded.read(65536)
        uploaded.seek(0)
        if b"\x00" in sample:
            raise ValidationError("Text file contains binary data.")
        try:
            sample.decode("utf-8")
        except UnicodeDecodeError as exc:
            # A multibyte char cut at the 64 KB boundary is fine; anything earlier is not.
            if exc.start < len(sample) - 4:
                raise ValidationError("Text files must be UTF-8 encoded.")
    return ALLOWED_TYPES[ext]
