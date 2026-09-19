"""Chunking Part B filing pages.
"""

from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict

CHARS_PER_TOKEN = 4
TARGET_TOKENS = 800
OVERLAP_TOKENS = 150
TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN
OVERSIZED_CHARS = 2_500 * CHARS_PER_TOKEN

NUMERIC_TOKEN_RE = re.compile(r"^[\(\[]?[-–+]?[\d,]+(?:\.\d+)?\s?%?[\)\]]?$")
PLACEHOLDER_CELLS = frozenset({"-", "–", "—", "*", "**", "N/A", "n/a", "NA", "nil", "Nil"})
HEADING_RE = re.compile(
    r"^(?:NOTE\s|PART\s|SECTION\s|[A-Z]\d+\.|\d+\.\d*\s|\d+\s+[A-Z])|^[A-Z][A-Z0-9 ,&\-/()]{6,}$"
)
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

MIN_TABLE_LINES = 3
TABLE_LINE_SHARE = 0.5
MIN_CELL_RUN_LINES = 8
MAX_INTERIOR_GAP_LINES = 2
FOOTER_TOLERANCE_LINES = 4


class ChunkKind(StrEnum):
    PROSE = "prose"
    TABLE = "table"


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)
    chunk_id: str
    company_code: str
    filename: str
    doc_type: str
    page: int
    ordinal: int
    kind: ChunkKind
    heading: str | None
    text: str
    chars: int
    oversized: bool = False
    continues_from_previous_page: bool = False
    sheet: str | None = None
    cell_range: str | None = None

    @property
    def citation_token(self) -> str:
        if self.sheet is not None:
            locator = f"{self.sheet}|{self.cell_range}" if self.cell_range else self.sheet
            return f"filing:{self.company_code}|{self.filename}|{locator}"
        return f"filing:{self.company_code}|{self.filename}|p.{self.page}"

    @property
    def embeddable(self) -> bool:
        """Only prose is embedded.
        """
        return self.kind is ChunkKind.PROSE


def line_signal(line: str) -> str:
    """Classify a line as table evidence: 'strong', 'weak' or 'none'.
    """
    tokens = line.split()
    if not tokens:
        return "none"
    if len(tokens) == 1:
        token = tokens[0]
        if NUMERIC_TOKEN_RE.match(token) or token in PLACEHOLDER_CELLS:
            return "weak"
        return "none"
    numeric = sum(1 for t in tokens if NUMERIC_TOKEN_RE.match(t))
    if numeric >= 2 and numeric / len(tokens) >= 0.4:
        return "strong"
    return "none"


def is_numeric_line(line: str) -> bool:
    return line_signal(line) != "none"


def _table_mask(lines: list[str]) -> list[bool]:
    """Which lines belong to a table run.
    """
    signals = [line_signal(ln) for ln in lines]
    mask = [False] * len(lines)

    start = 0
    while start < len(lines):
        if signals[start] == "none":
            start += 1
            continue

        end = start
        evidence = 0
        best_end = start
        while end < len(lines):
            if signals[end] != "none":
                evidence += 1
            span = end - start + 1
            if evidence / span >= TABLE_LINE_SHARE:
                best_end = end
            elif span - evidence > MAX_INTERIOR_GAP_LINES:
                break
            end += 1

        window = signals[start : best_end + 1]
        strong = sum(1 for s in window if s == "strong")
        weak = sum(1 for s in window if s == "weak")
        accepted = len(window) >= MIN_TABLE_LINES and (
            strong >= 1 or weak >= MIN_CELL_RUN_LINES
        )
        if accepted:
            for i in range(start, best_end + 1):
                mask[i] = True
            start = best_end + 1
        else:
            start += 1
    return mask


def is_heading(line: str) -> bool:
    stripped = line.strip()
    if not (3 <= len(stripped) <= 90):
        return False
    if stripped.endswith((".", ";", ",")):
        return False
    return bool(HEADING_RE.match(stripped))


