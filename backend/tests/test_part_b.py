"""Part B ingestion: entity identity, extractability, and basis of preparation.

These parse ~45 MB of PDF, so the filings are loaded once per session.
"""

from __future__ import annotations

import pytest

from ingestion.part_b_filings import load_filings, roll_up


@pytest.fixture(scope="session")
def filings():
    return load_filings()


@pytest.fixture(scope="session")
def by_name(filings):
    return {f.filename: f for f in filings}


@pytest.fixture(scope="session")
def companies(filings):
    return roll_up(filings)


# --- extractability (step 1 gate, now a regression test) --------------------


def test_all_filings_are_substantially_extractable(filings):
    """7 PDFs plus the 2 PTTGC workbooks."""
    assert len(filings) == 9
    for filing in filings:
        share = filing.usable_units / filing.total_units
        assert share >= 0.90, f"{filing.filename} only {share:.0%} extractable"


def test_every_part_b_file_on_disk_is_indexed(filings):
    """Guards the gap that let the workbooks sit in the manifest but not the index.

    Nothing previously asserted that a file counted in the corpus was actually
    retrievable, so two PTTGC filings were hashed, reported, and unreachable.
    """
    from app.config import get_settings

    on_disk = {
        p.name
        for pattern in ("*.pdf", "*.xlsx", "*.XLSX")
        for p in get_settings().part_b_dir.rglob(pattern)
    }
    assert {f.filename for f in filings} == on_disk


def test_pttgc_one_report_extracts_fully(by_name):
    """The file predicted most likely to fail. It has no ToUnicode CMaps.

    Kept as a regression test: if a library upgrade breaks glyph-name fallback, this
    is where it shows up, rather than in a note that quietly loses its Thai figures.
    """
    filing = by_name["pttgc-one-report2025-en.pdf"]
    assert len(filing.pages) == 207
    assert filing.usable_pages == 207


def test_unextractable_pages_are_flagged_not_dropped(filings):
    """A page that yields nothing must still exist, carrying a reason."""
    flagged = [p for f in filings for p in f.pages if not p.text_extractable]
    assert flagged, "expected at least one cover/divider page to fail the yield check"
    assert all(p.reason in {"no_extractable_text", "garbled_text", "text_not_word_like"}
               for p in flagged)


# --- entity identity: the document, never the folder ------------------------


def test_petronas_folder_contains_no_pcg_filings(by_name):
    """eval/KNOWN_FAILURES.md section C1.

    All three filings in the folder labelled "PETRONAS Chemicals Group (PCG)" report
    Petroliam Nasional Berhad, the integrated parent. Presenting them as a chemicals
    peer of PTTGC would be a category error.
    """
    petronas_files = [
        "Financial Report 1H 2025.pdf",
        "PETRONAS Group FRA FY2025 - IFR_0.pdf",
        "PETRONAS Interim Financial Report 1H 2026 28.8.2026.pdf",
    ]
    for name in petronas_files:
        basis = by_name[name].basis
        assert basis.company_code == "PETRONAS"
        assert basis.legal_entity == "Petroliam Nasional Berhad (PETRONAS Group)"
        assert basis.folder_label == "PETRONAS Chemicals Group (PCG)"
        assert basis.entity_matches_folder is False
        assert any("not the entity named by its folder" in w for w in basis.warnings)


def test_no_company_rolls_up_as_pcg(companies):
    """There is no PETRONAS Chemicals Group filing in the dataset at all."""
    assert "PCG" not in companies
    assert set(companies) == {"GC", "PETRONAS", "RIL"}


def test_correctly_labelled_folders_do_not_warn(by_name):
    """The check must be specific, or every filing carries a scary warning."""
    for name in ("pttgc-one-report2025-en.pdf", "RIL-Integrated-Annual-Report-2025-26.pdf"):
        assert by_name[name].basis.entity_matches_folder is True
        assert by_name[name].basis.warnings == []


# --- basis of preparation ---------------------------------------------------


