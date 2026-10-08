"""PDF acquisition belongs to sources; these functions are replayable offline.

Known table schemas are interpreted by columns. An unrecognized multi-merchant
document produces pending data rather than copying a global percentage to rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from io import BytesIO

import pdfplumber

from app.promotions.schemas import Benefit, Cap, Evidence, OfferData, Schedule
from app.scraping.clients.pdf_reader import PdfReaderError
from app.scraping.schemas import ScrapedSource
from app.scraping.utils.offers import extract_benefits, extract_eligibility, extract_validity, identity, make_offer
from app.scraping.utils.schedule import extract_schedule, normalize


@dataclass
class PdfPage:
    number: int
    text: str
    tables: list[list[list[str | None]]] = field(default_factory=list)


def read_pdf(document: ScrapedSource) -> list[PdfPage]:
    if document.content is None:
        return [PdfPage(int(document.metadata.get("page", "1")), document.text or "")]
    try:
        with pdfplumber.open(BytesIO(document.content)) as pdf:
            return [PdfPage(i, page.extract_text(layout=False) or "", page.extract_tables())
                    for i, page in enumerate(pdf.pages, 1)]
    except Exception as exc:
        raise PdfReaderError("El documento no se pudo leer como PDF") from exc


def _amount(value: str) -> Decimal | None:
    match = re.search(r"\d[\d.]*", value)
    return Decimal(match[0].replace(".", "")) if match else None


def _percent(value: str) -> Decimal | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", value)
    return Decimal(match[1].replace(",", ".")) if match else None


def _validity_section(text: str) -> str:
    match = re.search(r"vigencia\s*:\s*(.*?)(?:condiciones\s*:|beneficio\s*:|cuadro\s+de)", text, re.I | re.S)
    return match[1] if match else text[:900]


def _table_evidence(url: str, page: int, row: list[str | None]) -> list[Evidence]:
    return [Evidence(source_url=url, page=page, section="table", method="pdf-table",
                     text=" | ".join(cell or "" for cell in row)[:1800])]


def _ueno_day_cell(value: str, start: date | None, end: date | None, url: str) -> Schedule:
    rule = extract_schedule(value, source_url=url, dedicated=True)
    if ":" in value and start and end and (start.year, start.month) == (end.year, end.month):
        numbers = re.findall(r"\b\d{1,2}\b", value.split(":", 1)[1])
        try:
            dates = [date(start.year, start.month, int(day)) for day in numbers]
        except ValueError:
            return Schedule(state="conflict", evidence=rule.evidence)
        if dates and rule.weekdays:
            if any(d.isoweekday() not in rule.weekdays or not start <= d <= end for d in dates):
                return Schedule(state="conflict", evidence=rule.evidence)
            return Schedule(state="known", kind="specific_dates", dates=dates, evidence=rule.evidence)
    return rule


def parse_ueno_tables(pages: list[PdfPage], url: str) -> list[OfferData]:
    text = "\n".join(page.text for page in pages)
    validity_text = _validity_section(text)
    start, end = extract_validity(validity_text)
    offers: list[OfferData] = []
    carry: list[str] = [""] * 8
    fuel_brands: list[tuple[str, str, int]] = []
    levels: list[tuple[list[str | None], int]] = []
    for page in pages:
        for table in page.tables:
            for row in table:
                # POWER, eight columns including merged cells: inherit group
                # fields only; merchant and processor always belong to this row.
                if len(row) == 8:
                    raw = [str(cell or "").strip() for cell in row]
                    if normalize(raw[0]) == "niveles" or normalize(raw[2]) == "marca":
                        carry = [""] * 8
                        continue
                    if not raw[2] or normalize(raw[2]) in {"marca", "beneficio"}:
                        continue
                    for column in (0, 1, 4, 5, 6, 7):
                        if raw[column]:
                            carry[column] = raw[column]
                        else:
                            raw[column] = carry[column]
                    base, additional = _percent(raw[6]), _percent(raw[7])
                    if base is None or not raw[1]:
                        continue
                    merchant = re.sub(r"\s*\(detallados?.*", "", " ".join(raw[2].split()), flags=re.I).strip()
                    evidence = _table_evidence(url, page.number, raw) + [Evidence(source_url=url, field="validity", text=validity_text[:500])]
                    offer = make_offer(merchant=merchant, variant="power:base", source_url=url,
                                       text=" | ".join(raw), days=raw[1],
                                       benefits_text=f"{base}% de reintegro", card_text="Tarjeta de crédito MasterCard Dúo Clásica, Black y Albirroja",
                                       valid_from=start, valid_until=end, evidence=evidence)
                    offer.schedule = _ueno_day_cell(raw[1], start, end, url)
                    offer.eligibility.processors = list(dict.fromkeys(raw[3].split()))
                    offer.eligibility.levels = ["Niveles " + " ".join(raw[0].split())]
                    offer.eligibility.conditions.append(raw[2])
                    offer.caps = [Cap(type="purchase", amount=_amount(raw[4]), period="week", scope="customer", description=raw[4]),
                                  Cap(type="purchase", amount=_amount(raw[5]), period="month", scope="customer", description=raw[5])]
                    offer.publication = "confirmed" if offer.validity_state == "known" and offer.schedule.state == "known" else "pending"
                    offers.append(offer)
                    if additional is not None and base + additional <= 100:
                        extra = offer.model_copy(deep=True)
                        extra.key = identity(merchant, "power:personalized")
                        extra.benefits = [Benefit(type="cashback", percentage=base + additional,
                                                 label=f"{base}% base + {additional}% adicional",
                                                 conditions=["Requiere desbloquear ueno+ POWER y alcanzar el saldo promedio personalizado"])]
                        extra.eligibility.personalization_required = True
                        extra.eligibility.conditions.append("El saldo promedio requerido es personalizado; verificar estado en la app ueno+")
                        offers.append(extra)
                elif len(row) == 3 and str(row[0] or "").isdigit() and "anexo" in normalize(str(row[1] or "")):
                    original = " ".join(str(row[1]).split())
                    name = re.sub(r"(?:detallados?\s+en\s+el|en\s+el)\s+ANEXO.*", "", original, flags=re.I).strip()
                    name = re.sub(r"^A\s+través\s+de\s+la\s+APP\s+", "", name, flags=re.I)
                    fuel_brands.append((name, original, page.number))
                elif len(row) == 4 and re.fullmatch(r"nivel\s+\d+", normalize(str(row[0] or ""))) and _percent(str(row[1])) is not None:
                    levels.append((row, page.number))
    # Fuel conditions declare bounded application throughout the interval and
    # per-brand terminals; tope renewal weekdays are never purchase weekdays.
    for merchant, conditions, merchant_page in fuel_brands:
        for row, page in levels:
            level = str(row[0])
            offer = make_offer(merchant=merchant, variant=level, source_url=url,
                               text=conditions, benefits_text=f"{row[1]} de reintegro",
                               card_text="Tarjeta de crédito MasterCard Dúo Clásica, Black y Albirroja",
                               valid_from=start, valid_until=end,
                               evidence=_table_evidence(url, page, row) + [Evidence(source_url=url, page=merchant_page, text=conditions), Evidence(source_url=url, field="validity", text=validity_text[:500])])
            offer.schedule = Schedule(state="known", kind="all_days", evidence=[Evidence(source_url=url, field="schedule", text="Aplicación durante toda la vigencia indicada en la fila del emblema")])
            offer.eligibility.levels = [level.title()]
            offer.eligibility.conditions = [conditions, "Únicamente estaciones incluidas en el anexo oficial; verificar sucursal"]
            offer.eligibility.processors = ["Upay"] if "puma" in normalize(merchant) else ["Upay", "Infonet"]
            if "premmia" in normalize(merchant):
                offer.eligibility.channels = ["Petrobras Premmia"]
            offer.caps = [Cap(type="purchase", amount=_amount(str(row[2])), period="week", scope="customer", description=str(row[2])),
                          Cap(type="cashback", amount=_amount(str(row[3])), period="week", scope="customer", description=str(row[3]))]
            offer.publication = "confirmed" if start and end else "pending"
            offers.append(offer)
    offers.extend(_parse_ueno_coupons(pages, url))
    offers.extend(_parse_ueno_combo(pages, url))
    return offers


def _parse_ueno_coupons(pages: list[PdfPage], url: str) -> list[OfferData]:
    text = "\n".join(page.text for page in pages)
    heading = re.search(r"CUPONES\s+COMO\s+REINTEGRO\s*[-–]\s*([^\n]+)", text, re.I)
    if not heading:
        return []
    merchant = heading[1].strip()
    validity_text = _validity_section(text)
    start, end = extract_validity(validity_text)
    method = re.search(r"M[eé]todos\s+de\s+pago\s+habilitados\s*:\s*(.*?)(?:Mec[aá]nica\s+del|Uso\s+del)", text, re.I | re.S)
    payment = method[1] if method else ""
    for page in pages:
        for table in page.tables:
            if len(table) != 2 or len(table[0]) != 4 or "beneficio del cupon" not in normalize(str(table[0][0])):
                continue
            row = [str(c or "") for c in table[1]]
            offer = make_offer(merchant=merchant, variant="coupon", source_url=url,
                               text=validity_text, benefits_text=row[0] + " de reintegro",
                               card_text=payment, valid_from=start, valid_until=end,
                               evidence=_table_evidence(url, page.number, row) + [Evidence(source_url=url, field="validity", text=validity_text)])
            offer.schedule = Schedule(state="known", kind="all_days", evidence=[Evidence(source_url=url, field="schedule", text=validity_text)])
            if not offer.benefits and _amount(row[0]) is not None:
                offer.benefits = [Benefit(type="cashback", label="Cupón de reintegro " + row[0])]
            offer.eligibility.personalization_required = True
            offer.eligibility.conditions.extend(["Solo clientes seleccionados con cupón asignado; activar antes de la compra; un único uso", payment.strip(), "Compra mínima: " + row[1]])
            if "agotar stock" in normalize(validity_text):
                offer.eligibility.conditions.append("Hasta agotar stock; verificar disponibilidad del cupón en la app")
            offer.caps = []
            for column, kind in [(2, "purchase"), (3, "cashback")]:
                amount = _amount(row[column])
                if amount is not None:
                    offer.caps.append(Cap(type=kind, amount=amount, period="transaction", scope="customer", description=str(table[0][column]) + ": " + row[column]))
            offer.terms = [text[:6000]]
            offer.publication = "confirmed" if start and end and offer.benefits else "pending"
            offers = [offer]
            installments = re.search(r"(?:hasta\s+)?(\d+)\s+cuotas?\s+sin\s+inter[eé]s(?:es)?", text, re.I)
            if installments and 1 <= int(installments[1]) <= 120:
                extra = offer.model_copy(deep=True)
                extra.key = identity(merchant, "coupon:installments")
                extra.eligibility.card_types = ["credit"]
                extra.eligibility.conditions.append("Solicitar expresamente cuotas antes de procesar la compra")
                extra.benefits.append(Benefit(type="installments", installments=int(installments[1]), is_maximum=True))
                offers.append(extra)
            return offers
    return []


def _parse_ueno_combo(pages: list[PdfPage], url: str) -> list[OfferData]:
    text = "\n".join(page.text for page in pages)
    if not re.search(r"Tu\s+combo\s+de\s+ahorro", text, re.I):
        return []
    headers = list(re.finditer(r"^Beneficio\s+\d+\s+([^:\n]+):", text, re.I | re.M))
    offers = []
    for i, header in enumerate(headers):
        section = text[header.end():headers[i + 1].start() if i + 1 < len(headers) else len(text)]
        section = re.split(r"^Observaciones\s*:|^Anexo\s+de\s+Beneficio", section, flags=re.I | re.M)[0]
        row = re.search(r"(\d{1,2})%\s+Gs\.?\s*([\d.]+)\s+Gs\.?\s*([\d.]+)", section, re.I)
        start, end = extract_validity(section)
        if not row or not start or not end:
            continue
        merchant = header[1].strip().title()
        offer = make_offer(merchant=merchant, variant="targeted-combo", source_url=url,
                           text=section, benefits_text=row[1] + "% de reintegro",
                           card_text="Tarjeta de crédito MasterCard Dúo Clásica y Albirroja",
                           valid_from=start, valid_until=end,
                           evidence=[Evidence(source_url=url, field="benefit", text=section[:1800], section=header[0])])
        offer.schedule = Schedule(state="known", kind="all_days", evidence=[Evidence(source_url=url, field="schedule", text=section[:500])])
        offer.eligibility.personalization_required = True
        offer.eligibility.conditions = ["Solo clientes seleccionados y notificados en la app ueno", section[:1600]]
        if "por una unica vez" in normalize(section):
            offer.eligibility.conditions.append("Beneficio por única vez")
        offer.caps = [Cap(type="purchase", amount=_amount(row[2]), period="campaign", scope="customer", description="Monto de compra por beneficio"),
                      Cap(type="cashback", amount=_amount(row[3]), period="campaign", scope="customer", description="Reintegro máximo por beneficio")]
        offer.publication = "confirmed"
        offers.append(offer)
    return offers


def parse_sudameris_tables(pages: list[PdfPage], url: str) -> list[OfferData]:
    offers: list[OfferData] = []
    common = "\n".join(page.text for page in pages if not page.tables)
    for page in pages:
        for table in page.tables:
            for row in _sudameris_catalogue_rows(table):
                cells = [" ".join(str(c or "").split()) for c in row]
                if not cells[0] or not cells[2] or not cells[4]:
                    continue
                merchant = re.sub(r"\s*\(.*", "", cells[0]).strip()
                base_text, separator, extra_text = cells[2].partition("+")
                offer = make_offer(merchant=merchant, variant="base", source_url=url,
                                   text=cells[4], days=cells[1], benefits_text=base_text,
                                   card_text="Tarjeta de crédito Mastercard y Visa",
                                   evidence=_table_evidence(url, page.number, row))
                amount = _amount(cells[3])
                offer.caps = [Cap(type="purchase", amount=amount, period="month", scope="account", description="Tope mensual por comercio, por cuenta de tarjeta: " + cells[3])] if amount is not None else []
                offer.terms = [cells[0], common[:5000]]
                offer.eligibility.conditions.extend([cells[0], cells[2], "Aplican exclusiones y canales del PDF oficial"])
                offer.eligibility.processors = ["Bancard", "Infonet"]
                # Instalments belong to the whole row, even when written after
                # a card-specific additional cashback percentage.
                for benefit in extract_benefits(cells[2]):
                    if benefit.type == "installments" and benefit not in offer.benefits:
                        offer.benefits.append(benefit)
                additional_benefits = [b for b in extract_benefits(extra_text) if b.type != "installments"]
                percentage_benefits = [b for b in offer.benefits if b.percentage is not None]
                # A discount plus cashback are concurrent benefits, not a sum
                # of percentages applied to one benefit.
                concurrent = bool(separator and additional_benefits and
                                  all(b.type not in {v.type for v in percentage_benefits} for b in additional_benefits))
                if concurrent:
                    offer.benefits.extend(additional_benefits)
                # A visibly incomplete payment clause is not a known channel.
                if re.search(r"\b(?:via|a\s+traves\s+de)\s*(?:hasta\s+\d+\s+cuotas|$)", normalize(cells[2])):
                    offer.publication = "pending"
                    offer.eligibility.unknown_fields.append("payment_channel")
                if any(len({b.percentage for b in offer.benefits if b.type == kind and b.percentage is not None}) > 1
                       for kind in {b.type for b in offer.benefits}):
                    offer.publication = "pending"
                offers.append(offer)
                additional = _percent(extra_text)
                if separator and not concurrent and additional is not None and len(percentage_benefits) == 1:
                    extra = offer.model_copy(deep=True)
                    extra.key = identity(merchant, extra_text)
                    percentage = offer.benefits[0].percentage + additional
                    if percentage > 100:
                        continue
                    extra.benefits[0].percentage = percentage
                    extra.benefits[0].conditions = [extra_text]
                    extra.eligibility.cards = [name for token, name in [("black", "Mastercard Black"), ("infinite", "Visa Infinite")] if token in normalize(extra_text)]
                    if not extra.eligibility.cards:
                        extra.publication = "pending"
                        if "cards" not in extra.eligibility.unknown_fields:
                            extra.eligibility.unknown_fields.append("cards")
                    extra.eligibility.conditions.append(extra_text)
                    offers.append(extra)
    by_key: dict[str, list[OfferData]] = {}
    for offer in offers:
        by_key.setdefault(offer.key, []).append(offer)
    for duplicates in by_key.values():
        if len(duplicates) < 2:
            continue
        # Official regional lists can repeat a brand with overlapping but
        # different conditions. Preserve both rows for review instead of
        # overwriting one variant or making arbitrary benefit choices.
        for offer in duplicates:
            offer.key = identity(offer.key, "duplicate-row:" + offer.evidence[0].text)
            offer.publication = "pending"
            offer.eligibility.unknown_fields.append("offer_variant")
    offers = list({offer.key: offer for offer in offers}.values())
    return offers


def _sudameris_catalogue_rows(table: list[list[str | None]]) -> list[list[str | None]]:
    """Recognize only the bank's five semantic columns, including PDF spacers.

    Recent regional documents extract seven or fifteen physical columns. Their
    blank spacer cells are not extra business fields; unknown layouts remain
    unparsed instead of guessing a column's meaning.
    """
    if not table:
        return []
    headings = [normalize(" ".join(str(cell or "").split())) for cell in table[0]]
    labels = [heading for heading in headings if heading]
    if len(headings) == 7 and len(labels) == 5:
        # "Tope de / compra / mensual" is sometimes split over header rows.
        labels[3] += " " + " ".join(str(row[4] or "") for row in table[1:4]
                                     if len(row) == 7 and not row[0] and not row[1] and not row[2] and not row[3] and not row[6])
    if len(labels) != 5 or not (
        "comercio" in labels[0] and "dia" in labels[1] and
        "beneficio" in labels[2] and "tope" in labels[3] and
        "compra" in labels[3] and "vigencia" in labels[4]
    ):
        return []
    width = len(headings)
    if width == 5:
        columns, spacers = [0, 1, 2, 3, 4], []
    elif width == 7 and headings[3] == headings[5] == "":
        columns, spacers = [0, 1, 2, 3, 6], [4, 5]
    elif width == 15 and all(headings[index] == "" for index in range(15) if index % 3 != 1):
        columns, spacers = [0, 3, 6, 9, 12], [index for index in range(15) if index % 3]
    else:
        return []
    rows = []
    for row in table[1:]:
        if len(row) != width:
            return []
        # Skip only recognizable wrapped header labels, not partial data rows.
        if not row[columns[0]] and not row[columns[1]] and not row[columns[2]] and not row[columns[4]]:
            continuation = " ".join(str(cell or "") for cell in row).strip()
            if not continuation or re.fullmatch(r"(?:mensual|por|comercio|compra|tope|de|\s)+", normalize(continuation)):
                continue
        if any(str(row[index] or "").strip() for index in spacers):
            return []
        cells = [row[index] for index in columns]
        if not cells[0] or not cells[2] or not cells[4]:
            return []
        rows.append(cells)
    return rows


def parse_sudameris_text(text: str, url: str, merchant: str) -> list[OfferData]:
    """Recognized one-merchant template: validity, benefits, conditions.

    Mixed discount/cashback/instalments may coexist. Different percentages of
    the same benefit require their own explicit card clauses.
    """
    validity = re.search(r"\bVIGENCIA\s*\n(.*?)\bBENEFICIOS?\s*\n", text, re.I | re.S)
    benefits = re.search(r"\bBENEFICIOS?\s*\n(.*?)(?:\bCONDICIONES\s*\n|\bEXCLUSIONES\s*\n|$)", text, re.I | re.S)
    conditions = re.search(r"\bCONDICIONES\s*\n(.*?)(?:\bEXCLUSIONES\s*\n|$)", text, re.I | re.S)
    if not validity or not benefits:
        return []
    start, end = extract_validity(validity[1])
    from app.scraping.utils.offers import extract_benefits, extract_caps
    condition_text = conditions[1] if conditions else "Tarjeta de crédito MasterCard y Visa"
    clauses = [" ".join(c.split()) for c in re.split(r"[•●]", benefits[1]) if c.strip()]
    benefit_clauses = []
    installments = []
    for clause in clauses:
        values = extract_benefits(clause)
        if not values:
            continue
        if all(value.type == "installments" for value in values):
            installments.extend((benefit, clause) for benefit in values)
        else:
            benefit_clauses.append((values, clause))
    if not benefit_clauses and not installments:
        return []
    # Multiple values of one type in a single clause remain unresolved rather
    # than being accepted as concurrent independent universal discounts.
    if any(len({b.percentage for b in values if b.type == kind and b.percentage is not None}) > 1
           for values, _ in benefit_clauses for kind in {b.type for b in values}):
        return []
    all_types = [b.type for values, _ in benefit_clauses for b in values]
    same_type_variants = len(all_types) != len(set(all_types))
    groups = benefit_clauses if same_type_variants else [([b for values, _ in benefit_clauses for b in values], " ".join(clause for _, clause in benefit_clauses))]
    offers = []
    for values, clause in groups:
        specific = extract_eligibility(clause)
        payment = clause if specific.cards else condition_text
        variant = ",".join(specific.cards) if specific.cards else re.sub(r"\d+\s*%", "", clause) or "general"
        offer = make_offer(merchant=merchant, variant="sudameris:" + variant,
                           source_url=url, text=validity[1], days=validity[1], benefits_text=clause,
                           card_text=payment, valid_from=start, valid_until=end,
                           evidence=[Evidence(source_url=url, field="validity", text=validity[1]),
                                     Evidence(source_url=url, field="benefit", text=clause)])
        offer.benefits = values
        offer.caps = extract_caps(condition_text)
        offer.terms = [condition_text, clause]
        offer.eligibility.conditions = [clause, condition_text]
        purchase_scope = _sudameris_purchase_scope(clause)
        if purchase_scope:
            for benefit in offer.benefits:
                benefit.conditions.append(purchase_scope)
            if "app" in normalize(purchase_scope):
                offer.eligibility.channels = ["app"]
        if same_type_variants and not specific.cards and not purchase_scope and any("tarjeta" not in normalize(c) for _, c in benefit_clauses):
            offer.publication = "pending"
        # Unrestricted cuotas are concurrent; accommodation-only cuotas become
        # a separate variant with their explicitly stated purchase condition.
        restricted = []
        for benefit, installment_clause in installments:
            if re.search(r"exclusiv|solamente|unicamente|aplica\s+(?:solo|para)", normalize(installment_clause)):
                restricted.append((benefit, installment_clause))
            else:
                offer.benefits.append(benefit.model_copy(deep=True))
        if not same_type_variants and offer.validity_state == "known" and offer.schedule.state == "known" and offer.benefits:
            offer.publication = "confirmed"
        offers.append(offer)
        if restricted:
            extra = offer.model_copy(deep=True)
            extra.key = identity(merchant, offer.key + ":installments")
            for benefit, installment_clause in restricted:
                benefit = benefit.model_copy(deep=True)
                benefit.conditions = [installment_clause]
                extra.benefits.append(benefit)
                extra.eligibility.conditions.append(installment_clause)
            offers.append(extra)
    return offers


def _sudameris_purchase_scope(clause: str) -> str | None:
    """Explicit product/channel variants are as material as card variants."""
    membership = re.match(r"membres[ií]a\s+(?:trimestral|semestral|anual)\s*:", clause, re.I)
    if membership:
        return membership[0].rstrip(":").strip()
    value = normalize(clause)
    if "en estaciones fisicas" in value or re.search(r"a\s+traves\s+de\s+la\s+app\s+\w", value):
        return clause
    return None
