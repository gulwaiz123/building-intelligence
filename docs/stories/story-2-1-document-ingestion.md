# Story 2.1 — Document Ingestion

Second story of the three-day "Building Intelligence with RAG" classroom course. Follows Story 1.1. Turns the supplied BNS and IPC PDFs into a small, inspectable section-level JSONL corpus. MongoDB, embeddings, vector indexes, retrieval, and answer generation belong to later stories.

## Purpose

Produce two JSONL corpus files from the supplied `data/raw/` PDFs:

- `data/processed/bns_sections.jsonl` — every section of the Bharatiya Nyaya Sanhita, 2023
- `data/processed/ipc_sections.jsonl` — every section of the Indian Penal Code, 1860

One JSON object per line, one record per section. No rewriting of meaning. No MongoDB, no embeddings, no vector indexes, no retrieval. The corpus must be small enough to inspect by eye and structured so later chunking can preserve parent section and source information.

## Prerequisites

- Story 1.1 complete (project seeded, `uv sync` works, `.env.example` exists).
- Python 3.12 and UV available.
- `data/raw/BNS_2023_bare_act.pdf` present and matches SHA-256 in `data/raw/PROVENANCE.md` (`a83f12a9…`).
- `data/raw/IPC_1860_bare_act.pdf` present and matches SHA-256 in `data/raw/PROVENANCE.md` (`ef8945c5…`).
- `data/raw/PROVENANCE.md` present.

## Inputs

| Input | Location |
|---|---|
| BNS 2023 bare act PDF | `data/raw/BNS_2023_bare_act.pdf` (237 pages, A4, Word-to-PDF) |
| IPC 1860 bare act PDF | `data/raw/IPC_1860_bare_act.pdf` (227 pages, letter, scanned/Ghostscript-produced) |
| Provenance document | `data/raw/PROVENANCE.md` |
| Architecture document | `docs/architecture.md` |
| Project `.env.example` | `.env.example` (do not add or rename settings) |

## Work to do

### 1. Verify inputs

Check all three `data/raw/` files exist. Verify each PDF SHA-256 against `PROVENANCE.md`. If either PDF is missing or has a different hash, stop and report exactly which file and the discrepancy. Do not proceed with mismatched inputs.

BNS act label is `BNS_2023`, status `in_force`. IPC act label is `IPC_1860`, status `repealed`. These values come from `PROVENANCE.md` and are fixed.

### 2. Choose PDF parsing dependency

Pick a PDF text-extraction library and add it to `pyproject.toml` as a project dependency. Recommended starting point: `pymupdf` (fitz) — it handles both Word-to-PDF and Ghostscript-produced PDFs, extracts text with layout, and has no system-level dependencies.

Use `uv add` so `uv.lock` and `pyproject.toml` stay consistent. Record the parser name and version in `docs/architecture.md` under a new "Corpus" section (see Stage 3 below).

### 3. Write the extraction script

Create a single extraction script that reads both PDFs and writes the two JSONL files. Place it wherever the implementer finds natural — a top-level `scripts/` directory, a module under `src/building_with_rag/`, or a standalone file. It must:

- Read each PDF exactly once per run.
- Extract every section as a discrete record.
- Handle repeated page headers (e.g., "BHARATIYA NYAYA SANHITA, 2023 (BNS)" appearing at the top of many pages). These must not appear in section `text`.
- Handle multi-page sections — concatenate text across page boundaries when a section continues onto the next page.
- Handle spacing and line-break artifacts from PDF extraction conservatively (collapse within-paragraph breaks, preserve paragraph boundaries).
- Detect and skip the BNS index and correspondence table — these are not sections and must not produce records.
- Keep BNS and IPC records in separate output files. Do not merge them even where section numbers happen to match.
- Detect obvious duplicate extraction (same section extracted twice from the same page range) and skip the duplicate.
- Flag uncertain or low-quality content by setting `needs_review: true` rather than silently guessing. Examples of uncertainty: text that appears to be a scanned image with no extractable characters, garbled glyphs, or sections where the parser cannot confidently separate heading from body.
- Record the parser name, parser version, and a `source_status_version` of `"v1"` on every record.

### 4. JSONL record schema

Every record must have these fields, in this order, top-level in the JSON object:

