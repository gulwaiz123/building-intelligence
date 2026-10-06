"""One shared registry of course modes and model IDs."""

MODES: dict[str, str] = {
    "semantic": "rag-semantic",
    "hybrid": "rag-hybrid",
    "hybrid-reranked": "rag-hybrid-reranked",
    "structured": "rag-structured",
    "decomposition": "rag-decomposition",
    "hyde": "rag-hyde",
}

MODEL_TO_MODE: dict[str, str] = {model: mode for mode, model in MODES.items()}
