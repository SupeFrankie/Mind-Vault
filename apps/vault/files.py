import logging
import re

from django.http import FileResponse, Http404

log = logging.getLogger(__name__)


def file_response(material):
    """Stream a material's file as an attachment. Callers MUST have checked permission.

    Served as `attachment` with nosniff and a server-assigned content type so an
    uploaded file can never run as active content in our origin.
    """
    if not material.file:
        raise Http404("This material has no file.")
    try:
        handle = material.file.open("rb")
    except FileNotFoundError:
        log.error("Missing file on disk for material %s", material.pk)
        raise Http404("File is missing.")
    safe_name = re.sub(r"[\x00-\x1f\x7f\"\\/]", "_", material.original_filename or "download")
    response = FileResponse(
        handle,
        as_attachment=True,
        filename=safe_name,
        content_type=material.content_type or "application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