| Field | Type | Description |
|---|---|---|
| `section_id` | string | Act-prefixed identifier, e.g. `"bns:1"`, `"ipc:21"` |
| `act` | string | `"BNS_2023"` or `"IPC_1860"` |
| `act_label` | string | `"Bharatiya Nyaya Sanhita, 2023"` or `"Indian Penal Code, 1860"` |
| `status` | string | `"in_force"` for BNS, `"repealed"` for IPC |
| `chapter` | string | Chapter number as it appears (e.g. `"I"`, `"II"`, `"VA"`) |
| `chapter_title` | string | Chapter heading text (e.g. `"PRELIMINARY"`, `"OF PUNISHMENTS"`) |
| `section_number` | integer | The bare section number (e.g. `1`, `21`) |
| `heading` | string | The section's heading/marginal note (e.g. `"Short title, commencement and application."`) |
| `text` | string | Full body text of the section, without the heading repeated |
| `source_pdf` | string | Relative path to the source PDF, e.g. `"data/raw/BNS_2023_bare_act.pdf"` |
| `source_sha256` | string | SHA-256 of the source PDF at extraction time |
| `parser` | string | Parser library name, e.g. `"pymupdf"` |
| `parser_version` | string | Parser library version, e.g. `"1.25.0"` |
| `source_status_version` | string | `"v1"` for the initial extraction |
| `needs_review` | boolean | `true` if the extractor is uncertain about this record's quality |

The `section_id` format is `act_prefix:section_number`. The act prefix is `bns` for BNS and `ipc` for IPC — lowercase, no year. This matches the act-qualified identifier convention in `docs/architecture.md`.

Later chunking will create `chunk_id` values that include the parent `section_id`, so the section-to-chunk relationship is always traceable. Chunk records will carry `act`, `heading`, and `source_pdf` forward so retrieval can surface source information without a join. That work belongs to a later story; this story only produces the section-level corpus.

### 5. Output location

Write the corpus to `data/processed/`. Create the directory if it does not exist. Do not overwrite an existing valid corpus unless source hashes have changed.

- `data/processed/bns_sections.jsonl` — one record per BNS section
- `data/processed/ipc_sections.jsonl` — one record per IPC section

### 6. Safe re-run behaviour

- If both output files already exist and both source PDF SHAs match the hashes stored in the existing corpus, do nothing (skip extraction). Print a message and exit cleanly.
- If either source hash has changed, regenerate _both_ files for that act as a single replacement (write to temp file, verify, rename over old file). Never mix records from different PDF versions in one corpus file.
- Re-running must never append duplicate records. Either replace the file atomically or skip it.
- If only one source hash changed, regenerate only that act's corpus. Leave the other act's file untouched.

### 7. Update architecture document

Add a new **Corpus** section to `docs/architecture.md`. Record:

- The parser library, its version, and why it was chosen.
- The extraction command (e.g. `uv run python scripts/extract_sections.py`).
- Output format: JSONL, one record per section, field descriptions.
- Output location: `data/processed/`.
- Known limitations: any sections flagged `needs_review`, IPC footnote handling strategy, BNS chapter-marker conventions, any sections that could not be reliably extracted.
- The source-hash safety rule: if hashes change, regenerate as a replacement, never mix versions.

### 8. Later-chunking contract

Document in the story (not architecture.md) that:

- Chunking (a later story) will split `text` into chunks while copying `section_id`, `act`, `heading`, and `source_pdf` onto each chunk record.
- One section may produce zero or more chunks depending on length.
- The `section_id` on a chunk always points back to the record in this story's corpus.
- This story's corpus is the canonical section-level source; chunks are derived from it, not from the raw PDFs.

## Completion checks (lightweight)

1. Both JSONL files exist at `data/processed/` and contain the expected number of records (BSN ~354 sections across 20 chapters; IPC ~511 sections across 23 chapters — exact counts depend on parser accuracy and must be reported, not asserted).
2. Inspect three representative records from each act against the PDF source:
   - A short section that fits on one page.
   - A section that spans pages (pick one visible from the PDF arrangement).
   - The first and last section of each act.
3. Verify every record has all 14 required fields and no extras at the top level.
4. Verify no record has empty `text` (whitespace-only counts as empty — flag it `needs_review`).
5. Verify `section_id` values are unique within each file.
6. Run the extraction a second time — it must skip both files cleanly (no changes, no appended duplicates).
7. `uv run ruff check` passes on the extraction script.

Do not require: a comprehensive validation framework, a reference-difference report against a gold corpus, broad security tests, synthetic permission records, or integration with the FastAPI application.

## Handover

At the end, the story records:

- Files created (list).
- Parser and version used.
- Record counts per act.
- Count of records flagged `needs_review` and why.
- Re-run behaviour confirmed.
- The `uv add` command used to install the PDF dependency.
- The extraction command.

Story report format (per project instructions): `Completed.`, changed paths, test results — under 7 lines, ending with the command for the full test suite if wanted, for example `uv run pytest`.