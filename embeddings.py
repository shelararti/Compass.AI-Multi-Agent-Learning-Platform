"""
Thin wrapper around the local Ollama embeddings endpoint, mirroring
llm_client.py's approach for chat completions.

Kept separate from llm_client because embeddings use a different model
(a small dedicated embedding model, e.g. nomic-embed-text) and a
different Ollama endpoint than chat completions.
"""

import json
import urllib.request
import urllib.error

from . import config


def embed_one(text: str, timeout: int = config.LLM_TIMEOUT_SECONDS) -> list:
    """Return the embedding vector for a single piece of text."""
    payload = {"model": config.EMBED_MODEL_NAME, "prompt": text}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        config.OLLAMA_EMBED_URL, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach Ollama embeddings at {config.OLLAMA_EMBED_URL}. "
            f"Is the '{config.EMBED_MODEL_NAME}' model pulled? "
            f"(ollama pull {config.EMBED_MODEL_NAME}) Details: {e}"
        )
    embedding = body.get("embedding")
    if not embedding:
        raise RuntimeError(
            f"Empty embedding response from Ollama for model '{config.EMBED_MODEL_NAME}'."
        )
    return embedding


def embed_texts(texts: list) -> list:
    """Embed a batch of texts. Ollama's /api/embeddings endpoint is
    single-text-per-call, so this loops -- fine for the chunk counts a
    single course PDF produces."""
    return [embed_one(t) for t in texts]
