"""Part B filing ingestion: entity, basis of preparation, and page records.
"""

from __future__ import annotations
import re
import unicodedata
from pathlib import Path
from typing import Any
import pymupdf
from pydantic import BaseModel, ConfigDict
from app.config import get_settings
from app.logging import get_logger

log = get_logger(__name__)

WORD_RE = re.compile(r"[A-Za-z]{2,}")

ENTITY_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("PCG", "PETRONAS Chemicals Group Berhad", r"PETRONAS\s+Chemicals\s+Group\s+Berhad"),
    ("GC", "PTT Global Chemical Public Company Limited", r"PTT\s+Global\s+Chemical\s+Public"),
    ("RIL", "Reliance Industries Limited", r"Reliance\s+Industries\s+Limited"),
    (
        "PETRONAS",
        "Petroliam Nasional Berhad (PETRONAS Group)",
        r"Petroliam\s+Nasional\s+Berhad|PETRONAS\s+Group",
    ),
)

CURRENCY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("MYR", r"\bRM\s*(?:Mil|Million|'000|\d)"),
    ("THB", r"\bBaht\b|\bTHB\b"),
    ("INR", r"\b(?:Rs\.?|INR|₹)\s*(?:crore|cr\b|\d)|\bcrore\b"),
)

UNIT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("millions", r"\b(?:RM|Baht|THB)\s*Mil(?:lion)?\b|\bUnit:\s*Baht\s*Million\b|\bin millions\b"),
    ("crore", r"\bcrore\b"),
    ("thousands", r"\bin\s+thousands\b|\b'000\b"),
    ("ones", r"\(in\s+Baht\)"),
)

PERIOD_RE = re.compile(
    r"(?:(?:half\s+year|year|quarter|three-month\s+period|six-month\s+period|period)"
    r"\s+ended\s+\d{1,2}(?:st|nd|rd|th)?\s+\w+,?\s+\d{4})",
    re.I,
)
FY_END_RE = re.compile(
    r"financial\s+year\s+ended\s+(\d{1,2}\s+\w+\s+\d{4})|"
    r"year\s+ended\s+(31\s*(?:st)?\s*(?:December|March),?\s+\d{4})",
    re.I,
)
FY_CONVENTION_RE = re.compile(r"\b31\s*(?:st)?\s*(December|March)\b", re.I)

ADJUSTED_LABEL_RE = re.compile(
    r"\b(?:adjusted\s+(?:ebitda|earnings|profit|net\s+profit)"
    r"|underlying\s+(?:ebitda|earnings|profit)"
    r"|ebitda)\b",
    re.I,
)
ADJUSTED_CONNECTOR_RE = re.compile(
    r"\b(?:refers\s+to|means|is\s+defined\s+as|excluding|comprises)\b", re.I
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=\.)\s+")


def _adjusted_measure_definitions(text: str, limit: int = 4) -> tuple[str, ...]:
    """Sentences that both name an earnings measure and define it.
    """
    found: list[str] = []
    for raw in SENTENCE_SPLIT_RE.split(re.sub(r"\s+", " ", text)):
        sentence = raw.strip()
        label = ADJUSTED_LABEL_RE.search(sentence)
        if not label or not ADJUSTED_CONNECTOR_RE.search(sentence):
            continue
        definition = sentence[label.start() :].strip()
        if 40 <= len(definition) <= 400 and definition not in found:
            found.append(definition)
    return tuple(sorted(found)[:limit])


class PageRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    page: int
    text: str
    chars: int
    text_extractable: bool
    reason: str | None = None


class BasisOfPreparation(BaseModel):
    """Everything needed before a figure from this filing may be compared to another."""

    model_config = ConfigDict(frozen=True)
    company_code: str
    legal_entity: str
    folder_label: str
    entity_matches_folder: bool
    reporting_currency: str | None
    units: str | None
    fiscal_year_end: str | None
    fiscal_year_end_convention: str | None
    periods_covered: tuple[str, ...]
    adjusted_measures: tuple[str, ...]
    source_file: str

    @property
    def warnings(self) -> list[str]:
        out: list[str] = []
        if not self.entity_matches_folder:
            out.append(
                f"This filing reports {self.legal_entity}, not the entity named by its "
                f"folder ({self.folder_label}). Do not treat it as a filing of "
                f"{self.folder_label}."
            )
        if self.reporting_currency is None:
            out.append("Reporting currency could not be determined; do not compare figures.")
        return out


