"""Known Ueno legal schemas: level tables joined to explicit merchant annexes.

The monthly flyer supplies navigation only. Validity, payment restrictions,
benefits and limits here are derived from each legal document's own fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.promotions.schemas import Benefit, Cap, Evidence, MerchantLocation, OfferData, Schedule
from app.scraping.parsers.pdf_offers import PdfPage
from app.scraping.utils.offers import extract_eligibility, extract_validity, identity, make_offer
from app.scraping.utils.schedule import MONTHS, extract_schedule, normalize


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _amount(value: str) -> Decimal | None:
    match = re.search(r"\d[\d.]*", value)
    return Decimal(match[0].replace(".", "")) if match else None


def _percentage(value: str) -> Decimal | None:
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*%\s*", value)
    value = Decimal(match[1].replace(",", ".")) if match else None
    return value if value is not None and 0 < value <= 100 else None


def _section(text: str, name: str, following: str) -> str:
    match = re.search(name + r"(?:\s+\d+)?\s*:\s*(.*?)(?=(?:" + following + r")(?:\s+\d+)?\s*:|\Z)", text, re.I | re.S)
    return match[1].strip() if match else ""


def _validity(text: str) -> str:
    return _section(text, "Vigencia", r"Condiciones|Sucursales adheridas / Comercios adheridos|Comercios adheridos|Medios de pago habilitados|Beneficio")


def _generic_merchant(name: str) -> bool:
    value = normalize(_text(name)).strip(" .")
    value = re.sub(r"^black\s+|\s+black$", "", value)
    return value in {"comercios", "comercios adheridos", "tiendas adheridas", "supermercados", "hoteles",
                     "farmacias", "bienestar", "deportes", "clubes", "joyerias", "gastronomia",
                     "entretenimiento", "agencias de viajes", "viajes", "tiendas", "plataformas",
                     "combustibles", "cuotas sin intereses"}


def _schedule(text: str, validity: str, url: str, page: int) -> Schedule:
    # A dedicated vigencia section cannot be polluted by accreditation weekdays.
    schedule = extract_schedule(validity, source_url=url, dedicated=True)
    if schedule.state != "unknown":
        return schedule
    # Some Black contracts place purchase weekdays in Condiciones rather than
    # Vigencia. Inspect applicability clauses before any continuous-application
    # declaration; accreditation weekdays and limit periods cannot supply days.
    conditions = _section(text, "Condiciones", "Medios de pago habilitados|Beneficio|Observaciones")
    for clause in re.split(r"[●➔]", conditions):
        if re.search(r"(?:aplica|v[aá]lid[oa])\b", clause, re.I):
            rule = extract_schedule(clause, source_url=url)
            if rule.state != "unknown":
                return rule
    benefit = _section(text, "Beneficio", "Observaciones|Comunicaciones|Exclusiones|Modificaciones")
    # Día del Niño uses 'durante la vigencia según detalle', while the common
    # form adds 'de la promoción'. Both explicitly describe application, rather
    # than merely defining a date range or the period of a spending cap.
    match = re.search(r"El beneficio(?: de reintegro| de cuotas sin intereses)?\s+se aplicar[aá]\s+a cada cliente\s+durante la vigencia(?: de la (?:presente )?promoci[oó]n)?(?=\s*(?:,|seg[uú]n|en los comercios))", benefit, re.I)
    if match:
        return Schedule(state="known", kind="all_days", evidence=[Evidence(
            source_url=url, page=page, field="schedule", section="Beneficio",
            text=_text(match[0])[:600], method="pdf-contract")])
    return schedule


@dataclass
class MerchantRow:
    name: str
    page: int
    cells: list[str]
    processors: list[str]
    validity: str | None = None
    location: str | None = None
    city: str | None = None
    channels: list[str] | None = None
    # A terminal header restricts payment without changing already persisted
    # variant identities, which predate structured locations.
    annex_channels: list[str] | None = None


def _merchant_rows(pages: list[PdfPage]) -> list[MerchantRow]:
    merchants: list[MerchantRow] = []
    schema: str | None = None
    branch_channels: list[str] | None = None
    for page in pages:
        for table in page.tables:
            for row in table:
                cells = [_text(cell) for cell in row]
                if len(cells) not in {4, 5}:
                    continue
                joined = normalize(" ".join(cells))
                if len(cells) == 5 and "nombre del comercio adherido" in joined and "vigencia" in joined:
                    schema = "merchant"
                    continue
                if len(cells) == 5 and "nombre del comercio" in joined and "direccion" in normalize(cells[2]) and "medios de" in joined:
                    schema = "address-branch"
                    continue
                if "nombre sucursal" in joined and "ubicacion" in joined and "medios de" in joined:
                    schema = "branch"
                    branch_channels = ["POS"] if re.search(r"\b(?:v?pos)\b", joined) else None
                    continue
                if len(cells) == 4 and "nombre sucursal" in joined and "ubicacion" in joined and "zona" in joined:
                    schema = "app-branch"
                    continue
                if "nombre" in normalize(cells[0]) and not cells[0].isdigit():
                    # A new, unrecognized annex header cannot inherit the
                    # column roles of the preceding annex.
                    schema = None
                    continue
                if not cells[0].isdigit() or not cells[1] or schema is None:
                    continue
                if schema == "merchant" and len(cells) == 5:
                    processors = [p for cell in cells[2:4] for p in cell.split() if p]
                    merchants.append(MerchantRow(cells[1], page.number, cells, processors, validity=cells[4]))
                elif schema == "branch" and len(cells) == 5:
                    merchants.append(MerchantRow(cells[1], page.number, cells, cells[4].split(),
                                                 location=cells[2], city=cells[3], annex_channels=branch_channels))
                elif schema == "app-branch" and len(cells) == 4:
                    merchants.append(MerchantRow(cells[1], page.number, cells, [], location=cells[2], city=cells[3], channels=["App Petropar"]))
                elif schema == "address-branch" and len(cells) == 5:
                    processors = [p for cell in cells[3:5] for p in cell.split() if p]
                    merchants.append(MerchantRow(cells[1], page.number, cells, processors, location=cells[2]))
    return merchants


def _row_dates(value: str | None, start: date | None, end: date | None) -> tuple[date | None, date | None, str]:
    if not value:
        return start, end, "known" if start and end else "unknown"
    left, right = extract_validity(value)
    if left and right:
        # A fully specified annex interval is still subordinate to the
        # contract's own validity. Keep its dates as evidence, but never let
        # them repair an unreadable/invalid parent interval or extend it.
        if not start or not end:
            return left, right, "unknown"
        if left < start or right > end:
            return left, right, "conflict"
        return left, right, "known"
    # Legal annexes omit the year only when sharing their document's explicit
    # interval. No year is borrowed from URL, clock, or flyer cover.
    normalized = normalize(value)
    short = re.fullmatch(r"(\d{1,2})(?:\s+de\s+(" + "|".join(MONTHS) + r"))?\s+(?:al|hasta|a)\s+(\d{1,2})\s+(?:de\s+)?(" + "|".join(MONTHS) + r")\.?", normalized)
    if not short or not start or not end or start.year != end.year:
        return None, None, "unknown"
    try:
        left = date(start.year, MONTHS[short[2] or short[4]], int(short[1]))
        right = date(start.year, MONTHS[short[4]], int(short[3]))
    except ValueError:
        return None, None, "conflict"
    if left > right:
        return None, None, "conflict"
    if left < start or right > end:
        return left, right, "conflict"
    return left, right, "known"


def _named_merchants(text: str, fallback: str, page: int) -> list[MerchantRow]:
    section = _section(text, "Sucursales adheridas / Comercios adheridos", "Medios de pago habilitados|Condiciones|Beneficio")
    if section and "anexo" not in normalize(section):
        names = [_text(item).rstrip(".") for item in re.split(r"[●•]\s*", section) if _text(item)]
        if names and all(len(name) < 100 and not re.search(r"promoci[oó]n|sucursales|aplica|tiendas|sitio web", name, re.I) for name in names):
            return [MerchantRow(name, page, [name], []) for name in names]
    # Never turn a category whose missing annex contains many merchants into a
    # universal discount for the category title.
    if re.search(r"adherid[oa]s?\s+(?:en|al|a|del)\s+(?:el\s+)?anexo", normalize(text)):
        return []
    conditions = _section(text, "Condiciones", "Medios de pago habilitados|Beneficio|Observaciones")
    named = re.search(r"Promoci[oó]n v[aá]lida en\s*:\s*([^\n]+)", conditions, re.I)
    if named and not _generic_merchant(named[1]) and len(_text(named[1])) < 100:
        return [MerchantRow(_text(named[1]).rstrip("."), page, [_text(named[0])], [])]
    match = re.search(r"(?:Reintegro|Reintegro\s+)[^\n]*?[“\"](?:Bolsa\s+)?([^”\"¨\n]+)[”\"¨]", text, re.I)
    name = _text(match[1]) if match else fallback
    name = re.sub(r"^D[eé]bito Autom[aá]tico (?:del|de)\s+", "", name, flags=re.I)
    if not name or _generic_merchant(name) or "anexo" in normalize(text[:1800]):
        return []
    return [MerchantRow(name, page, [name], [])]


def _club_concepts(pages: list[PdfPage], validity: str) -> list[MerchantRow]:
    club = re.search(r"para el\s+(CLUB [^,\n]+)", validity, re.I)
    if not club:
        return []
    merchants = []
    for page in pages:
        for table in page.tables:
            if not table or len(table[0]) != 2:
                continue
            heading = normalize(_text(table[0][0]))
            processor = "Upay" if "red upay" in heading else "Infonet" if "red infonet" in heading else None
            if not processor:
                continue
            concepts = [_text(row[1]) for row in table[1:] if len(row) == 2 and _text(row[0]).isdigit() and _text(row[1])]
            if concepts:
                merchants.append(MerchantRow(_text(club[1]), page.number, concepts, [processor],
                                 channels=["Débito automático"] if processor == "Infonet" else ["POS", "web"]))
    return merchants


def _payment(text: str):
    payment = _section(text, "Medios de pago habilitados", "Beneficio|Observaciones")
    if not payment:
        payment = _section(text, "Condiciones", "Beneficio|Observaciones")
    # Keep prohibitions as conditions, never accepted channels.
    included = re.split(r"No aplica|No v[aá]lid|La promoci[oó]n no es v[aá]lida", payment, flags=re.I)[0]
    eligibility = extract_eligibility(included)
    eligibility.conditions = [_text(payment)] if payment else []
    lower = normalize(included)
    if "mastercard" in lower or "master card" in lower:
        cards = []
        for pattern, label in [(r"cl[aá]sicas?", "Mastercard Dúo Clásica"), (r"(?<!ultra )\bblack\b", "Mastercard Dúo Black"),
                               (r"ultra\s+black", "Mastercard Dúo Ultra Black"), (r"albirroja", "Mastercard Dúo Albirroja")]:
            if re.search(pattern, included, re.I):
                cards.append(label)
        if cards:
            eligibility.cards = cards
            eligibility.unknown_fields = []
    # Looking up one's level in the app is not personalized eligibility, but
    # an explicitly required personalized average balance is. Keep that
    # requirement even when it appears in Beneficio instead of payment methods.
    eligibility.personalization_required = False
    for clause in re.split(r"[●➔]", re.split(r"Observaciones\s*:", text, flags=re.I)[0]):
        value = normalize(clause)
        if "saldo promedio" in value and re.search(r"personalizad|requerid|debera mantener|sujeto", value):
            eligibility.personalization_required = True
            if _text(clause) not in eligibility.conditions:
                eligibility.conditions.append(_text(clause))
    if "debito automatico" in lower:
        eligibility.channels = ["Débito automático"]
    return eligibility


def _cap_period(text: str) -> str:
    clauses = [normalize(clause) for clause in text.split("●") if re.search(r"tope|l[ií]mite", clause, re.I)]
    if any("mensual" in clause or "mensualmente" in clause for clause in clauses):
        return "month"
    if any("semanal" in clause for clause in clauses):
        return "week"
    if any("durante la vigencia" in clause or "campana" in clause for clause in clauses):
        return "campaign"
    if re.search(r"El beneficio se aplicar[aá] a cada cliente durante la vigencia", text, re.I):
        return "campaign"
    return "unknown"


def parse_ueno_legal_tables(pages: list[PdfPage], url: str, fallback_merchant: str) -> list[OfferData]:
    text = "\n".join(page.text for page in pages)
    if not pages or not re.search(r"Bases y Condiciones", text, re.I):
        return []
    validity = _validity(text)
    start, end = extract_validity(validity)
    exact_dates = extract_schedule(validity, source_url=url, dedicated=True)
    if not start and not end and exact_dates.state == "known" and exact_dates.kind == "specific_dates":
        start, end = min(exact_dates.dates), max(exact_dates.dates)
    tiers: list[tuple[list[str], int]] = []
    schema = False
    for page in pages:
        for table in page.tables:
            for row in table:
                cells = [_text(cell) for cell in row]
                if len(cells) != 4:
                    continue
                heading = normalize(" ".join(cells))
                if "nivel del" in heading and "reintegro" in normalize(cells[1]) and "compra" in normalize(cells[2]) and "reintegro" in normalize(cells[3]):
                    schema = True
                    continue
                if schema and re.fullmatch(r"nivel\s+[1-5]", normalize(cells[0])) and _percentage(cells[1]) is not None:
                    tiers.append((cells, page.number))
    if not tiers:
        card_tiers = _parse_card_tiers(pages, url, text, validity, start, end)
        if card_tiers:
            return card_tiers
        black_tiers = _parse_black_tables(pages, url, text, validity, start, end, fallback_merchant)
        if black_tiers:
            return black_tiers
        return _parse_installments(pages, url, text, validity, start, end, fallback_merchant)
    merchants = _merchant_rows(pages) or _club_concepts(pages, validity) or _named_merchants(text, fallback_merchant, pages[0].number)
    if not merchants:
        return []
    schedule = _schedule(text, validity, url, pages[0].number)
    payment = _payment(text)
    period = _cap_period(text)
    scope = "customer" if "cada cliente" in normalize(text) or "por cliente" in normalize(text) else "unknown"
    header = re.split(r"Observaciones\s*:", text, flags=re.I)[0]
    conditions = [_text(clause) for clause in header.split("●") if re.search(r"m[ií]nim|igual|mayor|no aplica|exclusiv|no acumul|concepto|un [uú]nico", clause, re.I)]
    installments = re.search(r"(?:Hasta|hasta)\s+(\d{1,2})\s+cuotas sin intereses", header)
    separated_installments = bool(re.search(r"Beneficio\s+2\s*:", header, re.I))
    offers: dict[str, OfferData] = {}
    for merchant in merchants:
        row_start, row_end, row_state = _row_dates(merchant.validity, start, end)
        for cells, tier_page in tiers:
            level = cells[0].title()
            # A branch address and processor identify restrictions, even when
            # several official rows use the same merchant name.
            variant = "legal:" + identity(level, merchant.location or "", merchant.city or "", " ".join(merchant.processors), " ".join(merchant.channels or []))
            offer = make_offer(merchant=merchant.name, variant=variant, source_url=url,
                               text="", valid_from=row_start, valid_until=row_end,
                               benefits_text=f"{cells[1]} de reintegro", evidence=[
                                   Evidence(source_url=url, page=tier_page, section="benefit-table", method="pdf-table", text=" | ".join(cells)),
                                   Evidence(source_url=url, page=merchant.page, section="merchant-annex", method="pdf-table", text=" | ".join(merchant.cells)),
                                   Evidence(source_url=url, page=pages[0].number, field="validity", text=validity)])
            offer.validity_state = row_state
            offer.schedule = schedule.model_copy(deep=True)
            offer.eligibility = payment.model_copy(deep=True)
            offer.eligibility.levels = [level]
            offer.eligibility.processors = merchant.processors or (["Upay"] if "red upay" in normalize(header) else [])
            offer.eligibility.conditions.extend(conditions)
            if re.search(r"anexo|exclusiv|[()]", merchant.name, re.I):
                offer.eligibility.conditions.append(merchant.name)
            if merchant.location:
                offer.eligibility.locations = [merchant.location]
            if merchant.city:
                offer.eligibility.cities = [merchant.city]
            if merchant.channels:
                offer.eligibility.channels = merchant.channels
                if merchant.channels == ["App Petropar"]:
                    # The app annex has no processor column. The contract's
                    # Upay terminal restriction belongs to its POS annex.
                    offer.eligibility.processors = merchant.processors
            elif merchant.annex_channels:
                offer.eligibility.channels = merchant.annex_channels
            if merchant.processors == ["Upay"] and "para el club" in normalize(validity):
                offer.eligibility.conditions.extend(merchant.cells)
            elif merchant.processors == ["Infonet"]:
                offer.eligibility.conditions.extend(merchant.cells)
            if re.search(r"ecommerce|e-commerce", merchant.name, re.I):
                offer.eligibility.channels = ["web"]
            if merchant.location:
                # One official row is one location. City and address cannot be
                # reconstructed from a merchant label or joined across rows.
                offer.locations = [MerchantLocation(
                    key=identity(merchant.name, merchant.location, merchant.city or "",
                                 " ".join(offer.eligibility.processors), " ".join(offer.eligibility.channels)),
                    name=merchant.name, address=merchant.location, city=merchant.city,
                    channels=list(offer.eligibility.channels), processors=list(offer.eligibility.processors),
                    evidence=[Evidence(source_url=url, page=merchant.page, section="merchant-annex",
                                       method="pdf-table", text=" | ".join(merchant.cells))],
                )]
            offer.caps = [Cap(type=kind, amount=_amount(value), period=period, scope=scope,
                              description=value + "; límite compartido en los comercios de la campaña")
                          for kind, value in [("purchase", cells[2]), ("cashback", cells[3])]
                          if _amount(value) is not None]
            if "sin tope" in normalize(cells[2] + " " + cells[3]):
                offer.eligibility.conditions.append("Sin tope de compra/reintegro indicado en la fila legal")
            # A merchant discount and bank cashback retain their mechanics and
            # calculation base; the percentages must never be summed.
            commercial = re.search(r"(\d{1,2})% de descuento directo aplicado.*?combinado con un \d{1,2}% de reintegro", header, re.I | re.S)
            if commercial:
                offer.benefits.insert(0, Benefit(type="discount", percentage=Decimal(commercial[1])))
                offer.benefits[-1].conditions.append("Reintegro sobre el importe neto posterior al descuento del comercio")
            offer.terms = [_text(header)]
            offer.publication = "confirmed" if row_state == "known" and schedule.state == "known" else "pending"
            offers[offer.key] = offer
            if installments and int(installments[1]) <= 120 and not separated_installments and merchant.processors != ["Infonet"]:
                financed = offer.model_copy(deep=True)
                financed.key = identity(merchant.name, variant + ":installments:" + installments[1])
                financed.benefits.append(Benefit(type="installments", installments=int(installments[1]), is_maximum=True,
                                                 conditions=["Solicitar expresamente las cuotas al pagar; consultar conceptos y canales habilitados en las bases"] ))
                offers[financed.key] = financed
    if separated_installments:
        for offer in _event_installments(pages, url, validity, start, end, payment):
            offers[offer.key] = offer
    return list(offers.values())


def _parse_installments(pages, url, text, validity, start, end, fallback):
    heading = re.search(r"Cuotas sin intereses\s*[–-]\s*([^\n]+)", text)
    benefit_section = _section(text, "Beneficio", "Observaciones|Comunicaciones|Exclusiones")
    installments = re.search(r"(?:Hasta|hasta)\s+(\d{1,2})\s+cuotas sin interes(?:es)?", benefit_section)
    if not heading or not installments:
        return []
    merchants = _merchant_rows(pages)
    if not merchants and "anexo" in normalize(text[:1500]):
        return []
    if not merchants and _generic_merchant(_text(heading[1]) or fallback):
        return []
    merchants = merchants or [MerchantRow(_text(heading[1]) or fallback, pages[0].number, [heading[1]], [])]
    payment = _payment(text)
    # The legal document explicitly permits purchases throughout a bounded
    # vigencia with no purchase-day restriction. Posting dates occur elsewhere.
    rule = extract_schedule(validity, source_url=url, dedicated=True)
    if rule.state != "known" and start and end:
        rule = Schedule(state="known", kind="all_days", evidence=[Evidence(source_url=url, page=pages[0].number,
                        field="schedule", section="Vigencia/Beneficio", text=validity + " | " + _text(benefit_section))])
    offers = {}
    for merchant in merchants:
        row_start, row_end, row_state = _row_dates(merchant.validity, start, end)
        offer = make_offer(merchant=merchant.name, variant="legal:installments:" + installments[1], source_url=url,
                           text="", valid_from=row_start, valid_until=row_end,
                           benefits_text=f"Hasta {installments[1]} cuotas sin intereses", evidence=[
                               Evidence(source_url=url, page=pages[0].number, section="Beneficio", text=_text(benefit_section)),
                               Evidence(source_url=url, page=merchant.page, section="merchant-annex", method="pdf-table", text=" | ".join(merchant.cells)),
                               Evidence(source_url=url, page=pages[0].number, field="validity", text=validity)])
        offer.validity_state = row_state
        offer.schedule = rule.model_copy(deep=True)
        offer.eligibility = payment.model_copy(deep=True)
        offer.eligibility.processors = merchant.processors or (["Upay"] if "upay" in normalize(benefit_section) else [])
        offer.eligibility.conditions.append(_text(benefit_section))
        offer.terms = [_text(benefit_section)]
        offer.publication = "confirmed" if row_state == "known" and rule.state == "known" else "pending"
        offers[offer.key] = offer
    return list(offers.values())


def _event_installments(pages, url, validity, start, end, payment):
    offers = []
    for page in pages:
        for table in page.tables:
            if not table or len(table[0]) != 6:
                continue
            heading = normalize(" ".join(_text(c) for c in table[0]))
            if "nombre del comercio" not in heading or "vigencia" not in heading:
                continue
            for row in table[1:]:
                cells = [_text(c) for c in row]
                if len(cells) != 6 or not cells[0].isdigit() or not cells[1]:
                    continue
                count = re.fullmatch(r"Hasta (\d{1,2}) cuotas sin intereses", cells[4], re.I)
                if not count:
                    continue
                left, right, state = _row_dates(cells[5], start, end)
                merchant = re.sub(r"\s*\(preventa.*", "", cells[1], flags=re.I).strip()
                offer = make_offer(merchant=merchant, variant="legal:event-installments:" + count[1], source_url=url,
                                   text="", valid_from=left, valid_until=right,
                                   benefits_text=cells[4], evidence=[
                                       Evidence(source_url=url, page=page.number, section="ANEXO II - Beneficio 2", method="pdf-table", text=" | ".join(cells)),
                                       Evidence(source_url=url, page=pages[0].number, field="validity", text=validity)])
                offer.validity_state = state
                offer.schedule = Schedule(state="known", kind="all_days", evidence=[Evidence(source_url=url, page=page.number,
                                           field="schedule", text=cells[5], method="pdf-contract")])
                offer.eligibility = payment.model_copy(deep=True)
                offer.eligibility.processors = cells[2].split()
                offer.eligibility.channels = [cells[3]]
                offer.eligibility.conditions.extend([cells[1], "Seleccionar expresamente cuotas sin intereses al pagar"])
                offer.terms = [" | ".join(cells)]
                offer.publication = "confirmed" if state == "known" else "pending"
                if "preventa" in normalize(cells[1]):
                    # A start day/month without year cannot justify extending
                    # a monthly interval back to the presale date.
                    offer.publication = "pending"
                    offer.eligibility.unknown_fields.append("presale_start_date")
                offers.append(offer)
    return offers


def _parse_black_tables(pages, url, text, validity, start, end, fallback):
    """Recognized Black cashback matrices, including explicit CDA variants.

    Percentages and caps belong to their row. Category campaigns require the
    actual merchant annex; a Black category heading never grants a category-wide
    discount. New column arrangements stay pending in the caller.
    """
    matrices = []
    for page in pages:
        for table in page.tables:
            if len(table) < 2 or len(table[0]) not in {4, 5}:
                continue
            columns = [normalize(_text(cell)) for cell in table[0]]
            if not ("tipo" in columns[0] and "tarjeta" in columns[0]):
                continue
            cda = len(columns) == 5 and "cdas" in columns[1] and "1er dia del mes" in columns[1]
            percentage_column = 2 if cda else 1
            purchase_column = 3 if cda else 2
            last_column = 4 if cda else 3
            if len(columns) == 5 and not cda:
                continue  # The separate ubox additive matrix has its own parser.
            if "reintegro" not in columns[percentage_column] or "compra" not in columns[purchase_column]:
                continue
            has_cashback_cap = "reintegro" in columns[last_column]
            has_row_dates = "vigencia" in columns[last_column]
            if not has_cashback_cap and not has_row_dates:
                continue
            for raw in table[1:]:
                cells = [_text(cell) for cell in raw]
                if len(cells) != len(columns):
                    return []
                card = normalize(cells[0])
                if card not in {"ueno black / ultra black", "ueno black", "ultra black"}:
                    return []
                percentage = _percentage(cells[percentage_column])
                if percentage is None:
                    return []
                amount_cells = [cells[purchase_column]] + ([cells[last_column]] if has_cashback_cap else [])
                if any(_amount(value) is None and "sin tope" not in normalize(value) for value in amount_cells):
                    return []
                matrices.append((page.number, columns, cells, cda, has_cashback_cap, has_row_dates))
    if not matrices:
        return []
    merchants = _merchant_rows(pages) or _named_merchants(text, fallback, pages[0].number)
    if not merchants:
        return []
    # Two identical eligibility rows with different benefits are conflicting;
    # do not silently keep the last version of the row.
    seen_rows = {}
    for _, _, cells, cda, *_ in matrices:
        row_identity = (normalize(cells[0]), normalize(cells[1]) if cda else "")
        if row_identity in seen_rows and seen_rows[row_identity] != cells:
            return []
        seen_rows[row_identity] = cells
    rule = _schedule(text, validity, url, pages[0].number)
    payment = _payment(text)
    if "credit" not in payment.card_types:
        return []
    header_text = re.split(r"Observaciones\s*:", text, flags=re.I)[0]
    terms = _text(text)
    installments = re.search(r"Hasta\s+(\d{1,2})\s+cuotas sin intereses\s+a trav[eé]s de la Red Upay", header_text, re.I)
    result = {}
    for merchant in merchants:
        merchant_start, merchant_end, merchant_state = _row_dates(merchant.validity, start, end)
        for page_number, columns, cells, cda, has_cap, has_dates in matrices:
            percentage_column, purchase_column, last_column = (2, 3, 4) if cda else (1, 2, 3)
            left, right, state = merchant_start, merchant_end, merchant_state
            if has_dates:
                left, right, state = _row_dates(cells[last_column], merchant_start, merchant_end)
                if merchant_state == "conflict":
                    state = "conflict"
            variant = "legal:card:" + identity(cells[0], cells[1] if cda else "", merchant.location or "",
                                              merchant.city or "", " ".join(merchant.processors))
            offer = make_offer(merchant=merchant.name, variant=variant, source_url=url,
                               text="", valid_from=left, valid_until=right,
                               benefits_text=f"{cells[percentage_column]} de reintegro", evidence=[
                                   Evidence(source_url=url, page=page_number, section="card-benefit-table", method="pdf-table",
                                            text=" | ".join(columns) + " || " + " | ".join(cells)),
                                   Evidence(source_url=url, page=merchant.page, section="merchant-annex", method="pdf-table",
                                            text=" | ".join(merchant.cells)),
                                   Evidence(source_url=url, page=pages[0].number, field="validity", text=validity),
                               ])
            offer.validity_state = state
            offer.schedule = rule.model_copy(deep=True)
            offer.eligibility = payment.model_copy(deep=True)
            card = normalize(cells[0])
            offer.eligibility.cards = (["Mastercard Dúo Black", "Mastercard Dúo Ultra Black"] if "/" in card else
                                       ["Mastercard Dúo Ultra Black" if card == "ultra black" else "Mastercard Dúo Black"])
            offer.eligibility.levels = []
            # A matrix explicitly names the eligible credit cards.
            offer.eligibility.card_types = ["credit"]
            offer.eligibility.unknown_fields = []
            offer.eligibility.processors = merchant.processors or payment.processors
            if not offer.eligibility.processors and "red upay" in normalize(header_text):
                offer.eligibility.processors = ["Upay"]
            if merchant.channels:
                offer.eligibility.channels = merchant.channels
            elif merchant.annex_channels:
                offer.eligibility.channels = merchant.annex_channels
            if merchant.location:
                offer.eligibility.locations = [merchant.location]
                offer.eligibility.cities = [merchant.city] if merchant.city else []
                offer.locations = [MerchantLocation(
                    key=identity(merchant.name, merchant.location, merchant.city or "",
                                 " ".join(offer.eligibility.processors), " ".join(offer.eligibility.channels)),
                    name=merchant.name, address=merchant.location, city=merchant.city,
                    channels=list(offer.eligibility.channels), processors=list(offer.eligibility.processors),
                    evidence=[offer.evidence[1].model_copy(deep=True)],
                )]
            if cda:
                offer.eligibility.conditions.append("CDAs vigentes el primer día del mes: " + cells[1])
            offer.eligibility.conditions.extend(_text(clause) for clause in header_text.split("●")
                                                if re.search(r"no aplica|no v[aá]lid|excluid|de forma conjunta|expresamente", clause, re.I))
            offer.terms = [terms]
            cap_columns = [("purchase", purchase_column)] + ([("cashback", last_column)] if has_cap else [])
            for kind, column in cap_columns:
                amount = _amount(cells[column])
                if amount is None:
                    offer.eligibility.conditions.append(columns[column] + ": " + cells[column])
                    continue
                period = "month" if "mensual" in columns[column] else _cap_period(text)
                offer.caps.append(Cap(type=kind, amount=amount, period=period, scope="customer",
                                      description=columns[column] + ": " + cells[column] + "; límite compartido en los comercios de la campaña"))
            offer.publication = "confirmed" if state == "known" and rule.state == "known" else "pending"
            result[offer.key] = offer
            if installments and 1 <= int(installments[1]) <= 120 and "upay" in {normalize(p) for p in offer.eligibility.processors}:
                financed = offer.model_copy(deep=True)
                # The contract offers financing separately and does not
                # explicitly guarantee combining it with cashback. Neither
                # cashback caps nor its CDA-based amount tiers govern the
                # separately stated installment benefit.
                financed.key = identity(merchant.name, "legal:card-installments:" + installments[1],
                                         merchant.location or "", merchant.city or "", " ".join(merchant.processors))
                financed.benefits = [Benefit(type="installments", installments=int(installments[1]), is_maximum=True,
                                             conditions=["Solicitar expresamente cuotas sin intereses al pagar; respetar exclusiones de productos de las bases"])]
                financed.caps = []
                financed.eligibility.cards = list(payment.cards)
                financed.eligibility.conditions = [c for c in financed.eligibility.conditions if not c.startswith("CDAs vigentes")]
                financed.evidence[0] = Evidence(source_url=url, page=next((p.number for p in pages if installments[0] in p.text), page_number),
                                               section="Beneficio", method="pdf-contract", text=_text(installments[0]))
                result[financed.key] = financed
    return list(result.values())


def _parse_card_tiers(pages, url, text, validity, start, end):
    """The ubox five-column matrix explicitly adds Black's 30% to base 20%."""
    if not re.search(r"beneficio adicional del\s+\d+%.*?se sumar[aá] al beneficio base", text, re.I | re.S):
        return []
    for page in pages:
        for table in page.tables:
            if not table or len(table[0]) != 5:
                continue
            header = normalize(" ".join(_text(c) for c in table[0]))
            if not all(field in header for field in ["tipo tarjeta", "% de reintegro", "suma maxima"]):
                continue
            rows = [[_text(c) for c in row] for row in table[1:]]
            if len(rows) != 2 or any(len(row) != 5 for row in rows):
                return []
            base = _percentage(rows[0][1])
            extra = re.fullmatch(r"\+\s*(\d+(?:[.,]\d+)?)%\s+adicional", rows[1][1], re.I)
            if base is None or not extra:
                return []
            additional = Decimal(extra[1].replace(",", "."))
            if base + additional > 100:
                return []
            offers = []
            payment = _payment(text)
            rule = _schedule(text, validity, url, pages[0].number)
            locations = []
            for branch_page in pages:
                for branches in branch_page.tables:
                    if not branches or len(branches[0]) != 3 or "locales ueno" not in normalize(_text(branches[0][0])):
                        continue
                    locations.extend(_text(row[1]) + " — " + _text(row[2]) for row in branches[1:]
                                     if len(row) == 3 and _text(row[0]).isdigit())
            header_text = re.split(r"Observaciones\s*:", text, flags=re.I)[0]
            for index, row in enumerate(rows):
                percentage = base if index == 0 else base + additional
                offer = make_offer(merchant="ubox", variant="legal:card-base" if index == 0 else "legal:card-black",
                                   source_url=url, text="", valid_from=start, valid_until=end,
                                   benefits_text=f"{percentage}% de reintegro", evidence=[
                                       Evidence(source_url=url, page=page.number, section="card-benefit-table", method="pdf-table", text=" | ".join(row)),
                                       Evidence(source_url=url, page=pages[0].number, field="validity", text=validity)])
                offer.schedule = rule.model_copy(deep=True)
                offer.eligibility = payment.model_copy(deep=True)
                if index == 1:
                    offer.eligibility.cards = ["Mastercard Dúo Black", "Mastercard Dúo Ultra Black"]
                    offer.benefits[0].label = f"{base}% base + {additional}% adicional"
                offer.eligibility.locations = locations
                offer.eligibility.processors = ["Upay"]
                offer.eligibility.conditions.extend(["Pago al retirar un paquete, sobre el valor de hasta 1 kg del envío desde USA",
                                                     "Límites compartidos por cliente entre tipos de tarjeta durante la campaña"])
                offer.caps = [Cap(type="purchase", amount=_amount(row[2]), period="campaign", scope="customer", description=row[2]),
                              Cap(type="cashback", amount=_amount(row[4]), period="campaign", scope="customer", description=row[4])]
                offer.terms = [_text(header_text)]
                offer.publication = "confirmed" if start and end and rule.state == "known" else "pending"
                offers.append(offer)
            return offers
    return []


def incomplete_legal_table(pages: list[PdfPage]) -> bool:
    """An unreadable row prevents retirement of previously interpreted tiers."""
    tier_values: dict[str, tuple[str, ...]] = {}
    for page in pages:
        for table in page.tables:
            if not table:
                continue
            for row in table:
                cells = [_text(cell) for cell in row]
                if len(cells) != 4 or not normalize(cells[0]).startswith("nivel "):
                    continue
                if not re.fullmatch(r"nivel\s+[1-5]", normalize(cells[0])):
                    continue
                if _percentage(cells[1]) is None:
                    return True
                for value in cells[2:]:
                    if _amount(value) is None and "sin tope" not in normalize(value):
                        return True
                level = normalize(cells[0])
                values = tuple(cells[1:])
                if level in tier_values and tier_values[level] != values:
                    return True
                tier_values[level] = values
    return False
