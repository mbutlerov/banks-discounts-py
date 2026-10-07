from __future__ import annotations

import re
from decimal import Decimal
from bs4 import BeautifulSoup

from app.promotions.schemas import Benefit, Evidence, OfferData
from app.scraping.base import BaseParser
from app.scraping.schemas import ScrapedPromotion, ScrapedSource
from app.scraping.utils.days import extract_valid_days
from app.scraping.utils.encoding import fix_mojibake
from app.scraping.utils.offers import extract_benefits, extract_caps, extract_eligibility, extract_validity, identity, legacy_summary, make_offer
from app.scraping.utils.schedule import MONTHS, normalize


class ItauHtmlParser(BaseParser):
    def parse(self, source: ScrapedSource) -> list[ScrapedPromotion]:
        if not source.text:
            return []
        if source.metadata.get("parse_warning", "").startswith("itau_"):
            source.metadata.pop("parse_warning")
        soup = BeautifulSoup(source.text, "html.parser")
        heading = soup.find("h2")
        merchant = fix_mojibake(source.metadata.get("partner_name") or (heading.get_text(" ", strip=True) if heading else ""))
        if not merchant:
            return []
        description = _get_field(soup, "beneficio") or ""
        validity = _get_field(soup, "vigencia") or ""
        payment = _get_field(soup, "medios de pago") or ""
        terms = _get_field(soup, "bases y condiciones") or ""
        # Intro contains the purchase weekdays/channel absent from labelled fields.
        intro = "\n".join(p.get_text(" ", strip=True) for p in soup.find_all("p"))
        schedule_intro = _purchase_days_text(intro)
        start, end = extract_validity(validity)
        intro_start, intro_end = extract_validity(intro)
        conflict = bool((start and intro_start and start != intro_start) or (end and intro_end and end != intro_end))
        evidence = [Evidence(source_url=source.source_url, field=field, text=value[:1500])
                    for field, value in [("validity", validity), ("validity", intro), ("terms", terms),
                                         ("cards", payment)] if value]
        # Benefit sentences associate a percentage with its own card variant.
        clauses = [c.strip() for c in re.split(r"(?<=[.!?])\s+|\n", description) if "%" in c or "cuota" in c.lower()]
        if not clauses:
            clauses = [description]
        benefit_conflict = _benefit_fields_conflict(description, intro)
        if benefit_conflict:
            source.metadata["parse_warning"] = "itau_conflicting_benefit_fields"
        offers = []
        for clause in clauses:
            offer = make_offer(merchant=merchant, variant="itau:" + re.sub(r"\d+\s*%", "", clause),
                               source_url=source.source_url, text=validity + "\n" + terms,
                               days=schedule_intro, benefits_text=clause, card_text=payment + " " + clause,
                               valid_from=start, valid_until=end,
                               evidence=[*evidence, Evidence(source_url=source.source_url, field="benefit", text=clause)],
                               default_type="discount")
            # Payment channels apply to the purchase; cards listed as points
            # earners in legal boilerplate are not required-card variants.
            intro_eligibility = extract_eligibility(intro)
            offer.eligibility.channels = intro_eligibility.channels
            offer.eligibility.processors = ["Bancard"] if "bancard" in terms.lower() else []
            # The introductory purchase sentence sometimes names the required
            # cards while the labelled benefit only contains the percentage.
            # Match its own benefit; points-earning cards in legal boilerplate
            # and another variant's payment sentence are never inherited.
            card_context = _matching_payment_context(intro, clause)
            if card_context and not offer.eligibility.cards:
                card_eligibility = extract_eligibility(card_context)
                offer.eligibility.cards = card_eligibility.cards
                if "the platinum card de american express" in normalize(card_context):
                    offer.eligibility.cards = [c for c in offer.eligibility.cards if c != "American Express"]
                    offer.eligibility.cards.append("American Express Platinum")
                if "personal bank" in normalize(card_context):
                    offer.eligibility.levels = ["Personal Bank"]
                offer.eligibility.conditions.append(card_context)
                offer.evidence.append(Evidence(source_url=source.source_url, field="cards", text=card_context))
            # A branch-specific sentence is a distinct condition, even when it
            # uses the same credit cards as the bank-wide merchant benefit.
            location = re.search(r"\ben\s+(?:el\s+)?(?:local|sucursal)\s+(?:del?\s+)?(.+?)\s+(?:ten[eé]s|tienes|aplica|obten[eé]s)\b", clause, re.I)
            if location:
                offer.eligibility.locations = [location[1].strip()]
            elif len(clauses) == 1:
                # A single merchant benefit may be limited to one shopping
                # centre in the intro, outside the labelled benefit field.
                purchase_location = re.search(r"\baplica\s+en\s+(?:el\s+)?(Shopping\s+[^.!\n]+)", intro, re.I)
                if purchase_location:
                    offer.eligibility.locations = [purchase_location[1].strip()]
                    offer.eligibility.conditions.append(purchase_location[0])
                    offer.evidence.append(Evidence(source_url=source.source_url, field="locations", text=purchase_location[0]))
            # Some details put the cap solely in their introductory paragraph.
            # Associate it with the preceding percentage before the next branch
            # benefit, rather than applying one tier's cap to all the variants.
            for cap in extract_caps(intro):
                offset = intro.find(cap.description)
                preceding = list(re.finditer(r"(?<!\d)(\d{1,3})\s*%", intro[:max(offset, 0)]))
                if preceding and any(b.percentage == int(preceding[-1][1]) for b in offer.benefits):
                    if cap not in offer.caps:
                        offer.caps.append(cap)
                        offer.evidence.append(Evidence(source_url=source.source_url, field="cap", text=cap.description))
            offer.terms = [terms] if terms else []
            variants = _split_wallet_bonus(offer, clause, intro) if not benefit_conflict else None
            for variant in variants or [offer]:
                if any(len({b.percentage for b in variant.benefits if b.type == kind and b.percentage is not None}) > 1
                       for kind in {b.type for b in variant.benefits}):
                    variant.publication = "pending"
                    if not benefit_conflict:
                        source.metadata["parse_warning"] = "itau_unresolved_card_variants"
                if conflict:
                    variant.validity_state = "conflict"
                    variant.publication = "pending"
                if benefit_conflict:
                    variant.publication = "pending"
                offers.append(variant)
        kind, percentage = legacy_summary(offers)
        metadata = dict(source.metadata)
        metadata.update({"source_url": source.source_url, "source_type": source.source_type})
        metadata["offers_complete"] = "true" if description and offers and all(o.benefits for o in offers) and not source.metadata.get("parse_warning") else "false"
        weekdays = extract_valid_days(schedule_intro)
        if weekdays:
            metadata["valid_days"] = ",".join(weekdays)
        source_key = f"itau:{source.metadata.get('b')}:{source.metadata.get('c')}" if source.metadata.get("b") and source.metadata.get("c") else "itau:url:" + identity(source.source_url)
        return [ScrapedPromotion(bank_slug="itau", title=merchant, merchant_name=merchant,
                                 category_name=source.metadata.get("category_name"), description=description or intro,
                                 benefit_type=kind, discount_percentage=percentage, start_date=start, end_date=end,
                                 terms_summary=terms[:1000] or None, raw_text=soup.get_text("\n", strip=True),
                                 source_key=source_key, offers=offers, metadata=metadata)]