class Filing(BaseModel):
    model_config = ConfigDict(frozen=True)
    filename: str
    folder_label: str
    path: str
    doc_type: str
    basis: BasisOfPreparation
    pages: tuple[PageRecord, ...] = ()
    sheets: tuple[Any, ...] = ()

    @property
    def is_workbook(self) -> bool:
        return bool(self.sheets)

    @property
    def usable_pages(self) -> int:
        return sum(1 for p in self.pages if p.text_extractable)

    @property
    def usable_units(self) -> int:
        """Extractable pages, or extractable sheets for a workbook."""
        if self.is_workbook:
            return sum(1 for s in self.sheets if s.text_extractable)
        return self.usable_pages

    @property
    def total_units(self) -> int:
        return len(self.sheets) if self.is_workbook else len(self.pages)


def _garble_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(
        1
        for ch in text
        if ch == "�"
        or (unicodedata.category(ch) in {"Cc", "Co", "Cn"} and ch not in "\r\n\t")
    )
    return bad / len(text)


def _lexicality(text: str) -> float:
    words = WORD_RE.findall(text.lower())
    if not words:
        return 0.0
    return sum(1 for w in words if set(w) & set("aeiou")) / len(words)


def _classify_page(text: str, min_chars: int, max_garble: float) -> tuple[bool, str | None]:
    stripped = text.strip()
    if len(stripped) < min_chars:
        return False, "no_extractable_text"
    if _garble_ratio(text) > max_garble:
        return False, "garbled_text"
    if _lexicality(text) < 0.55:
        return False, "text_not_word_like"
    return True, None


def _first_match(patterns, haystack: str):
    for key, *rest in patterns:
        pattern = rest[-1]
        if re.search(pattern, haystack, re.I):
            return key, rest
    return None, None


def _doc_type(filename: str) -> str:
    lowered = filename.lower()
    if "media_release" in lowered or "media release" in lowered:
        return "media_release"
    if "interim" in lowered or "1h" in lowered:
        return "interim_report"
    if "one-report" in lowered or "annual" in lowered or "integrated" in lowered:
        return "annual_report"
    if "financial_statements" in lowered:
        return "financial_statements"
    return "financial_report"


def extract_filing(path: Path, folder_label: str) -> Filing:
    settings = get_settings()
    doc = pymupdf.open(path)

    pages: list[PageRecord] = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        ok, reason = _classify_page(
            text, settings.min_page_chars, settings.max_replacement_char_ratio
        )
        pages.append(
            PageRecord(
                page=i,
                text=text,
                chars=len(text.strip()),
                text_extractable=ok,
                reason=reason,
            )
        )
    doc.close()

    head = "\n".join(p.text for p in pages[:8])
    whole = "\n".join(p.text for p in pages if p.text_extractable)

    code, entity_rest = _first_match(ENTITY_PATTERNS, head)
    if code is None:
        code, entity_rest = _first_match(ENTITY_PATTERNS, whole)
    legal_entity = entity_rest[0] if entity_rest else "unknown"

    currency = _first_match(CURRENCY_PATTERNS, head)[0] or _first_match(
        CURRENCY_PATTERNS, whole
    )[0]
    units = _first_match(UNIT_PATTERNS, head)[0] or _first_match(UNIT_PATTERNS, whole)[0]

    def _periods_in(source: str) -> tuple[str, ...]:
        return tuple(
            sorted({re.sub(r"\s+", " ", m.group(0)).strip() for m in PERIOD_RE.finditer(source)})
        )

    periods = _periods_in(head) or _periods_in(whole)[:6]

    fy_match = FY_END_RE.search(whole)
    fiscal_year_end = next((g for g in (fy_match.groups() if fy_match else ()) if g), None)
    convention_match = FY_CONVENTION_RE.search(whole)
    fiscal_year_end_convention = (
        f"31 {convention_match.group(1).title()}" if convention_match else None
    )

    adjusted = _adjusted_measure_definitions(whole)

    folder_code = re.search(r"\(([A-Z]+)\)", folder_label)
    expected = folder_code.group(1) if folder_code else folder_label

    basis = BasisOfPreparation(
        company_code=code or "unknown",
        legal_entity=legal_entity,
        folder_label=folder_label,
        entity_matches_folder=(code == expected),
        reporting_currency=currency,
        units=units,
        fiscal_year_end=fiscal_year_end,
        fiscal_year_end_convention=fiscal_year_end_convention,
        periods_covered=periods,
        adjusted_measures=adjusted,
        source_file=path.name,
    )

    log.info(
        "partb.extracted",
        file=path.name,
        entity=legal_entity,
        matches_folder=basis.entity_matches_folder,
        currency=currency,
        units=units,
        pages=len(pages),
        usable=sum(1 for p in pages if p.text_extractable),
    )

    return Filing(
        filename=path.name,
        folder_label=folder_label,
        path=str(path),
        doc_type=_doc_type(path.name),
        basis=basis,
        pages=tuple(pages),
    )


