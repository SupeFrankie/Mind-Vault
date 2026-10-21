"""Bounded, advisory detection of common contact details in PDF text."""
import re

from django.conf import settings
from pypdf import PdfReader, apply_configuration


MAX_PAGES = 25
MAX_EXTRACTED_CHARS = 1_000_000

_EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+(?![\w.-])", re.I)
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{5,}\d(?!\w)")


def detect_patterns(text):
    """Return category names only; never return or persist matched values."""
    findings = []
    if _EMAIL.search(text):
        findings.append("email")
    if any(7 <= sum(char.isdigit() for char in match.group()) <= 15 for match in _PHONE.finditer(text)):
        findings.append("phone")
    return findings


def _result(status, findings=()):
    return {"status": status, "findings": list(findings)}


def scan_pdf(uploaded):
    """Scan a bounded subset of PDF text; failures are unavailable, never clean."""
    if getattr(uploaded, "size", 0) <= 0 or getattr(uploaded, "size", 0) > settings.MAX_UPLOAD_BYTES:
        return _result("unavailable")
    try:
        uploaded.seek(0)
        # Tight pypdf parser ceilings bound decompression and operator work on
        # untrusted input. Larger/complex PDFs are reported as unavailable.
        with apply_configuration(
            maximum_declared_stream_length=2_000_000,
            array_based_stream_maximum_output_length=2_000_000,
            array_maximum_element_count=20_000,
            dictionary_maximum_element_count=20_000,
            zlib_maximum_output_length=2_000_000,
            lzw_maximum_output_length=2_000_000,
            run_length_maximum_output_length=2_000_000,
            jbig2_maximum_output_length=2_000_000,
            brotli_maximum_output_length=2_000_000,
            image_maximum_buffer_size=2_000_000,
            flate_max_row_length=2_000_000,
            stream_filters_maximum_length=8,
            stream_decoding_work_maximum_length=2_000_000,
            stream_parsing_work_maximum_count=100_000,
            xmp_maximum_input_length=500_000,
            page_tree_maximum_entries=MAX_PAGES,
            page_tree_maximum_depth=32,
            xform_maximum_invocations_per_extraction=100,
        ):
            reader = PdfReader(uploaded, strict=False, root_object_recovery_limit=1_000)
            if reader.is_encrypted:
                return _result("unavailable")
            pages = reader.pages
            page_count = len(pages)
            if not 1 <= page_count <= MAX_PAGES:
                return _result("unavailable")

            found_text = False
            findings = set()
            total_chars = 0
            for page in pages:
                page_text = page.extract_text() or ""
                total_chars += len(page_text)
                if total_chars > MAX_EXTRACTED_CHARS:
                    return _result("unavailable")
                if page_text.strip():
                    found_text = True
                    findings.update(detect_patterns(page_text))
            if not found_text:
                # Includes scanned/image-only PDFs and PDFs without extractable text.
                return _result("unavailable")
            return _result("complete", sorted(findings))
    except Exception:
        # Malformed, encrypted, unsupported, or over-budget PDFs do not fail upload.
        return _result("unavailable")
    finally:
        try:
            uploaded.seek(0)
        except Exception:
            pass