def _percentage_values(text: str) -> list[Decimal]:
    return [Decimal(m[1].replace(",", "."))
            for m in re.finditer(r"(?<!\d)(\d{1,3}(?:[.,]\d+)?)\s*%", text)]


def _benefit_fields_conflict(description: str, intro: str) -> bool:
    """A repeated introductory benefit corroborates, rather than overrides.

    Cap amounts and points boilerplate do not participate. A shorter summary
    may legitimately omit another card's benefit, but two equal-size sets of
    percentage statements that disagree are a real source contradiction.
    """
    labelled, introductory = _percentage_values(description), _percentage_values(intro)
    if labelled and len(labelled) == len(introductory) and sorted(labelled) != sorted(introductory):
        return True
    bonuses, intro_bonuses = list(_WALLET_BONUS.finditer(normalize(description))), list(_WALLET_BONUS.finditer(normalize(intro)))
    if len(bonuses) == len(intro_bonuses) == 1:
        left, right = bonuses[0], intro_bonuses[0]
        if tuple(left[k] for k in ("base", "bonus", "kind")) != tuple(right[k] for k in ("base", "bonus", "kind")):
            return True
        left_channels, right_channels = _wallet_channels(left["condition"]), _wallet_channels(right["condition"])
        if left_channels and right_channels and "Billetera" not in left_channels + right_channels:
            return set(left_channels) != set(right_channels)
    return False


