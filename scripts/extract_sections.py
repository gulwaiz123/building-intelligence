"""Extract sections from BNS 2023 and IPC 1860 bare act PDFs into JSONL corpus files.

BNS strategy:
  1. Parse the Arrangement of Sections (index, pages 2-19) to build a
     section_number -> heading mapping.
  2. Parse the body text (pages 74+, 0-indexed 73+) to extract full section
     text, using chapter markers in the body for precise chapter boundaries.
  3. Merge: heading from index, chapter+text from body.

IPC strategy:
  Ghostscript-produced PDF with inconsistent text formatting. Sections
  appear as "<num>. <heading>.--<body>" with occasional standalone heading
  lines before the numbered line. Large margin section numbers also act as
  boundary markers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pymupdf

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
BNS_PDF = DATA_RAW / "BNS_2023_bare_act.pdf"
IPC_PDF = DATA_RAW / "IPC_1860_bare_act.pdf"
BNS_OUTPUT = DATA_PROCESSED / "bns_sections.jsonl"
IPC_OUTPUT = DATA_PROCESSED / "ipc_sections.jsonl"

BNS_ACT = "BNS_2023"
IPC_ACT = "IPC_1860"
BNS_ACT_LABEL = "Bharatiya Nyaya Sanhita, 2023"
IPC_ACT_LABEL = "Indian Penal Code, 1860"

PARSER_NAME = "pymupdf"

# Page ranges (1-indexed, inclusive start, exclusive end)
BNS_INDEX_START = 1   # page 2
BNS_INDEX_END = 19    # page 19 (exclusive, 0-indexed 18)
BNS_BODY_START = 73   # page 74 (0-indexed)

# Header/footer patterns to strip
HEADER_PATTERNS = [
    re.compile(r"^BHARATIYA\s+NYAYA\s+SANHITA,\s*2023\s*\(BNS\)\s*$", re.IGNORECASE),
    re.compile(r"^THE\s+INDIAN\s+PENAL\s+CODE,\s*1860\s*$", re.IGNORECASE),
]
FOOTER_PATTERNS = [
    "HomePage",
    "Chapters and Sections",
]
SEPARATOR_PATTERN = re.compile(r"^-{3,}\s*$")

CHAPTER_RE = re.compile(r"^CHAPTER\s+([IVXLCDM]+)\s*$")
# Section number at start of line: "123.", "123. ", or "123.(1)"
SECTION_START_RE = re.compile(r"^(\d{1,3})\.")
# IPC section with .-- separator: "123. Heading.--body"
IPC_SECTION_RE = re.compile(r"^(\d{1,3}[A-Z]?)\.\s+(.+?)[.]\s*[-–—]+\s*(.*)", re.DOTALL)
# Large standalone section number (IPC margin numbers like "102" on their own line)
IPC_MARGIN_NUM_RE = re.compile(r"^(\d{2,3})\s*$")

# Non-section sub-numbering: "(1) ", "(a) ", "Illustration", "Exception"
SUB_CLAUSE_RE = re.compile(r"^\(\d+\)")
ILLUSTRATION_RE = re.compile(r"^Illustrations?[.]?", re.IGNORECASE)
EXCEPTION_RE = re.compile(r"^Exceptions?\s*\d*[.]?", re.IGNORECASE)
EXPLANATION_RE = re.compile(r"^Explanation\s*\d*[.]?", re.IGNORECASE)


def sha256_hex(path: Path) -> str:
    """Return hex digest of file at *path*."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_parser_version() -> str:
    return pymupdf.__version__


# ---------------------------------------------------------------------------
# BNS parser
# ---------------------------------------------------------------------------

