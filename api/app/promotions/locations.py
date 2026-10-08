"""Source-backed chain presentation, without changing persisted offer identities."""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
import unicodedata

from app.promotions.schemas import MerchantLocation, OfferData, OfferResponse, PromotionGrouping, PromotionLocation


def _text(value: str) -> str:
    return " ".join(value.split())


def _normalized(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", _text(value)) if not unicodedata.combining(char)).casefold()


def _digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


@lru_cache(maxsize=128)
def _petropar_contract(terms: tuple[str, ...]) -> bool:
    # Card variants repeat the same immutable legal text. Cache only its
    # classification; document, dates and grouping identity stay per offer.
    return any("bolsa petropar" in _normalized(term) for term in terms)


def chain_group(bank_slug: str, offer: OfferData) -> PromotionGrouping | None:
    # A name prefix alone is insufficient: a shared fuel catalogue can contain
    # several brands/campaigns. Only this recognized contract and its annex qualify.
    if bank_slug != "ueno":
        return None
    annex = [item for item in offer.evidence if item.section == "merchant-annex" and item.source_url]
    benefit = [item for item in offer.evidence if item.section == "benefit-table" and item.source_url]
    documents = {item.source_url for item in annex + benefit}
    if not annex or not benefit or len(documents) != 1:
        return None
    # Most offers cannot belong to this contract. Check their short evidence
    # references before normalizing the full legal text of every card variant.
    if not _petropar_contract(tuple(offer.terms)):
        return None
    identity = [bank_slug, next(iter(documents)), str(offer.valid_from), str(offer.valid_until),
                offer.validity_state, offer.start_open, offer.end_open]
    return PromotionGrouping(key="ueno-petropar-" + _digest(identity), name="Petropar")


def location_entries(offer: OfferData) -> list[MerchantLocation]:
    if offer.locations:
        return offer.locations
    if offer.merchant is not None:
        # Typed merchant observations use explicit membership projection. A
        # withdrawn association must not reappear from a historical PDF annex.
        return []
    # Compatibility with already ingested PDF rows. Column roles were established
    # by the parser, and all values come from the annex, never a guessed city name.
    if len(offer.eligibility.locations) != 1 or len(offer.eligibility.cities) != 1:
        return []
    address, city = offer.eligibility.locations[0], offer.eligibility.cities[0]
    for evidence in offer.evidence:
        if evidence.section != "merchant-annex" or evidence.method != "pdf-table":
            continue
        cells = [_text(cell) for cell in evidence.text.split("|")]
        if len(cells) not in {4, 5} or not cells[0].isdigit():
            continue
        if _normalized(cells[2]) != _normalized(address) or _normalized(cells[3]) != _normalized(city):
            continue
        channels = ["POS"] if len(cells) == 5 else ["App Petropar"]
        processors = cells[4].split() if len(cells) == 5 else []
        return [MerchantLocation(key=_digest([cells[1], address, city, processors, channels]),
                                 name=cells[1], address=address, city=city, channels=channels,
                                 processors=processors, evidence=[evidence])]
    return []


def prepare_chain_offer(offer: OfferData) -> OfferData:
    locations = location_entries(offer)
    if not locations:
        return offer
    # Older POS rows inherited both channels from the contract's introductory
    # paragraph. Their annex and the App annex specify distinct accepted routes.
    eligibility = offer.eligibility.model_copy(deep=True)
    eligibility.channels = list(dict.fromkeys(channel for location in locations for channel in location.channels))
    eligibility.processors = list(dict.fromkeys(processor for location in locations for processor in location.processors))
    return offer.model_copy(update={"locations": locations, "eligibility": eligibility})


def search_matches(offer: OfferData, title: str, needle: str) -> bool:
    values = [offer.merchant_name, title, *offer.eligibility.cities, *offer.eligibility.locations]
    values.extend(value for location in location_entries(offer) for value in [location.name, location.city or "", location.address or ""])
    return any(_normalized(needle) in _normalized(value) for value in values)


def _rules(variant: OfferResponse) -> dict:
    data = variant.model_dump(mode="json")
    for field in ("key", "merchant_name", "merchant", "locations"):
        data.pop(field, None)
    data["eligibility"].pop("cities", None)
    data["eligibility"].pop("locations", None)
    data["evidence"] = [item for item in data["evidence"] if item.get("section") != "merchant-annex"]
    return data


def presentation_locations(variants: list[OfferResponse]) -> list[PromotionLocation]:
    result: dict[str, PromotionLocation] = {}
    for variant in variants:
        for location in location_entries(variant):
            # An identical official name, city and address identify one station;
            # its POS/App affiliations retain their separate linked rule sets.
            fields = [_text(location.name), _text(location.city or ""), _text(location.address or "")]
            if not location.city or not location.address:
                fields.extend([location.key, variant.source_url])
            # A catalogue ID survives a renamed branch and bank-specific source
            # aliases. Unnormalized observations retain their exact source match.
            identity = location.key if location.key.startswith("catalogue:location:") else _digest(fields)
            if identity not in result:
                evidence = location.evidence[0] if location.evidence else None
                location_data = location.model_dump()
                location_data["key"] = identity
                result[identity] = PromotionLocation(**location_data, variant_keys=[],
                                                     source_url=evidence.source_url if evidence else variant.source_url,
                                                     source_page=evidence.page if evidence else None)
            row = result[identity]
            row.channels = list(dict.fromkeys([*row.channels, *location.channels]))
            row.processors = list(dict.fromkeys([*row.processors, *location.processors]))
            if variant.key not in row.variant_keys:
                row.variant_keys.append(variant.key)
            known_evidence = {item.model_dump_json() for item in row.evidence}
            for item in location.evidence:
                signature = item.model_dump_json()
                if signature not in known_evidence:
                    row.evidence.append(item)
                    known_evidence.add(signature)
    return sorted(result.values(), key=lambda item: (_normalized(item.city or ""), _normalized(item.name), _normalized(item.address or ""), item.key))


def collapse_chain_variants(variants: list[OfferResponse], group: PromotionGrouping) -> tuple[list[OfferResponse], list[PromotionLocation]]:
    locations = presentation_locations(variants)
    # All eligibility, channels, processors, benefits, caps, terms, publication,
    # availability and matched dates remain in the signature. Only location data
    # moves into the linked annex records above.
    by_rules: dict[str, OfferResponse] = {}
    aliases: dict[str, str] = {}
    for variant in sorted(variants, key=lambda item: item.key):
        has_location = bool(location_entries(variant))
        if not has_location:
            # Without a structured annex association there is no safe basis for
            # removing the source merchant or location restrictions.
            signature = _digest(variant.model_dump(mode="json"))
        else:
            signature = _digest(_rules(variant))
        key = group.key + ":" + signature
        aliases[variant.key] = key
        if key not in by_rules:
            eligibility = variant.eligibility.model_copy(deep=True)
            if has_location:
                eligibility.cities = []
                eligibility.locations = []
            by_rules[key] = variant.model_copy(update={
                "key": key, "merchant_name": group.name if has_location else variant.merchant_name,
                "eligibility": eligibility, "locations": [],
                "evidence": [item for item in variant.evidence if item.section != "merchant-annex"] if has_location else variant.evidence,
            })
    for location in locations:
        location.variant_keys = list(dict.fromkeys(aliases[key] for key in location.variant_keys))
    return list(by_rules.values()), locations
