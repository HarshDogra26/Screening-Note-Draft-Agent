"""Enumerations.
"""

from __future__ import annotations
from enum import StrEnum


class Domain(StrEnum):

    PART_A = "part_a"
    PART_B = "part_b"


# --- plants.csv ------------------------------------------------------------


class PlantStatus(StrEnum):
    OPERATING = "Operating"
    UNDER_CONSTRUCTION = "Under construction"
    IDLED = "Idled"


class Product(StrEnum):
    ETHYLENE = "Ethylene"
    PROPYLENE = "Propylene"
    HDPE = "HDPE"
    LDPE = "LDPE"
    LLDPE = "LLDPE"
    PP = "PP"
    NAPHTHA = "Naphtha"  # prices.csv only; not a plant product


class Region(StrEnum):
    SOUTHEAST_ASIA = "Southeast Asia"
    NORTHEAST_ASIA = "Northeast Asia"
    SOUTH_ASIA = "South Asia"
    MIDDLE_EAST = "Middle East"
    NW_EUROPE = "NW Europe"


class SourceType(StrEnum):
    COMPANY_ANNOUNCEMENT = "Company announcement"
    REGULATORY_FILING = "Regulatory filing"
    PRICE_ASSESSMENT = "Price assessment"
    NOT_PUBLICLY_CONFIRMED = "Not publicly confirmed"


# --- prices.csv ------------------------------------------------------------


class Basis(StrEnum):

    SPOT = "Spot"
    CONTRACT = "Contract"


class PriceRegion(StrEnum):

    SOUTHEAST_ASIA_CFR = "Southeast Asia CFR"
    NORTHEAST_ASIA_CFR = "Northeast Asia CFR"
    ASIA_CFR = "Asia CFR"


# --- documents -------------------------------------------------------------


class DocSourceType(StrEnum):
    COMPANY_PRESS_RELEASE = "Company press release"
    INDUSTRY_BULLETIN = "Industry association bulletin"
    GOVERNMENT_AGENCY = "Government agency publication"


# --- grounding -------------------------------------------------------------


class SourceKind(StrEnum):
    PLANT = "plant"
    PRICE = "price"
    DOCUMENT = "document"
    FILING = "filing"


class ClaimStatus(StrEnum):
    ASSERTED = "asserted"
    NOT_PUBLICLY_CONFIRMED = "not_publicly_confirmed"
    DISPUTED = "disputed"


class GapReason(StrEnum):

    NOT_PUBLICLY_CONFIRMED = "not_publicly_confirmed"
    NO_ASSESSMENT = "no_assessment"
    NOT_DISCLOSED = "not_disclosed"
    NOT_EXTRACTABLE = "not_extractable"
    OUT_OF_CORPUS = "out_of_corpus"


class RefusalReason(StrEnum):
    FORECAST_REQUESTED = "forecast_requested"
    OUT_OF_CORPUS = "out_of_corpus"
    CROSS_DOMAIN = "cross_domain"
    REQUIRES_OUTSIDE_KNOWLEDGE = "requires_outside_knowledge"
    NOT_PUBLISHED_BY_SOURCE = "not_published_by_source"


class ComparabilityVerdict(StrEnum):
    COMPARABLE = "comparable"
    COMPARABLE_WITH_CAVEAT = "comparable_with_caveat"
    NOT_COMPARABLE = "not_comparable"


class WarningCode(StrEnum):
    BASIS_CHANGE = "basis_change"
    COVERAGE_GAP = "coverage_gap"
    ENDPOINT_NOT_PUBLICLY_CONFIRMED = "endpoint_not_publicly_confirmed"
    REGION_MISMATCH = "region_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    FISCAL_PERIOD_MISMATCH = "fiscal_period_mismatch"
    SEGMENT_STRUCTURE_MISMATCH = "segment_structure_mismatch"
    ADJUSTED_MEASURE_DEFINITION_MISMATCH = "adjusted_measure_definition_mismatch"


class SectionKind(StrEnum):

    SUMMARY = "summary"
    SUPPLY_BASE = "supply_base"
    CAPACITY_CHANGES = "capacity_changes"
    PRICE_MOVEMENT = "price_movement"
    FEEDSTOCK_MARGIN = "feedstock_margin"
    COMPANY_PROFILE = "company_profile"
    FINANCIAL_PERFORMANCE = "financial_performance"
    COMPETITOR_COMPARISON = "competitor_comparison"
    OPERATIONAL_EVENTS = "operational_events"
    GAPS_AND_CONFLICTS = "gaps_and_conflicts"
