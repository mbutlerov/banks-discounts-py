"""Offline OCR of captured documents, for review rather than publication.

Words retain their page, confidence and coordinates. OCR output is deliberately
not fed to bank parsers: a logo, branch or table row still needs an explicit
association to its legal conditions before an operator can correct an offer.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from contextlib import closing
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
import pypdfium2 as pdfium

from app.scraping.snapshots import read_snapshot


REVIEW_VERSION = "ocr-review-v1"
MAX_PAGES = 40
MAX_RENDER_PIXELS = 12_000_000


class OcrReviewError(RuntimeError):
    """A review document could not be extracted; published data stays intact."""


def _words_from_tsv(tsv: str) -> tuple[str, list[dict[str, Any]]]:
    words: list[dict[str, Any]] = []
    lines: dict[tuple[int, int, int], list[str]] = {}
    try:
        for row in csv.DictReader(StringIO(tsv), delimiter="\t"):
            value = (row.get("text") or "").strip()
            if row.get("level") != "5" or not value:
                continue
            confidence = float(row["conf"])
            if not math.isfinite(confidence) or not 0 <= confidence <= 100:
                continue
            line = tuple(int(row[key]) for key in ("block_num", "par_num", "line_num"))
            box = [int(row[key]) for key in ("left", "top", "width", "height")]
            words.append({"text": value, "confidence": round(confidence, 2), "bbox": box})
            lines.setdefault(line, []).append(value)
    except (KeyError, TypeError, ValueError, csv.Error) as exc:
        raise OcrReviewError("El motor OCR devolvió un resultado inválido.") from exc
    return "\n".join(" ".join(line) for line in lines.values()), words


def _recognize(image: Image.Image, executable: str, language: str) -> tuple[str, list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory(prefix="bank-ocr-") as directory:
        image_path = Path(directory) / "page.png"
        image.save(image_path, format="PNG")
        try:
            result = subprocess.run(
                [executable, str(image_path), "stdout", "-l", language, "--psm", "3", "tsv"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OcrReviewError("No se pudo reconocer una página con Tesseract; verificá el idioma instalado.") from exc
    return _words_from_tsv(result.stdout)


def _selected_pages(pages: list[int] | None, total: int) -> list[int]:
    selected = sorted(set(pages)) if pages else list(range(1, total + 1))
    if not selected or len(selected) > MAX_PAGES or any(number < 1 or number > total for number in selected):
        raise OcrReviewError(f"Seleccioná entre 1 y {MAX_PAGES} páginas existentes en el documento.")
    return selected


def extract_review(content: bytes, *, language: str = "spa", pages: list[int] | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3})?", language):
        raise OcrReviewError("El idioma debe ser un código como spa o spa+eng.")
    executable = shutil.which("tesseract")
    if not executable:
        raise OcrReviewError("Tesseract no está instalado. Ejecutá ocr-review dentro del contenedor scraper.")
    try:
        version = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                 timeout=10, check=True).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise OcrReviewError("No se pudo identificar la versión de Tesseract.") from exc
    extracted: list[dict[str, Any]] = []

    def add_page(number: int, image: Image.Image) -> None:
        with ImageOps.exif_transpose(image).convert("RGB") as rgb:
            scale = min(1.0, math.sqrt(MAX_RENDER_PIXELS / (rgb.width * rgb.height)))
            if scale < 1:
                rgb.thumbnail((max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))))
            text, words = _recognize(rgb, executable, language)
            extracted.append({"page": number, "text": text, "width": rgb.width, "height": rgb.height,
                              "mean_confidence": round(sum(word["confidence"] for word in words) / len(words), 2) if words else None,
                              "words": words})

    try:
        if content.startswith(b"%PDF-"):
            with pdfium.PdfDocument(content) as document:
                for number in _selected_pages(pages, len(document)):
                    with closing(document[number - 1]) as page:
                        width, height = page.get_size()
                        scale = min(4.0, math.sqrt(MAX_RENDER_PIXELS / (width * height)))
                        with closing(page.render(scale=scale)) as bitmap:
                            with bitmap.to_pil() as image:
                                add_page(number, image)
        else:
            _selected_pages(pages, 1)
            with Image.open(BytesIO(content)) as image:
                add_page(1, image)
    except OcrReviewError:
        raise
    except Exception as exc:
        raise OcrReviewError("El snapshot no es un PDF o una imagen legible para OCR.") from exc
    return {"schema_version": REVIEW_VERSION, "method": "tesseract", "engine_version": version,
            "language": language, "content_hash": hashlib.sha256(content).hexdigest(),
            "status": "review_required", "pages": extracted}


def review_document(document: Any, output_dir: str, *, language: str = "spa", pages: list[int] | None = None) -> dict[str, Any]:
    content = read_snapshot(document.snapshot_path, document.content_hash)
    report = extract_review(content, language=language, pages=pages)
    report.update({"document_id": document.id, "source_url": document.url, "bank_slug": document.bank_slug})
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    destination = root / f"document-{document.id}-{digest[:16]}.json"
    # An immutable report does not change source_documents or published offers.
    if not destination.exists():
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as temporary:
            temporary.write(payload)
            pending = Path(temporary.name)
        try:
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)
    return {"path": str(destination), "document_id": document.id, "status": report["status"],
            "pages": len(report["pages"]), "words": sum(len(page["words"]) for page in report["pages"])}
