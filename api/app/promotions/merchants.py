"""Reconcile stable catalogue identities without rewriting source observations.

The source offer keeps its ID, key, original JSON and corrections. Shared rules
and membership rows are a projection that can be rebuilt in a transaction.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import re
from typing import Any
import unicodedata

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database.models.bank import Bank
from app.database.models.ingestion import PromotionOverride
from app.database.models.merchant_group import MerchantGroup
from app.database.models.merchant_location import MerchantLocation as StoredLocation
from app.database.models.merchant_membership import MerchantAlias, MerchantLocationAlias, MerchantOfferRule, OfferLocation
from app.database.models.offer import PromotionOffer
from app.database.models.promotion import Promotion
from app.promotions.locations import chain_group, prepare_chain_offer
from app.promotions.overrides import effective_offer
from app.promotions.schemas import MerchantIdentity, MerchantLocation, OfferData, PromotionGrouping


def _label(value: str) -> str:
    return " ".join(value.split())


def _exact(value: str) -> str:
    # Whitespace and capitalization are presentation, not a fuzzy brand match.
    return _label(value).casefold()


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _bounded(value: str) -> str:
    return value if len(value) <= 500 else value[:430] + ":" + sha256(value.encode()).hexdigest()


def _slug(name: str, namespace: str, key: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    label = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:110] or "comercio"
    return label + "-" + _hash([namespace, key])[:20]


def _catalogue(db: Session) -> dict:
    transaction = db.get_transaction()
    if transaction is None:
        db.begin()
        transaction = db.get_transaction()
    state = db.info.get("merchant_catalogue")
    if state is None or state["transaction"] is not transaction:
        # Bank-specific locks permit parallel scrapes. This short catalogue lock
        # also protects verified identities that can be shared across banks.
        key = int.from_bytes(sha256(b"banks-discounts:merchant-catalogue").digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        state = {"transaction": transaction, "merchants": {}, "aliases": {}, "locations": {}, "location_aliases": {}, "rules": {}}
        db.info["merchant_catalogue"] = state
    return state


def _cached(cache: dict, key: Any, loader):
    result = cache.get(key)
    # A nested ingestion savepoint may have rolled back newly created entities.
    if result is None or not inspect(result).persistent:
        result = loader()
        if result is not None:
            cache[key] = result
    return result


def _evidence(data: MerchantIdentity | MerchantLocation) -> dict:
    return {"evidence": [item.model_dump(mode="json") for item in data.evidence]}


def _identity(parent: Promotion, data: OfferData) -> tuple[MerchantIdentity, OfferData]:
    if data.merchant is not None:
        return data.merchant, data
    group = chain_group(parent.bank.slug, data)
    if group is not None:
        prepared = prepare_chain_offer(data)
        evidence = [item for item in prepared.evidence if item.section in {"merchant-annex", "benefit-table"}]
        return MerchantIdentity(namespace="chain", key="petropar", name="Petropar", evidence=evidence), prepared
    return MerchantIdentity(namespace=f"bank:{parent.bank.slug}:merchants", key=_bounded(_exact(data.merchant_name)),
                            name=_label(data.merchant_name), evidence=data.evidence), data


def context_key(parent: Promotion, data: OfferData) -> str:
    """Campaign grouping uses original scope, so a local correction stays local."""
    source = parent.source_key or (parent.metadata_jsonb or {}).get("source_url") or f"legacy:{parent.id}"
    if not isinstance(source, str):
        source = f"legacy:{parent.id}"
    if ":merchant:" in source:
        source = source.rsplit(":merchant:", 1)[0]
    scope = ["campaign", parent.campaign_id] if parent.campaign_id else ["source", source]
    return _hash([scope, parent.category_id, str(data.valid_from), str(data.valid_until),
                  data.validity_state, data.start_open, data.end_open])


def rule_payload(data: OfferData, *, structured_locations: bool) -> dict:
    payload = data.model_dump(mode="json")
    for field in ("key", "merchant_name", "merchant", "source_url", "locations"):
        payload.pop(field, None)
    if structured_locations:
        payload["eligibility"].pop("cities", None)
        payload["eligibility"].pop("locations", None)

    def stable(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items() if key not in {"evidence", "document_id"}}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    # All source evidence remains attached to the original offer and membership.
    # It does not define benefit equality and changes on each downloaded snapshot.
    return stable(payload)


def _group(db: Session, identity: MerchantIdentity, state: dict, counts: Counter,
           *, observation: tuple[str, str] | None = None) -> MerchantGroup:
    alias_key = (identity.namespace, identity.key)
    alias = _cached(state["aliases"], alias_key,
                    lambda: db.query(MerchantAlias).filter_by(namespace=identity.namespace, source_key=identity.key).first())
    source_alias = (_cached(state["aliases"], observation, lambda: db.query(MerchantAlias).filter_by(
        namespace=observation[0], source_key=observation[1]).first()) if observation else None)
    source_reviewed = source_alias is not None and (source_alias.evidence_jsonb or {}).get("method") == "manual-review"
    name_reviewed = alias is not None and (alias.evidence_jsonb or {}).get("method") == "manual-review"
    if source_alias is not None and (source_reviewed or not name_reviewed):
        group = source_alias.merchant_group
    elif alias is not None:
        group = alias.merchant_group
    else:
        group = MerchantGroup(slug=_slug(identity.name, identity.namespace, identity.key), name=identity.name[:150],
                              metadata_jsonb={"identity_namespace": identity.namespace, "identity_key": identity.key})
        db.add(group)
        db.flush()
        counts["merchants_created"] += 1
    if alias is None:
        alias = MerchantAlias(namespace=identity.namespace, source_key=identity.key, merchant_group_id=group.id,
                              evidence_jsonb=_evidence(identity))
        db.add(alias)
        db.flush()
        state["aliases"][alias_key] = alias
        counts["merchant_aliases_created"] += 1
    if observation and source_alias is None:
        source_alias = MerchantAlias(namespace=observation[0], source_key=observation[1], merchant_group_id=group.id,
                                     evidence_jsonb=_evidence(identity))
        db.add(source_alias)
        db.flush()
        state["aliases"][observation] = source_alias
        counts["merchant_aliases_created"] += 1
    elif source_alias is not None and not source_reviewed and source_alias.merchant_group_id != group.id:
        # A reviewed name alias supersedes its automatically derived source
        # alias. A specific manually reviewed source identity keeps precedence.
        source_alias.merchant_group = group
        counts["merchant_aliases_updated"] += 1
    return group


def _location(db: Session, parent: Promotion, group: MerchantGroup, item: MerchantLocation, state: dict, counts: Counter) -> StoredLocation:
    namespace = f"bank:{parent.bank.slug}:locations"
    source_key = _bounded(f"{group.id}:{item.key}")
    alias_key = (namespace, source_key)
    alias = _cached(state["location_aliases"], alias_key,
                    lambda: db.query(MerchantLocationAlias).filter_by(namespace=namespace, source_key=source_key).first())
    identity = (_hash([_exact(item.name), _exact(item.city or ""), _exact(item.address)]) if item.address
                else _hash(["source", namespace, source_key]))
    location_key = (group.id, identity)
    location = alias.location if alias is not None else _cached(state["locations"], location_key,
                          lambda: db.query(StoredLocation).filter_by(merchant_group_id=group.id, identity_key=identity).first())
    if location is None:
        location = StoredLocation(merchant_group_id=group.id, identity_key=identity, name=item.name[:150],
                                  city=item.city[:100] if item.city else None, address=item.address[:255] if item.address else None)
        db.add(location)
        db.flush()
        state["locations"][location_key] = location
        counts["locations_created"] += 1
    elif alias is not None:
        # A stable source ID survives a spelling correction or renamed branch.
        updates = {"name": item.name[:150], "city": item.city[:100] if item.city else None,
                   "address": item.address[:255] if item.address else None}
        changed = any(getattr(location, field) != value for field, value in updates.items())
        if changed:
            for field, value in updates.items():
                setattr(location, field, value)
        if location.identity_key != identity:
            other = _cached(state["locations"], location_key,
                            lambda: db.query(StoredLocation).filter_by(merchant_group_id=group.id, identity_key=identity).first())
            if other is None or other.id == location.id:
                state["locations"].pop((group.id, location.identity_key), None)
                location.identity_key = identity
                state["locations"][location_key] = location
                changed = True
        if changed:
            counts["locations_updated"] += 1
    if alias is None:
        alias = MerchantLocationAlias(namespace=namespace, source_key=source_key, location_id=location.id,
                                      evidence_jsonb=_evidence(item))
        db.add(alias)
        db.flush()
        state["location_aliases"][alias_key] = alias
        counts["location_aliases_created"] += 1
    return location


def normalize_offer(db: Session, parent: Promotion, stored: PromotionOffer, data: OfferData,
                    *, corrections: list[PromotionOverride] | None = None) -> dict[str, int]:
    """Refresh one effective observation; original JSON and identities stay intact."""
    state, counts = _catalogue(db), Counter()
    original = OfferData.model_validate(stored.data_jsonb)
    identity, prepared = _identity(parent, data)
    if corrections is None:
        corrections = db.query(PromotionOverride).filter_by(promotion_id=parent.id, active=True).all()
    applicable = [item for item in corrections if item.active and item.offer_key in (None, original.key)]
    patches = [item.patch_jsonb for item in applicable]
    if any(patch.get("locations") == [] for patch in patches):
        explicit_scope = any("location_scope" in patch for patch in patches)
        prepared = prepared.model_copy(update={"locations": [], "location_scope": data.location_scope if explicit_scope else "unknown"})
    # Source preparation can recover legacy annex channels, but a correction
    # takes precedence. An address correction without a corresponding annex
    # replacement withdraws that membership instead of asserting the old one.
    unstructured_location_patch = not any("locations" in patch for patch in patches) and any(
        "locations" in patch.get("eligibility", {}) or "cities" in patch.get("eligibility", {}) for patch in patches)
    if unstructured_location_patch or (
        (data.eligibility.cities != original.eligibility.cities or data.eligibility.locations != original.eligibility.locations) and data.locations == original.locations
    ):
        prepared = prepared.model_copy(update={"locations": [], "location_scope": "unknown"})
    channels_changed = (data.eligibility.channels != original.eligibility.channels or
                        any("channels" in item.patch_jsonb.get("eligibility", {}) for item in applicable))
    processors_changed = (data.eligibility.processors != original.eligibility.processors or
                          any("processors" in item.patch_jsonb.get("eligibility", {}) for item in applicable))
    if channels_changed or processors_changed:
        eligibility = prepared.eligibility.model_copy(deep=True)
        if channels_changed:
            eligibility.channels = data.eligibility.channels
        if processors_changed:
            eligibility.processors = data.eligibility.processors
        locations = [location.model_copy(update={
            "channels": data.eligibility.channels if channels_changed else location.channels,
            "processors": data.eligibility.processors if processors_changed else location.processors,
        }) for location in prepared.locations]
        prepared = prepared.model_copy(update={"eligibility": eligibility, "locations": locations})
    observation = None
    if data.merchant is None and identity.namespace != "chain":
        observation = (f"bank:{parent.bank.slug}:offer-merchants", _bounded(f"{parent.source_key or parent.id}:{stored.key}"))
    group = _group(db, identity, state, counts, observation=observation)
    scope = "specified" if prepared.locations else "all" if prepared.location_scope == "all" else "unknown"
    payload = rule_payload(prepared.model_copy(update={"location_scope": scope}), structured_locations=bool(prepared.locations))
    context, fingerprint = context_key(parent, original), _hash(payload)
    rule_key = (group.id, parent.bank_id, context, fingerprint)
    rule = _cached(state["rules"], rule_key, lambda: db.query(MerchantOfferRule).filter_by(
        merchant_group_id=group.id, bank_id=parent.bank_id, context_key=context, fingerprint=fingerprint).first())
    if rule is None:
        rule = MerchantOfferRule(merchant_group_id=group.id, bank_id=parent.bank_id, context_key=context,
                                 fingerprint=fingerprint, data_jsonb=payload)
        db.add(rule)
        db.flush()
        state["rules"][rule_key] = rule
        counts["rules_created"] += 1
    if (stored.merchant_group_id, stored.rule_id, stored.location_scope) != (group.id, rule.id, scope):
        stored.merchant_group, stored.rule, stored.location_scope = group, rule, scope
        counts["offers_updated"] += 1
    existing = {row.location_id: row for row in stored.location_memberships}
    memberships: dict[int, dict] = {}
    for item in prepared.locations:
        location = _location(db, parent, group, item, state, counts)
        serialized = item.model_dump(mode="json")
        # Duplicate source rows for one physical branch retain every payment route.
        if location.id in memberships:
            current = memberships[location.id]
            for field in ("channels", "processors", "evidence"):
                current[field] = list({json.dumps(value, sort_keys=True): value for value in [*current[field], *serialized[field]]}.values())
        else:
            memberships[location.id] = serialized
    for location_id, serialized in memberships.items():
        membership = existing.get(location_id)
        if membership is None:
            membership = OfferLocation(offer_id=stored.id, location_id=location_id, publication=prepared.publication, data_jsonb=serialized)
            stored.location_memberships.append(membership)
            counts["memberships_created"] += 1
        elif membership.publication != prepared.publication or membership.data_jsonb != serialized:
            membership.publication, membership.data_jsonb = prepared.publication, serialized
            counts["memberships_updated"] += 1
    for location_id, membership in existing.items():
        if location_id not in memberships and membership.publication != "retired":
            membership.publication = "retired"
            counts["memberships_retired"] += 1
    db.flush()
    return dict(counts)


def normalized_grouping(parent: Promotion, stored: PromotionOffer) -> PromotionGrouping | None:
    if stored.merchant_group is None or stored.rule is None:
        return None
    return PromotionGrouping(key=f"merchant-{stored.merchant_group_id}-{parent.bank_id}-{stored.rule.context_key}",
                             name=stored.merchant_group.name)


def normalized_offer(stored: PromotionOffer, data: OfferData) -> OfferData:
    """Expose normalized references while leaving effective rule overlays to callers."""
    if stored.merchant_group is None:
        return data
    merchant = data.merchant or MerchantIdentity(namespace="catalogue", key=str(stored.merchant_group_id), name=stored.merchant_group.name)
    locations = []
    for membership in stored.location_memberships:
        if membership.publication == "retired":
            continue
        try:
            source = MerchantLocation.model_validate(membership.data_jsonb)
        except ValueError:
            # A corrupt optional projection withdraws that assertion; the
            # original observation remains available for operational review.
            continue
        physical = membership.location
        locations.append(source.model_copy(update={
            "key": f"catalogue:location:{physical.id}", "name": physical.name,
            "city": physical.city, "address": physical.address,
        }))
    updates = {"merchant": merchant, "locations": locations, "location_scope": stored.location_scope}
    if chain_group(stored.promotion.bank.slug, data) is not None:
        # Legacy Petropar observations inherited both channels from the contract
        # introduction. The precise accepted route belongs to the annex row.
        eligibility = data.eligibility.model_copy(deep=True)
        eligibility.channels = list(dict.fromkeys(channel for location in locations for channel in location.channels))
        eligibility.processors = list(dict.fromkeys(processor for location in locations for processor in location.processors))
        updates["eligibility"] = eligibility
    return data.model_copy(update=updates)


def backfill_merchants(db: Session, *, bank: str | None = None) -> dict:
    """Conservative, repeatable backfill. The caller decides commit or rollback."""
    _catalogue(db)
    query = db.query(PromotionOffer).join(Promotion).join(Bank)
    if bank:
        query = query.filter(Bank.slug == bank)
    rows = query.options(joinedload(PromotionOffer.promotion).joinedload(Promotion.bank),
                         selectinload(PromotionOffer.location_memberships).joinedload(OfferLocation.location)).order_by(PromotionOffer.id).all()
    overrides: dict[int, list] = {}
    for correction in db.query(PromotionOverride).filter_by(active=True).order_by(PromotionOverride.id):
        overrides.setdefault(correction.promotion_id, []).append(correction)
    counts = Counter()
    report = {"scanned": len(rows), "reconciled": 0, "unknown_locations": 0, "errors": [], "changes": {}}
    for stored in rows:
        try:
            source = OfferData.model_validate(stored.data_jsonb).model_copy(update={"publication": stored.publication})
            effective = effective_offer(source, overrides.get(stored.promotion_id, []))
        except ValueError as error:
            report["errors"].append({"offer_id": stored.id, "promotion_id": stored.promotion_id, "error": str(error)})
            continue
        counts.update(normalize_offer(db, stored.promotion, stored, effective,
                                      corrections=overrides.get(stored.promotion_id, [])))
        report["reconciled"] += 1
        report["unknown_locations"] += stored.location_scope == "unknown"
    report["changes"] = dict(counts)
    return report