def parse_bns_index(doc: pymupdf.Document) -> dict[int, str]:
    """Parse Arrangement of Sections (pages 2-19) to map section_number -> heading.

    Index format: section number on one line ("1."), heading on next line(s).
    Long headings may span multiple lines (continued text is indented).
    """
    headings: dict[int, str] = {}
    pending_number: int | None = None
    pending_lines: list[str] = []
    in_index = False

    for pg in range(BNS_INDEX_START, BNS_INDEX_END):
        text = doc[pg].get_text("text")
        for line in text.split("\n"):
            line = line.strip()
            if not line or line in FOOTER_PATTERNS:
                continue
            if SEPARATOR_PATTERN.match(line):
                continue

            # Detect start of arrangement
            if line == "ARRANGEMENT OF SECTIONS":
                in_index = True
                continue
            if not in_index:
                continue

            # Skip the "SECTION" column header
            if line == "SECTION":
                continue

            # Chapter markers within index — flush any pending section
            if CHAPTER_RE.match(line):
                _flush_index_section(headings, pending_number, pending_lines)
                pending_number = None
                pending_lines = []
                continue

            # Chapter title or section heading continuation
            if line.isupper() or re.match(r"^OF\s", line):
                _flush_index_section(headings, pending_number, pending_lines)
                pending_number = None
                pending_lines = []
                continue

            # Section number line: "123."
            num_match = re.match(r"^(\d{1,3})\.\s*$", line)
            if num_match:
                _flush_index_section(headings, pending_number, pending_lines)
                pending_number = int(num_match.group(1))
                pending_lines = []
                continue

            # Heading text line (when we have a pending section number)
            if pending_number is not None:
                # Skip non-heading lines that follow section numbers
                if line.startswith(("Note:", "———")):
                    continue
                pending_lines.append(line)
                continue

    # Flush last section
    _flush_index_section(headings, pending_number, pending_lines)

    return headings


def _flush_index_section(
    headings: dict[int, str],
    number: int | None,
    lines: list[str],
) -> None:
    """Save accumulated heading lines for a section number."""
    if number is None or not lines:
        return
    heading = " ".join(lines).strip().rstrip(".")
    heading = re.sub(r"\s+", " ", heading).strip()
    if heading:
        headings[number] = heading


def roman_to_int(roman: str) -> int:
    """Convert roman numeral string to integer."""
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    result = 0
    prev = 0
    for char in reversed(roman):
        cur = values[char]
        if cur >= prev:
            result += cur
        else:
            result -= cur
        prev = cur
    return result


def _is_continuation_line(line: str) -> bool:
    """Check if line looks like continuation of previous section body."""
    if not line:
        return True
    if CHAPTER_RE.match(line):
        return False
    if SECTION_START_RE.match(line):
        # Check if it's a sub-clause like "(1)" or "(a)"
        m = SECTION_START_RE.match(line)
        rest = line[m.end():]
        return bool(SUB_CLAUSE_RE.match(rest))
    return True


def _strip_header_line(line: str) -> str | None:
    """Return None if line is a page header/footer, else the line."""
    if not line or not line.strip():
        return None
    stripped = line.strip()
    for pat in HEADER_PATTERNS:
        if pat.match(stripped):
            return None
    if stripped in FOOTER_PATTERNS:
        return None
    if SEPARATOR_PATTERN.match(stripped):
        return None
    return line


def parse_bns_body(doc: pymupdf.Document) -> list[dict]:
    """Parse BNS body text (pages 74+) into list of section dicts.

    Returns list of dicts with keys: chapter, chapter_title, section_number,
    heading, text.
    """
    sections: list[dict] = []
    current_chapter: str | None = None
    current_chapter_title: str | None = None
    current_section: dict | None = None
    pending_heading: str | None = None

    for pg in range(BNS_BODY_START, doc.page_count):
        text = doc[pg].get_text("text")
        lines = text.split("\n")

        for i, raw_line in enumerate(lines):
            filtered = _strip_header_line(raw_line)
            if filtered is None:
                continue
            line = filtered.strip()
            if not line:
                continue

            # Chapter marker
            ch_match = CHAPTER_RE.match(line)
            if ch_match:
                if current_section:
                    sections.append(current_section)
                    current_section = None
                current_chapter = ch_match.group(1)
                current_chapter_title = None
                # Next non-empty line is chapter title
                for j in range(i + 1, len(lines)):
                    next_raw = _strip_header_line(lines[j])
                    if next_raw and next_raw.strip():
                        ct = next_raw.strip().rstrip(".")
                        current_chapter_title = ct
                        break
                pending_heading = None
                continue

            # Section start: "123. text"
            sec_match = SECTION_START_RE.match(line)
            if sec_match:
                num = int(sec_match.group(1))
                rest = line[sec_match.end():].strip()

                # If we have a current section and the number matches or
                # the rest is clearly a sub-clause continuation, append.
                if current_section is not None:
                    if num != current_section["section_number"]:
                        # New section — fall through
                        pass
                    elif SUB_CLAUSE_RE.match(rest):
                        # Same section number with sub-clause — continuation
                        current_section["text_lines"].append(line)
                        continue
                    else:
                        # Same number but not a sub-clause — could be
                        # continuation of body. Append.
                        current_section["text_lines"].append(line)
                        continue
                # Only reach here for genuinely new sections
                if current_section:
                    sections.append(current_section)

                heading = pending_heading if pending_heading else ""
                pending_heading = None

                current_section = {
                    "chapter": current_chapter,
                    "chapter_title": current_chapter_title,
                    "section_number": num,
                    "heading": heading,
                    "text_lines": [rest] if rest else [],
                }
                continue

            # Check if this line is a heading (before a section number
            # appears on a following line). Headings are not all-caps chapter
            # titles and not continuation text.
            if (
                current_section is None
                and not line.isupper()
                and line.endswith(".")
                and len(line) < 200
            ):
                pending_heading = line.rstrip(".")
                continue

            # Continuation of current section
            if current_section:
                current_section["text_lines"].append(line)
            else:
                # Before any section — might be preamble/intro text. Skip.
                pass

    # Don't forget last section
    if current_section:
        sections.append(current_section)

    return sections


