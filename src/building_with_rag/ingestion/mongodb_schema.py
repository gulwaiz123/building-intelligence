"""MongoDB schema for the four-collection RAG document model.

Defines collection names, validators, deterministic ID generators, document
builders, and ordinary (non-vector) indexes. No embedding logic here.
"""

import hashlib
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Collection names
# ---------------------------------------------------------------------------

SOURCES_COLLECTION = "sources"
SECTIONS_COLLECTION = "sections"
CHUNKS_COLLECTION = "chunks"
EMBEDDINGS_COLLECTION = "embeddings"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_ACTS = frozenset({"BNS_2023", "IPC_1860"})
VALID_STATUSES = frozenset({"in_force", "repealed"})
DEFAULT_ACCESS_LEVEL = "public"
CHUNKING_VERSION = "v1"
EMBEDDING_MODEL = "voyage-3.5"
EMBEDDING_MODEL_VERSION = "voyage-3.5"
EMBEDDING_DIMENSIONS = 1024
CHUNK_SIZE = 2048
CHUNK_OVERLAP = 256
CHUNK_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
SOURCE_STATUS_VERSION = "v1"

# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_act(value: str) -> str:
    """Raise ValueError if *value* is not a known act code."""
    if value not in VALID_ACTS:
        raise ValueError(f"Invalid act: {value!r}. Must be one of {sorted(VALID_ACTS)}")
    return value


def validate_status(value: str) -> str:
    """Raise ValueError if *value* is not a known status."""
    if value not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {value!r}. Must be one of {sorted(VALID_STATUSES)}")
    return value


def validate_access_level(value: str) -> str:
    """Raise ValueError if *value* is not a known access level."""
    if value != DEFAULT_ACCESS_LEVEL:
        raise ValueError(
            f"Invalid access_level: {value!r}. Only {DEFAULT_ACCESS_LEVEL!r} is supported."
        )
    return value


def ensure_deterministic_id(*parts: str) -> str:
    """Return a hex digest from SHA-256 of the joined *parts*."""
    joined = ":".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def validate_section_id(value: str) -> str:
    """Raise ValueError if *value* does not match ``bns:<int>`` or ``ipc:<int>``."""
    if not isinstance(value, str) or ":" not in value:
        raise ValueError(f"Invalid section_id: {value!r}")
    prefix, _, number = value.partition(":")
    if prefix not in ("bns", "ipc"):
        raise ValueError(f"Invalid section_id prefix: {value!r}")
    try:
        int(number)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid section_id number: {value!r}") from None
    return value


def now_utc() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)

# ---------------------------------------------------------------------------
# Deterministic ID helpers
# ---------------------------------------------------------------------------


def build_chunk_id(section_id: str, chunk_index: int, chunking_version: str = CHUNKING_VERSION) -> str:
    """Deterministic ``chunk_id`` — truncate sha256 hex to 24 chars."""
    full = ensure_deterministic_id(section_id, str(chunk_index), chunking_version)
    return full[:24]


def build_embedding_id(chunk_id: str, model: str = EMBEDDING_MODEL,
                       model_version: str = EMBEDDING_MODEL_VERSION,
                       dimensions: int = EMBEDDING_DIMENSIONS) -> str:
    """Deterministic ``embedding_id`` — truncate sha256 hex to 24 chars."""
    full = ensure_deterministic_id(f"emb:{chunk_id}", model, model_version, str(dimensions))
    return full[:24]

# ---------------------------------------------------------------------------
# Document builders — sources
# ---------------------------------------------------------------------------


def build_source_doc(*, act: str, act_label: str, status: str, source_pdf: str,
                     source_sha256: str, parser: str, parser_version: str,
                     corpus_path: str, section_count: int) -> dict:
    """Return a ``sources`` collection document ready for upsert."""
    validate_act(act)
    validate_status(status)
    source_id = f"{act.split('_')[0].lower()}_source"  # "bns_source" / "ipc_source"
    ts = now_utc()
    return {
        "_id": source_id,
        "source_pdf": source_pdf,
        "source_sha256": source_sha256,
        "act": act,
        "act_label": act_label,
        "status": status,
        "parser": parser,
        "parser_version": parser_version,
        "corpus_path": corpus_path,
        "section_count": section_count,
        "created_at": ts,
        "updated_at": ts,
    }


def build_section_doc(record: dict) -> dict:
    """Return a ``sections`` collection document from one corpus JSONL record."""
    section_id = record["section_id"]
    validate_section_id(section_id)
    validate_act(record["act"])
    validate_status(record["status"])
    ts = now_utc()
    act_prefix = section_id.split(":")[0].lower()
    return {
        "_id": section_id,
        "section_id": section_id,
        "act": record["act"],
        "act_label": record["act_label"],
        "status": record["status"],
        "chapter": record["chapter"],
        "chapter_title": record["chapter_title"],
        "section_number": record["section_number"],
        "heading": record["heading"],
        "text": record["text"],
        "source_id": f"{act_prefix}_source",
        "source_pdf": record["source_pdf"],
        "source_sha256": record["source_sha256"],
        "parser": record["parser"],
        "parser_version": record["parser_version"],
        "source_status_version": record.get("source_status_version", SOURCE_STATUS_VERSION),
        "needs_review": record["needs_review"],
        "access_level": DEFAULT_ACCESS_LEVEL,
        "provenance": {
            "corpus_file": f"data/processed/{act_prefix}_sections.jsonl",
            "record_index": record.get("_record_index", -1),
        },
        "created_at": ts,
        "updated_at": ts,
    }