def _matching_payment_context(intro: str, clause: str) -> str | None:
    signature = {(b.type, b.percentage, b.installments) for b in extract_benefits(clause, default_type="discount")}
    if not signature:
        return None
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", intro) if s.strip()]
    matches = []
    for index, sentence in enumerate(sentences):
        found = {(b.type, b.percentage, b.installments) for b in extract_benefits(sentence, default_type="discount")}
        if not signature.issubset(found):
            continue
        payment = sentence if re.search(r"\bpagando\s+con\b", normalize(sentence)) else (
            sentences[index - 1] if index and re.match(r"pagando\s+con\b", normalize(sentences[index - 1])) else "")
        if payment and payment not in matches:
            matches.append(payment)
    return matches[0] if len(matches) == 1 else None


_WALLET_BONUS = re.compile(
    r"(?P<base>\d{1,3}(?:[.,]\d+)?)\s*%\s+de\s+(?P<kind>ahorro|descuento|reintegro)"
    r"\s*(?:y\s+)?(?:\+|mas)\s*(?P<bonus>\d{1,3}(?:[.,]\d+)?)\s*%\s*"
    r"(?:adicional\s+)?pagando\s+(?:con|via)\s+(?P<condition>.+?)"
    r"(?=\s+y\s+(?:hasta\s+)?\d+\s+cuotas?\b|[.;]|$)"
)


def _wallet_channels(condition: str) -> list[str]:
    value = normalize(condition).strip()
    channels = []
    for pattern, label in [(r"\bapple\s+pay\b", "Apple Pay"),
                           (r"\b(?:google\s+pay|gpay)\b", "Google Pay"),
                           (r"\b(?:codigo\s+)?qr\b", "QR"),
                           (r"\bbilletera\b", "Billetera")]:
        if re.search(pattern, value):
            channels.append(label)
            value = re.sub(pattern, "", value)
    # Unknown card or extra conditions cannot be silently dropped.
    return channels if channels and not re.sub(r"\by\b|[\s,]", "", value) else []


