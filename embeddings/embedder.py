import os
import threading
from typing import Optional

from utils.logger import get_logger

logger = get_logger("embeddings.embedder")

_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
_model = None
_load_failed = False           # cache failure so we don't retry the heavy load every call
_lock = threading.Lock()       # serialise loading so the prewarm thread and request
                               # threads can't both construct the model at once


def _get_model():
    global _model, _load_failed
    # Fast path — already loaded, or already known unavailable.
    if _model is not None:
        return _model
    if _load_failed:
        return None
    with _lock:
        # Re-check inside the lock: another thread may have finished loading
        # (or failed) while we were waiting.
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(_MODEL_NAME)
            logger.info(f"Embedding model loaded: {_MODEL_NAME}")
        except Exception as e:
            logger.warning(f"Embedding model unavailable ({e}) — embeddings disabled")
            _load_failed = True
            _model = None
    return _model


def encode(text: str) -> Optional[list[float]]:
    """Return a 384-dim embedding as a plain Python list, or None if unavailable."""
    model = _get_model()
    if model is None:
        return None
    try:
        vector = model.encode(text, convert_to_numpy=True)
        return vector.tolist()
    except Exception as e:
        logger.error(f"Encode failed: {e}")
        return None