class CompanyBasis(BaseModel):
    """Basis of preparation rolled up to the legal entity.
    """

    model_config = ConfigDict(frozen=True)

    company_code: str
    legal_entity: str
    reporting_currency: str | None
    units: str | None
    fiscal_year_end_convention: str | None
    adjusted_measures: tuple[str, ...]
    filings: tuple[str, ...]
    conflicting_currencies: tuple[str, ...] = ()
    units_by_filing: dict[str, str] = {}

    @property
    def fiscal_note(self) -> str:
        if not self.fiscal_year_end_convention:
            return "Fiscal year end could not be determined from the available filings."
        return f"{self.legal_entity} reports to a {self.fiscal_year_end_convention} year end."


def roll_up(filings: tuple[Filing, ...]) -> dict[str, CompanyBasis]:
    grouped: dict[str, list[Filing]] = {}
    for filing in filings:
        grouped.setdefault(filing.basis.company_code, []).append(filing)

    out: dict[str, CompanyBasis] = {}
    for code, group in sorted(grouped.items()):
        currencies = sorted({f.basis.reporting_currency for f in group if f.basis.reporting_currency})
        conventions = [f.basis.fiscal_year_end_convention for f in group if f.basis.fiscal_year_end_convention]
        units_by_filing = {f.filename: f.basis.units for f in group if f.basis.units}
        distinct_units = sorted(set(units_by_filing.values()))
        measures = sorted({m for f in group for m in f.basis.adjusted_measures})
        out[code] = CompanyBasis(
            company_code=code,
            legal_entity=group[0].basis.legal_entity,
            reporting_currency=currencies[0] if len(currencies) == 1 else None,
            units=distinct_units[0] if len(distinct_units) == 1 else None,
            units_by_filing=units_by_filing,
            fiscal_year_end_convention=conventions[0] if conventions else None,
            adjusted_measures=tuple(measures),
            filings=tuple(sorted(f.filename for f in group)),
            conflicting_currencies=tuple(currencies) if len(currencies) > 1 else (),
        )
    return out


def extract_workbook(path: Path, folder_label: str) -> Filing:
    """A spreadsheet filing.
    """
    from .part_b_xlsx import chunk_workbook, workbook_text

    head = workbook_text(path)

    code, entity_rest = _first_match(ENTITY_PATTERNS, head)
    legal_entity = entity_rest[0] if entity_rest else "unknown"
    currency = _first_match(CURRENCY_PATTERNS, head)[0]
    units = _first_match(UNIT_PATTERNS, head)[0]

    periods = tuple(
        sorted({re.sub(r"\s+", " ", m.group(0)).strip() for m in PERIOD_RE.finditer(head)})
    )[:6]

    fy_end = None

    folder_code = re.search(r"\(([A-Z]+)\)", folder_label)
    expected = folder_code.group(1) if folder_code else folder_label

    basis = BasisOfPreparation(
        company_code=code or "unknown",
        legal_entity=legal_entity,
        folder_label=folder_label,
        entity_matches_folder=(code == expected),
        reporting_currency=currency,
        units=units,
        fiscal_year_end=None,
        fiscal_year_end_convention=fy_end,
        periods_covered=periods,
        adjusted_measures=(),
        source_file=path.name,
    )

    sheets, _ = chunk_workbook(
        path, company_code=basis.company_code, filename=path.name,
        doc_type="financial_statements",
    )

    log.info(
        "partb.workbook_extracted",
        file=path.name,
        entity=legal_entity,
        currency=currency,
        periods=list(periods),
        sheets=len(sheets),
    )

    return Filing(
        filename=path.name,
        folder_label=folder_label,
        path=str(path),
        doc_type="financial_statements",
        basis=basis,
        sheets=tuple(sheets),
    )


def load_filings(part_b_dir: Path | None = None) -> tuple[Filing, ...]:
    from .part_b_xlsx import workbook_paths

    part_b_dir = part_b_dir or get_settings().part_b_dir
    out = [
        extract_filing(pdf, pdf.parent.name)
        for pdf in sorted(part_b_dir.rglob("*.pdf"))
    ]
    out.extend(
        extract_workbook(path, path.parent.name) for path in workbook_paths(part_b_dir)
    )
    out.sort(key=lambda f: f.filename)
    mislabelled = [f.filename for f in out if not f.basis.entity_matches_folder]
    if mislabelled:
        log.warning("partb.entity_folder_mismatch", files=mislabelled)
    return tuple(out)