def build_chunk_doc(*, section: dict, chunk_index: int, text: str,
                    embedding_input: str) -> dict:
    """Return a ``chunks`` collection document."""
    section_id = section["_id"]
    ts = now_utc()
    chunk_id = build_chunk_id(section_id, chunk_index)
    return {
        "_id": chunk_id,
        "chunk_id": chunk_id,
        "section_id": section_id,
        "chunk_index": chunk_index,
        "chunking_version": CHUNKING_VERSION,
        "text": text,
        "embedding_input": embedding_input,
        "act": section["act"],
        "chapter": section["chapter"],
        "status": section["status"],
        "access_level": DEFAULT_ACCESS_LEVEL,
        "created_at": ts,
    }


def build_embedding_doc(*, chunk: dict, vector: list[float]) -> dict:
    """Return an ``embeddings`` collection document."""
    chunk_id = chunk["_id"]
    embedding_id = build_embedding_id(chunk_id)
    ts = now_utc()
    return {
        "_id": embedding_id,
        "embedding_id": embedding_id,
        "chunk_id": chunk_id,
        "model": EMBEDDING_MODEL,
        "model_version": EMBEDDING_MODEL_VERSION,
        "dimensions": EMBEDDING_DIMENSIONS,
        "vector": vector,
        "act": chunk["act"],
        "chapter": chunk["chapter"],
        "status": chunk["status"],
        "access_level": DEFAULT_ACCESS_LEVEL,
        "created_at": ts,
    }

# ---------------------------------------------------------------------------
# Index definitions (ordinary / non-vector)
# ---------------------------------------------------------------------------


def ensure_ordinary_indexes(db):
    """Create ordinary (non-vector) indexes on all four collections.

    Idempotent — existing matching indexes are skipped by MongoDB.
    """
    # --- sources ---
    db[SOURCES_COLLECTION].create_index("act", unique=True, name="idx_sources_act_unique")

    # --- sections ---
    db[SECTIONS_COLLECTION].create_index("section_id", unique=True,
                                         name="idx_sections_section_id_unique")
    db[SECTIONS_COLLECTION].create_index(
        [("act", 1), ("section_number", 1)],
        unique=True,
        name="idx_sections_act_section_number_unique",
    )
    db[SECTIONS_COLLECTION].create_index("access_level", name="idx_sections_access_level")
    db[SECTIONS_COLLECTION].create_index("source_id", name="idx_sections_source_id")

    # --- chunks ---
    db[CHUNKS_COLLECTION].create_index("chunk_id", unique=True, name="idx_chunks_chunk_id_unique")
    db[CHUNKS_COLLECTION].create_index("section_id", name="idx_chunks_section_id")
    db[CHUNKS_COLLECTION].create_index(
        [("act", 1), ("chunk_index", 1)], name="idx_chunks_act_chunk_index"
    )
    db[CHUNKS_COLLECTION].create_index("access_level", name="idx_chunks_access_level")

    # --- embeddings ---
    db[EMBEDDINGS_COLLECTION].create_index("embedding_id", unique=True,
                                           name="idx_embeddings_embedding_id_unique")
    db[EMBEDDINGS_COLLECTION].create_index("chunk_id", unique=True,
                                           name="idx_embeddings_chunk_id_unique")
    db[EMBEDDINGS_COLLECTION].create_index(
        [("act", 1), ("access_level", 1)], name="idx_embeddings_act_access_level"
    )

# ---------------------------------------------------------------------------
# Vector search index definition
# ---------------------------------------------------------------------------


VECTOR_INDEX_NAME = "vector_index"

VECTOR_INDEX_DEFINITION = {
    "fields": [
        {"type": "vector", "path": "vector", "numDimensions": 1024, "similarity": "cosine"},
        {"type": "filter", "path": "act"},
        {"type": "filter", "path": "chapter"},
        {"type": "filter", "path": "status"},
        {"type": "filter", "path": "access_level"},
    ]
}
# ---------------------------------------------------------------------------
# Atlas Search (keyword) index on chunks.text, used by hybrid retrieval
# ---------------------------------------------------------------------------

KEYWORD_INDEX_NAME = "chunk_text_index"

KEYWORD_INDEX_DEFINITION = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "text": {"type": "string", "analyzer": "lucene.standard"},
            "act": {"type": "token"},
            "status": {"type": "token"},
            "access_level": {"type": "token"},
        },
    }
}
