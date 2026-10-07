from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable

from app.promotions.schemas import OfferData


def source_fingerprint(data: OfferData | dict) -> str:
    """Hash source conditions, ignoring changing ingestion document identities."""
    def stable(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items() if key != "document_id"}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    source = dict(data.model_dump(mode="json") if isinstance(data, OfferData) else data)
    # Additive schema defaults do not change the historical source observation.
    # Explicit identities and a declared scope still participate in the hash.
    if source.get("merchant") is None:
        source.pop("merchant", None)
    if source.get("location_scope", "unknown") == "unknown":
        source.pop("location_scope", None)
    normalized = json.dumps(stable(source), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _merge(original: dict, patch: dict) -> dict:
    result = deepcopy(original)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def effective_offer(data: OfferData, overrides: Iterable[Any]) -> OfferData:
    """Manual corrections overlay source data, which is kept intact for review."""
    result = data.model_dump(mode="json")
    for override in overrides:
        if not override.active or override.offer_key not in (None, data.key):
            continue
        result = _merge(result, override.patch_jsonb)
    return OfferData.model_validate(result)