def _blocks(lines: list[str]) -> list[tuple[ChunkKind, list[str]]]:
    """Group consecutive lines into prose and table runs.
    """
    out: list[tuple[ChunkKind, list[str]]] = []
    for line, in_table in zip(lines, _table_mask(lines), strict=True):
        kind = ChunkKind.TABLE if in_table else ChunkKind.PROSE
        if out and out[-1][0] is kind:
            out[-1][1].append(line)
        else:
            out.append((kind, [line]))
    return out


def _pack_prose(text: str) -> list[str]:
    """Split prose to the target size, preferring paragraph then sentence breaks."""
    text = text.strip()
    if len(text) <= TARGET_CHARS:
        return [text] if text else []

    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= TARGET_CHARS:
            pieces.append(para)
            continue
        current = ""
        for sentence in SENTENCE_END_RE.split(para):
            if current and len(current) + len(sentence) + 1 > TARGET_CHARS:
                pieces.append(current.strip())
                # Carry the tail forward so a fact spanning the boundary survives.
                current = current[-OVERLAP_CHARS:] + " " + sentence
            else:
                current = f"{current} {sentence}".strip()
        if current.strip():
            pieces.append(current.strip())

    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) + len(piece) + 1 <= TARGET_CHARS:
            merged[-1] = f"{merged[-1]}\n\n{piece}"
        else:
            merged.append(piece)

    capped: list[str] = []
    for piece in merged:
        while len(piece) > TARGET_CHARS:
            cut = piece.rfind(" ", 0, TARGET_CHARS)
            if cut <= 0:
                cut = TARGET_CHARS
            capped.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            capped.append(piece)
    return capped


def chunk_page(
    *,
    text: str,
    page: int,
    company_code: str,
    filename: str,
    doc_type: str,
    previous_page_ended_in_table: bool = False,
) -> list[Chunk]:
    lines = text.splitlines()
    if not any(ln.strip() for ln in lines):
        return []

    chunks: list[Chunk] = []
    heading: str | None = None
    ordinal = 0
    seen_table = False

    for kind, block_lines in _blocks(lines):
        body = "\n".join(block_lines).strip()
        if not body:
            continue

        if kind is ChunkKind.PROSE:
            for line in block_lines:
                if is_heading(line):
                    heading = line.strip()
            parts = _pack_prose(body)
        else:
            # One chunk, whole, however large.
            parts = [body]

        for part in parts:
            if not part.strip():
                continue
            ordinal += 1
            is_continuation = (
                kind is ChunkKind.TABLE and not seen_table and previous_page_ended_in_table
            )
            if kind is ChunkKind.TABLE:
                seen_table = True
            chunks.append(
                Chunk(
                    chunk_id=f"{company_code}|{filename}|p{page:04d}|c{ordinal:02d}",
                    company_code=company_code,
                    filename=filename,
                    doc_type=doc_type,
                    page=page,
                    ordinal=ordinal,
                    kind=kind,
                    heading=heading,
                    text=part,
                    chars=len(part),
                    oversized=len(part) > OVERSIZED_CHARS,
                    continues_from_previous_page=is_continuation,
                )
            )

    return chunks


def page_ends_in_table(text: str) -> bool:
    """Does a table run reach the bottom of the page?
    """
    lines = text.splitlines()
    mask = _table_mask(lines)
    tail = [flag for line, flag in zip(lines, mask, strict=True) if line.strip()][-FOOTER_TOLERANCE_LINES:]
    return any(tail)


def chunk_filing(filing) -> list[Chunk]:
    """Chunk a filing: pages for a PDF, sheets for a workbook."""
    if getattr(filing, "is_workbook", False):
        from .part_b_xlsx import chunk_workbook

        _, chunks = chunk_workbook(
            Path(filing.path),
            company_code=filing.basis.company_code,
            filename=filing.filename,
            doc_type=filing.doc_type,
        )
        return chunks

    out: list[Chunk] = []
    previous_ended_in_table = False
    for page in filing.pages:
        if not page.text_extractable:
            previous_ended_in_table = False
            continue
        out.extend(
            chunk_page(
                text=page.text,
                page=page.page,
                company_code=filing.basis.company_code,
                filename=filing.filename,
                doc_type=filing.doc_type,
                previous_page_ended_in_table=previous_ended_in_table,
            )
        )
        previous_ended_in_table = page_ends_in_table(page.text)
    return out