def build_bns_records(
    headings: dict[int, str],
    body_sections: list[dict],
    source_pdf: str,
    source_sha256: str,
    parser_version: str,
) -> list[dict]:
    """Merge index headings with body text to produce final records."""
    records = []
    seen = set()

    for sec in body_sections:
        num = sec["section_number"]
        if num in seen:
            continue
        seen.add(num)

        # Prefer index heading, fall back to body heading
        heading = headings.get(num) or sec.get("heading", "")
        text = _clean_section_text(sec["text_lines"])

        needs_review = False
        if not text.strip():
            needs_review = True

        records.append({
            "section_id": f"bns:{num}",
            "act": BNS_ACT,
            "act_label": BNS_ACT_LABEL,
            "status": "in_force",
            "chapter": sec.get("chapter") or "",
            "chapter_title": sec.get("chapter_title") or "",
            "section_number": num,
            "heading": heading,
            "text": text,
            "source_pdf": source_pdf,
            "source_sha256": source_sha256,
            "parser": PARSER_NAME,
            "parser_version": parser_version,
            "source_status_version": "v1",
            "needs_review": needs_review,
        })

    return records


def _clean_section_text(lines: list[str]) -> str:
    """Collapse within-paragraph breaks, preserve paragraph boundaries."""
    if not lines:
        return ""
    text = " ".join(lines)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Collapse space before punctuation
    text = re.sub(r"\s+([.,;:)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return text


# ---------------------------------------------------------------------------
# IPC parser
# ---------------------------------------------------------------------------

def parse_ipc(doc: pymupdf.Document) -> list[dict]:
    """Parse IPC 1860 PDF into list of section dicts.

    IPC is a scanned/Ghostscript PDF. Text extraction quality varies.
    Section format: heading (optional standalone line), then
    "<num>. <heading>.--<body>" with .-- as the heading/body separator,
    or "<num>. <heading>" (no .-- , body on next lines).
    """
    sections: list[dict] = []
    current_chapter: str | None = None
    current_chapter_title: str | None = None
    current_section: dict | None = None
    pending_heading: str | None = None

    all_lines: list[str] = []
    for pg in range(doc.page_count):
        text = doc[pg].get_text("text")
        for line in text.split("\n"):
            filtered = _strip_header_line(line)
            if filtered is None:
                continue
            stripped = filtered.strip()
            if not stripped:
                continue
            if SEPARATOR_PATTERN.match(stripped):
                continue
            # Skip footnote/reference markers
            if re.match(r"^\d*\*+$", stripped):
                continue
            if re.match(r"^\*+\d*$", stripped):
                continue
            # Skip lines that are just "1." or "2." etc. — page artifacts
            if re.match(r"^\d{1,2}\.\s*$", stripped):
                continue
            all_lines.append(stripped)

    i = 0
    while i < len(all_lines):
        line = all_lines[i]

        # Chapter marker
        ch_match = CHAPTER_RE.match(line)
        if ch_match:
            _flush_ipc_section(sections, current_section)
            current_section = None
            current_chapter = ch_match.group(1)
            current_chapter_title = None
            pending_heading = None
            if i + 1 < len(all_lines):
                nxt = all_lines[i + 1]
                if not re.match(r"^\d+\.\s", nxt) and not CHAPTER_RE.match(nxt):
                    current_chapter_title = nxt.rstrip(".")
                    i += 1
            i += 1
            continue

        # Try IPC section with .-- separator: "num. heading.--body"
        ipc_match = _match_ipc_section(line)
        if ipc_match:
            _flush_ipc_section(sections, current_section)
            num = ipc_match["section_number"]
            heading = ipc_match["heading"]
            body_start = ipc_match["body_start"]

            if pending_heading and _headings_match(pending_heading, heading):
                heading = pending_heading
            pending_heading = None

            current_section = {
                "chapter": current_chapter,
                "chapter_title": current_chapter_title,
                "section_number": num,
                "heading": heading,
                "text_lines": [body_start] if body_start else [],
            }
            i += 1
            continue

        # Simple section start: "num. heading text" (no .-- separator)
        # BUT heading text should NOT look like a sub-clause "(1)"
        sec_match = SECTION_START_RE.match(line)
        if sec_match:
            num = int(sec_match.group(1))
            rest = line[sec_match.end():].strip()

            # Skip common false positives
            if num <= 20 and len(rest) < 5:
                # Could be a page number artifact — only treat as section
                # if followed by substantial heading text
                pass
            if SUB_CLAUSE_RE.match(rest):
                if current_section:
                    current_section["text_lines"].append(line)
                i += 1
                continue

            # This is a new section
            _flush_ipc_section(sections, current_section)

            if pending_heading:
                heading = pending_heading
                pending_heading = None
            else:
                # Try to extract heading from rest
                hb = re.split(r"[.]\s*[-–—]+", rest, maxsplit=1)
                if len(hb) == 2:
                    heading = hb[0].strip().rstrip(".")
                    body = hb[1].strip()
                else:
                    heading = rest.rstrip(".")
                    body = ""

            current_section = {
                "chapter": current_chapter,
                "chapter_title": current_chapter_title,
                "section_number": num,
                "heading": heading,
                "text_lines": [body] if body else [],
            }
            i += 1
            continue

        # Potential heading line before a section number
        if current_section is None and _looks_like_heading(line):
            if pending_heading:
                # Accumulate multi-line headings
                pending_heading = pending_heading + " " + line.rstrip(".")
            else:
                pending_heading = line.rstrip(".")
            i += 1
            continue

        # Continuation of current section body
        if current_section:
            current_section["text_lines"].append(line)
        elif current_chapter and not _looks_like_heading(line):
            # Body text before any section detected — might be preamble
            pass

        i += 1

    _flush_ipc_section(sections, current_section)

    # Post-processing: split inline section markers from section body text
    sections = _split_inline_ipc_sections(sections)

    return sections


def _split_inline_ipc_sections(sections: list[dict]) -> list[dict]:
    """Scan each section's body text for embedded section markers.

    IPC PDF merges adjacent sections mid-line:
    "...within India.  4.  Extension of Code..."
    """
    existing = {s["section_number"] for s in sections}

    # Matches "  123.  Heading." or "  123.  Heading.--Body"
    inline_re = re.compile(
        r"\s{2,}(\d{1,3})\.\s{2,}([A-Z][^.]+\.?)"
        r"\s*(?:[-–—]+\s*(.+?))?"
        r"(?=\s{2,}\d{1,3}\.\s{2,}[A-Z]|\s*$)",
        re.DOTALL,
    )

    result: list[dict] = []
    for section in sections:
        body_text = " ".join(section.get("text_lines", []))
        if not body_text:
            result.append(section)
            continue

        matches = list(inline_re.finditer(body_text))
        if not matches:
            result.append(section)
            continue

        extracted = set()
        kept_parts = []
        last_end = 0

        for m in matches:
            prefix = body_text[last_end : m.start()].strip()
            num = int(m.group(1))
            heading = m.group(2).strip().rstrip(".")
            inline_body = (m.group(3) or "").strip()

            if prefix and last_end == 0:
                kept_parts.append(prefix)

            if num not in existing and num not in extracted:
                child = {
                    "chapter": section.get("chapter"),
                    "chapter_title": section.get("chapter_title"),
                    "section_number": num,
                    "heading": heading,
                    "text_lines": [inline_body] if inline_body else [],
                }
                if inline_body:
                    result.append(child)
                    extracted.add(num)

            last_end = m.end()

        # Update parent with remaining text
        remaining = body_text[last_end:].strip()
        if kept_parts:
            kept_parts.append(remaining)
            section["text_lines"] = [" ".join(p for p in kept_parts if p)]
        elif remaining:
            section["text_lines"] = [remaining]
        result.append(section)

    return result


def _match_ipc_section(line: str) -> dict | None:
    """Match IPC section format: 'num. heading.--body' or 'num. heading.—body'."""
    m = re.match(r"^(\d{1,3}[A-Z]?)\.\s+(.+?)[.]\s*[-–—]+\s*(.+)", line)
    if not m:
        return None
    num_str = m.group(1)
    heading = m.group(2).strip().rstrip(".")
    body = m.group(3).strip()
    try:
        section_num = int(re.match(r"^(\d+)", num_str).group(1))
    except (ValueError, AttributeError):
        return None
    return {"section_number": section_num, "heading": heading, "body_start": body}


def _looks_like_heading(line: str) -> bool:
    """Check if a line looks like it could be a section heading."""
    if not line:
        return False
    if CHAPTER_RE.match(line):
        return False
    if SECTION_START_RE.match(line):
        return False
    if re.match(r"^\d+$", line):
        return False
    if SEPARATOR_PATTERN.match(line):
        return False
    # Headings are typically sentence case, not all caps
    if line.isupper() and len(line) > 30:
        return False  # probably a chapter title
    return bool(re.match(r"^[A-Z]", line) and len(line) > 5 and len(line) < 200)


def _flush_ipc_section(sections: list[dict], section: dict | None) -> None:
    """Add completed section to list if it has meaningful content."""
    if section is None:
        return
    # Only flush if there's actual text content
    if section.get("text_lines"):
        sections.append(section)


def _headings_match(a: str, b: str) -> bool:
    """Check if two heading strings are essentially the same."""
    a_norm = re.sub(r"\s+", " ", a.strip().rstrip(".").lower())
    b_norm = re.sub(r"\s+", " ", b.strip().rstrip(".").lower())
    if a_norm == b_norm:
        return True
    # One contains the other
    return bool(a_norm in b_norm or b_norm in a_norm)


def build_ipc_records(
    body_sections: list[dict],
    source_pdf: str,
    source_sha256: str,
    parser_version: str,
) -> list[dict]:
    """Build final IPC records from parsed sections."""
    records = []
    seen = set()

    for sec in body_sections:
        num = sec["section_number"]
        # IPC may have section numbers with letters (e.g., 29A)
        # but we use just the integer for section_number
        if num in seen:
            continue
        seen.add(num)

        text = _clean_section_text(sec["text_lines"])
        heading = sec.get("heading", "")

        needs_review = False
        if not text.strip():
            needs_review = True

        records.append({
            "section_id": f"ipc:{num}",
            "act": IPC_ACT,
            "act_label": IPC_ACT_LABEL,
            "status": "repealed",
            "chapter": sec.get("chapter") or "",
            "chapter_title": sec.get("chapter_title") or "",
            "section_number": num,
            "heading": heading,
            "text": text,
            "source_pdf": source_pdf,
            "source_sha256": source_sha256,
            "parser": PARSER_NAME,
            "parser_version": parser_version,
            "source_status_version": "v1",
            "needs_review": needs_review,
        })

    return records


# ---------------------------------------------------------------------------
# File I/O with safe re-run
# ---------------------------------------------------------------------------

def corpus_exists_and_matches(output_path: Path, expected_sha: str) -> bool:
    """Check if output file exists and its records have matching source hash."""
    if not output_path.exists():
        return False
    try:
        with open(output_path) as f:
            first_line = f.readline().strip()
            if not first_line:
                return False
            record = json.loads(first_line)
            return record.get("source_sha256") == expected_sha
    except (json.JSONDecodeError, KeyError):
        return False


def write_jsonl_atomic(path: Path, records: list[dict]) -> None:
    """Write records to a temp file then rename over *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in records:
                json.dump(rec, f, ensure_ascii=False)
                f.write("\n")
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def verify_inputs() -> tuple[str, str]:
    """Verify PDF files exist and hashes match PROVENANCE.md. Returns SHAs."""
    provenance = DATA_RAW / "PROVENANCE.md"
    if not provenance.exists():
        print(f"ERROR: {provenance} not found", file=sys.stderr)
        sys.exit(1)

    provenance.read_text()
    bns_expected = "a83f12a93e32c9e0b39a85f75850a448dc4682b1b44b2d6bd680165bb9931549"
    ipc_expected = "ef8945c5d1b02904da67959e245b87bd5751ed5563d03ab0079758909f145309"

    if not BNS_PDF.exists():
        print(f"ERROR: {BNS_PDF} not found", file=sys.stderr)
        sys.exit(1)
    if not IPC_PDF.exists():
        print(f"ERROR: {IPC_PDF} not found", file=sys.stderr)
        sys.exit(1)

    bns_sha = sha256_hex(BNS_PDF)
    ipc_sha = sha256_hex(IPC_PDF)

    if bns_sha != bns_expected:
        print(f"ERROR: BNS SHA-256 mismatch\nexpected: {bns_expected}\n     got: {bns_sha}", file=sys.stderr)
        sys.exit(1)
    if ipc_sha != ipc_expected:
        print(f"ERROR: IPC SHA-256 mismatch\nexpected: {ipc_expected}\n     got: {ipc_sha}", file=sys.stderr)
        sys.exit(1)

    print(f"✓ BNS SHA-256: {bns_sha}")
    print(f"✓ IPC SHA-256: {ipc_sha}")
    return bns_sha, ipc_sha


def main() -> None:
    bns_sha, ipc_sha = verify_inputs()
    parser_version = get_parser_version()
    print(f"Parser: {PARSER_NAME} {parser_version}")

    bns_source = str(BNS_PDF.relative_to(ROOT))
    ipc_source = str(IPC_PDF.relative_to(ROOT))

    # --- BNS ---
    bns_needs_extraction = not corpus_exists_and_matches(BNS_OUTPUT, bns_sha)
    if bns_needs_extraction:
        print("Extracting BNS sections...")
        doc = pymupdf.open(str(BNS_PDF))
        try:
            headings = parse_bns_index(doc)
            print(f"  Index: {len(headings)} section headings found")
            body = parse_bns_body(doc)
            print(f"  Body: {len(body)} sections found")
            records = build_bns_records(headings, body, bns_source, bns_sha, parser_version)
            write_jsonl_atomic(BNS_OUTPUT, records)
            print(f"  Wrote {len(records)} records to {BNS_OUTPUT}")
        finally:
            doc.close()
    else:
        print(f"BNS corpus up to date — skipping ({BNS_OUTPUT})")

    # --- IPC ---
    ipc_needs_extraction = not corpus_exists_and_matches(IPC_OUTPUT, ipc_sha)
    if ipc_needs_extraction:
        print("Extracting IPC sections...")
        doc = pymupdf.open(str(IPC_PDF))
        try:
            body = parse_ipc(doc)
            print(f"  Body: {len(body)} sections found")
            records = build_ipc_records(body, ipc_source, ipc_sha, parser_version)
            write_jsonl_atomic(IPC_OUTPUT, records)
            print(f"  Wrote {len(records)} records to {IPC_OUTPUT}")
        finally:
            doc.close()
    else:
        print(f"IPC corpus up to date — skipping ({IPC_OUTPUT})")

    # Summary
    _print_summary()


def _print_summary() -> None:
    """Print record counts and review flags."""
    for label, path in [("BNS", BNS_OUTPUT), ("IPC", IPC_OUTPUT)]:
        if not path.exists():
            print(f"{label}: file not found", file=sys.stderr)
            continue
        records = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        review = [r for r in records if r.get("needs_review")]
        empty = [r for r in records if not r["text"].strip()]
        print(f"{label}: {len(records)} records, {len(review)} needs_review, {len(empty)} empty-text")
        if review:
            ids = [r["section_id"] for r in review[:10]]
            print(f"  flagged: {', '.join(ids)}{' ...' if len(review) > 10 else ''}")


if __name__ == "__main__":
    main()