def test_three_currencies_three_conventions(companies):
    assert companies["PETRONAS"].reporting_currency == "MYR"
    assert companies["GC"].reporting_currency == "THB"
    assert companies["RIL"].reporting_currency == "INR"

    assert companies["PETRONAS"].fiscal_year_end_convention == "31 December"
    assert companies["GC"].fiscal_year_end_convention == "31 December"
    assert companies["RIL"].fiscal_year_end_convention == "31 March"


def test_units_differ_across_companies(companies):
    """Malaysian ringgit millions against Indian rupee crore."""
    assert companies["PETRONAS"].units == "millions"
    assert companies["RIL"].units == "crore"


def test_units_differing_within_one_company_are_not_silently_resolved(companies):
    """PTTGC states Baht millions in its annual report and plain Baht in its interim
    statements. Picking one would misstate every figure from the other by a factor of
    a million, so the company-level value is withheld and the per-filing map is the
    source of truth."""
    gc = companies["GC"]
    assert gc.units is None
    assert gc.units_by_filing["pttgc-one-report2025-en.pdf"] == "millions"
    assert gc.units_by_filing["FINANCIAL_STATEMENTS.XLSX"] == "ones"
    assert set(gc.units_by_filing.values()) == {"millions", "ones"}


def test_ril_year_end_recovered_from_sibling_filings(by_name, companies):
    """The Q1 release never states a year end; a quarterly release has no reason to.

    The company-level rollup recovers it from Reliance's other filings rather than
    leaving it unknown or inventing it.
    """
    q1 = by_name["Media_Release_RIL_Q1_FY2026-27_Financial_and_Operational_Performance.pdf"]
    assert q1.basis.fiscal_year_end_convention is None
    assert companies["RIL"].fiscal_year_end_convention == "31 March"


def test_gc_adjusted_ebitda_definition_captured_verbatim(companies):
    """Each company defines adjusted earnings its own way; the definition is the fact."""
    measures = companies["GC"].adjusted_measures
    assert len(measures) == 1, f"expected one deduplicated definition, got {measures}"
    definition = measures[0]
    assert definition.startswith("Adjusted EBITDA refers to EBITDA excluding")
    for excluded in ("Stock gain", "NRV", "commodity hedging", "Extra item"):
        assert excluded in definition


def test_no_false_positive_earnings_measures(companies):
    """'Each GDR represents 4 (Four) underlying equity shares' is not a definition
    of adjusted earnings. A looser label pattern matched it."""
    for company in companies.values():
        for measure in company.adjusted_measures:
            assert "GDR" not in measure
            assert "equity shares" not in measure


def test_periods_are_whitespace_normalised(by_name):
    """These labels are laid out across table cells and arrive with newlines.

    Without normalisation the same period appears several times as distinct strings.
    """
    interim = by_name["PETRONAS Interim Financial Report 1H 2026 28.8.2026.pdf"]
    assert any("30 June 2026" in p for p in interim.basis.periods_covered)
    assert all("\n" not in p for p in interim.basis.periods_covered)


def test_h1_2026_alignment_and_ril_gap(by_name, companies):
    """The strongest Part B comparison case, asserted at the data level.

    PETRONAS and GC both report a six-month period to 30 June 2026. Reliance does not:
    its filings straddle that window as Q4 FY2025-26 (to 31 March) and Q1 FY2026-27
    (to 30 June), across a fiscal-year boundary.
    """
    petronas_h1 = by_name["PETRONAS Interim Financial Report 1H 2026 28.8.2026.pdf"]
    assert any("30 June 2026" in p for p in petronas_h1.basis.periods_covered)

    q4 = by_name["24042026_Media_Release_RIL_Q4_FY2025-26_Financial_and_Operational_Performance.pdf"]
    q1 = by_name["Media_Release_RIL_Q1_FY2026-27_Financial_and_Operational_Performance.pdf"]
    assert any("31st March 2026" in p.lower() or "31ST MARCH, 2026" in p
               for p in q4.basis.periods_covered)
    assert any("30th June 2026" in p.lower() or "30TH JUNE, 2026" in p
               for p in q1.basis.periods_covered)

    # The two Reliance quarters sit either side of its 31 March year end.
    assert companies["RIL"].fiscal_year_end_convention == "31 March"
    assert companies["PETRONAS"].fiscal_year_end_convention == "31 December"