def _split_wallet_bonus(offer: OfferData, clause: str, intro: str) -> list[OfferData] | None:
    """Recognize only one explicit same-kind additional wallet percentage.

    The original key remains the base rule, and the extra key derives from
    the payment condition, not its percentage. A date range or missing weekday
    never becomes an all-days schedule through this transformation.
    """
    matches = list(_WALLET_BONUS.finditer(normalize(clause)))
    if len(matches) != 1 or len(_percentage_values(clause)) != 2:
        return None
    match = matches[0]
    intro_percentages = _percentage_values(intro)
    if intro_percentages and sorted(intro_percentages) != sorted(_percentage_values(clause)):
        return None
    channels = _wallet_channels(match["condition"])
    if not channels:
        return None
    base, bonus = Decimal(match["base"].replace(",", ".")), Decimal(match["bonus"].replace(",", "."))
    kind = "cashback" if match["kind"] == "reintegro" else "discount"
    if not 0 < base < base + bonus <= 100 or any(b.type not in (kind, "installments") for b in offer.benefits):
        return None
    # "Billetera" is sometimes expanded to named wallets in the introduction.
    # Use that expansion only when both percentages and their roles match.
    intro_matches = list(_WALLET_BONUS.finditer(normalize(intro)))
    if channels == ["Billetera"] and len(intro_matches) == 1:
        other = intro_matches[0]
        if (other["base"], other["bonus"], other["kind"]) == (match["base"], match["bonus"], match["kind"]):
            channels = _wallet_channels(other["condition"]) or channels
    baseline = offer.model_copy(deep=True)
    base_benefit = next((b.model_copy(deep=True) for b in offer.benefits if b.type == kind and b.percentage == base),
                        Benefit(type=kind, percentage=base))
    installments = [b.model_copy(deep=True) for b in offer.benefits if b.type == "installments"]
    baseline.benefits = [base_benefit, *installments]
    # A wallet mentioned solely as the condition for the bonus is not required
    # for the base benefit. Other explicit purchase channels are preserved.
    common_intro = normalize(intro)
    for other in reversed(intro_matches):
        common_intro = common_intro[:other.start()] + other["base"] + "% de " + other["kind"] + common_intro[other.end():]
    baseline.eligibility.channels = extract_eligibility(common_intro).channels
    extra = baseline.model_copy(deep=True)
    extra.key = identity(offer.key, "wallet-bonus", ",".join(sorted(channels)))
    extra.benefits[0].percentage = base + bonus
    condition = match["condition"].strip()
    extra.benefits[0].conditions.append("Adicional " + match["bonus"] + "% pagando con " + condition)
    extra.eligibility.channels = channels
    extra.eligibility.conditions.append("Adicional " + match["bonus"] + "% pagando con " + condition)
    return [baseline, extra]


def _purchase_days_text(intro: str) -> str:
    """Keep purchase rules separate from the promotion's validity interval.

    In particular, "Del 14 de septiembre al 31 de octubre 2026" is not a
    two-date purchase schedule. Its closing Saturday must not conflict with
    the following independent "De lunes a miércoles" purchase sentence.
    """
    months = "(?:" + "|".join(MONTHS) + ")"
    numeric = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{4})"
    named_full = r"\d{1,2}\s+(?:de\s+)?" + months + r"\s+(?:de[l]?\s+)?\d{4}"
    weekday = r"(?:(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)\s+)?"
    named_start = weekday + r"\d{1,2}(?:\s+(?:de\s+)?" + months + r")?(?:\s+(?:de[l]?\s+)?\d{4})?"
    range_prefix = r"(?:del|desde(?:\s+el)?|a\s+partir\s+de(?:l|\s+el)?)\s+"
    named_range = range_prefix + named_start + r"\s+(?:hasta|al|a)\s+(?:el\s+)?" + named_full
    numeric_range = numeric + r"\s*(?:hasta|al|a|[-–])\s*(?:el\s+)?" + numeric
    end_only = r"(?:hasta|al)\s+(?:el\s+)?(?:" + named_full + "|" + numeric + ")"
    start_only = r"(?:desde|a\s+partir\s+de)\s+(?:el\s+)?(?:" + named_full + "|" + numeric + ")"
    parts = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", intro):
        start, end = extract_validity(sentence)
        if not (start or end):
            parts.append(sentence)
            continue
        # Remove the actual bounds, preserving "todos los días", weekend or
        # monthly rules in the same sentence. Separate purchase dates survive.
        value = normalize(sentence)
        for pattern in (named_range, numeric_range, end_only, start_only):
            value = re.sub(pattern, "", value)
        parts.append(value)
    return "\n".join(parts)


def _get_field(soup: BeautifulSoup, label: str) -> str | None:
    for strong in soup.find_all("strong"):
        if label in strong.get_text(" ", strip=True).lower():
            parts = []
            for sibling in strong.next_siblings:
                text = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
                if text:
                    parts.append(text)
            return fix_mojibake(" ".join(parts).strip()) or None
    return None
