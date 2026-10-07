from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.database.models import PromotionOffer, SourceDocument
from app.promotions.overrides import source_fingerprint
from app.promotions.schemas import Evidence
from app.scraping import ai_proposals
from app.scraping.snapshots import store_snapshot

from tests.helpers.ingestion import candidate, db, ingestion_engine


def proposal_data():
    original = candidate().offers[0]
    proposed = original.model_dump(mode="json")
    proposed["evidence"] = [{"text": "Cafe ofrece 20% de reintegro los sábados", "page": 1, "field": "benefits"}]
    proposed["schedule"]["evidence"] = [{"text": "Cafe ofrece 20% de reintegro los sábados", "page": 1, "field": "schedule"}]
    document = SimpleNamespace(id=123, url="https://www.ueno.com.py/example")
    pages = {1: "Cafe ofrece 20% de reintegro los sábados. Condiciones del banco."}
    return original, proposed, pages, document


def test_proposal_requires_real_quote_and_correct_page():
    original, proposed, pages, document = proposal_data()
    proposed["evidence"][0]["text"] = "Oferta inventada que no aparece en el documento"
    with pytest.raises(ValueError, match="ausente"):
        ai_proposals.validate_proposal(proposed, original, pages, document)
    _, proposed, _, _ = proposal_data()
    proposed["evidence"][0]["page"] = 2
    with pytest.raises(ValueError, match="ausente"):
        ai_proposals.validate_proposal(proposed, original, pages, document)
    _, proposed, _, _ = proposal_data()
    proposed["evidence"] = []
    with pytest.raises(ValueError, match="evidencia"):
        ai_proposals.validate_proposal(proposed, original, pages, document)


@pytest.mark.parametrize("field", ["key", "merchant_name"])
def test_ai_proposal_cannot_change_offer_identity(field):
    original, proposed, pages, document = proposal_data()
    proposed[field] = "Other identity"
    with pytest.raises(ValueError, match="identidad"):
        ai_proposals.validate_proposal(proposed, original, pages, document)


def test_valid_ai_proposal_always_pending_and_rebinds_evidence():
    original, proposed, pages, document = proposal_data()
    proposal = ai_proposals.validate_proposal(proposed, original, pages, document)
    assert original.publication == "confirmed"
    assert proposal.publication == "pending"
    for evidence in proposal.evidence + proposal.schedule.evidence:
        assert evidence.document_id == document.id
        assert evidence.source_url == document.url
        assert evidence.method == "ai_proposal"


def test_ai_disabled_cannot_access_database_or_provider(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_AI_ENRICHMENT", False)

    class NoDatabase:
        def get(self, *args):
            raise AssertionError("Disabled AI accessed the database")

    def forbidden_provider(*args, **kwargs):
        raise AssertionError("Disabled AI accessed Gemini")

    monkeypatch.setattr(ai_proposals, "GeminiClient", forbidden_provider)
    with pytest.raises(ValueError, match="Activar"):
        ai_proposals.propose_offer(NoDatabase(), 123, 456)


def test_fingerprint_ignores_document_identity_but_detects_changed_conditions():
    original, proposed, _, _ = proposal_data()
    first = original.model_copy(update={"evidence": [Evidence(document_id=1, text="Original conditions")]}).model_dump(mode="json")
    second = deepcopy(first)
    second["evidence"][0]["document_id"] = 999
    first["schedule"]["evidence"] = [{"document_id": 1, "text": "Saturday"}]
    second["schedule"]["evidence"] = [{"document_id": 999, "text": "Saturday"}]
    assert source_fingerprint(first) == source_fingerprint(second)
    second["benefits"][0]["percentage"] = "30"
    assert source_fingerprint(first) != source_fingerprint(second)


def test_proposal_cache_avoids_provider_and_tracks_current_document_without_publication(db, monkeypatch, tmp_path):
    from app.scraping.persistence import save_promotions
    monkeypatch.setattr(settings, "ENABLE_AI_ENRICHMENT", True)
    monkeypatch.setattr(settings, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    save_promotions(db, [candidate()])
    row = db.query(PromotionOffer).one()
    hash_value, path = store_snapshot(b"<main>Cafe ofrece 20% de reintegro los sabados. Condiciones del banco.</main>")
    first_document = SourceDocument(bank_slug="ueno", url="https://www.ueno.com.py/example", content_hash=hash_value, snapshot_path=path, mime_type="text/html")
    second_document = SourceDocument(bank_slug="ueno", url=first_document.url, content_hash=hash_value, snapshot_path=path, mime_type="text/html")
    db.add_all([first_document, second_document])
    db.commit()
    first_source = deepcopy(row.data_jsonb)
    first_source["evidence"] = [{"source_url": first_document.url, "document_id": first_document.id, "text": "Cafe ofrece 20% de reintegro los sabados", "page": 1, "field": "benefits"}]
    row.data_jsonb = first_source
    db.commit()
    calls = []

    class FakeGemini:
        def __init__(self, *args, **kwargs):
            pass

        def generate_json(self, **kwargs):
            calls.append(kwargs)
            proposed = row.data_jsonb.copy()
            proposed["evidence"] = [{"text": "Cafe ofrece 20% de reintegro los sabados", "page": 1, "field": "benefits"}]
            return SimpleNamespace(parsed_json=proposed)

    monkeypatch.setattr(ai_proposals, "GeminiClient", FakeGemini)
    first = ai_proposals.propose_offer(db, first_document.id, row.id)
    repeated_source = deepcopy(row.data_jsonb)
    repeated_source["evidence"][0]["document_id"] = second_document.id
    row.data_jsonb = repeated_source
    db.commit()
    second = ai_proposals.propose_offer(db, second_document.id, row.id)
    assert len(calls) == 1
    assert first["cached"] is False and second["cached"] is True
    assert second["document_id"] == second_document.id
    assert second["proposal"]["evidence"][0]["document_id"] == second_document.id
    assert second["proposal"]["publication"] == "pending"
    assert row.publication == row.data_jsonb["publication"] == "confirmed"
