from __future__ import annotations

import hashlib
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from app.scraping.ocr_review import OcrReviewError, _words_from_tsv, extract_review, review_document


TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "4\t1\t1\t1\t1\t0\t0\t0\t100\t20\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t1\t2\t30\t10\t96.1\tLOCAL\n"
    "5\t1\t1\t1\t1\t2\t33\t2\t40\t10\t40.0\tDUDOSO\n"
    "5\t1\t1\t1\t2\t1\t1\t25\t50\t10\t91.0\tASUNCION\n"
)


def image_bytes() -> bytes:
    buffer = BytesIO()
    with Image.new("RGB", (200, 100), "white") as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_confidence_and_coordinates_survive_for_manual_review():
    text, words = _words_from_tsv(TSV)
    assert text == "LOCAL DUDOSO\nASUNCION"
    assert words[1] == {"text": "DUDOSO", "confidence": 40.0, "bbox": [33, 2, 40, 10]}


def test_missing_engine_is_actionable_and_never_silent_empty_success():
    with patch("app.scraping.ocr_review.shutil.which", return_value=None):
        with pytest.raises(OcrReviewError, match="contenedor scraper"):
            extract_review(image_bytes())


def test_command_has_no_shell_and_language_cannot_inject_options():
    content = image_bytes()
    with patch("app.scraping.ocr_review.shutil.which", return_value="tesseract"), patch(
        "app.scraping.ocr_review.subprocess.run", side_effect=[SimpleNamespace(stdout="tesseract 5.5\n"), SimpleNamespace(stdout=TSV)]
    ) as process:
        report = extract_review(content)
    assert report["status"] == "review_required"
    assert report["content_hash"] == hashlib.sha256(content).hexdigest()
    assert report["pages"][0]["mean_confidence"] == 75.7
    assert process.call_args.args[0][2:] == ["stdout", "-l", "spa", "--psm", "3", "tsv"]
    assert "shell" not in process.call_args.kwargs
    with pytest.raises(OcrReviewError, match="idioma"):
        extract_review(content, language="spa --bad")


def test_invalid_page_does_not_publish_or_create_a_review():
    with patch("app.scraping.ocr_review.shutil.which", return_value="tesseract"), patch(
        "app.scraping.ocr_review.subprocess.run", return_value=SimpleNamespace(stdout="tesseract 5.5\n")
    ):
        with pytest.raises(OcrReviewError, match="páginas existentes"):
            extract_review(image_bytes(), pages=[2])


def test_pdf_pages_keep_original_number_when_reviewing_a_subset():
    content = BytesIO()
    with Image.new("RGB", (100, 100), "white") as page:
        page.save(content, format="PDF", save_all=True, append_images=[page])
    with patch("app.scraping.ocr_review.shutil.which", return_value="tesseract"), patch(
        "app.scraping.ocr_review.subprocess.run", return_value=SimpleNamespace(stdout="tesseract 5.5\n")
    ), patch("app.scraping.ocr_review._recognize", return_value=("LOCAL", [])):
        report = extract_review(content.getvalue(), pages=[2])
    assert [page["page"] for page in report["pages"]] == [2]


def test_review_report_is_immutable_and_does_not_modify_database_document(tmp_path):
    document = SimpleNamespace(id=123, snapshot_path="a.bin", content_hash="hash", url="https://www.bancoatlas.com.py/annex.jpg", bank_slug="atlas")
    original = vars(document).copy()
    with patch("app.scraping.ocr_review.read_snapshot", return_value=b"captured"), patch(
        "app.scraping.ocr_review.extract_review", return_value={"status": "review_required", "pages": [{"page": 1, "words": []}], "content_hash": "hash"}
    ):
        first = review_document(document, str(tmp_path))
        second = review_document(document, str(tmp_path))
    assert first == second
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert vars(document) == original
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["source_url"] == document.url and report["document_id"] == 123
