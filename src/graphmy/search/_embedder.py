"""
graphmy/search/_embedder.py
============================
Lazy-loading wrapper around the sentence-transformers embedding model.

We use ``flax-sentence-embeddings/st-codesearch-distilroberta-base``, which
was fine-tuned on CodeSearchNet to map natural-language queries to code
snippets. It is the best CPU-friendly model for this task:
  - 768-dimensional embeddings
  - ~329 MB download (cached by HuggingFace after first use)
  - Cold-start ~4–8 s on CPU, then fast

"Lazy loading" means the model is NOT imported or loaded at import time.
The first call to ``embed()`` or ``embed_many()`` triggers the download
and load. This keeps CLI startup instant — the model is only loaded when
a natural-language query is actually issued.

Why a wrapper class instead of calling SentenceTransformer directly?
  - One shared model instance across the process lifetime
  - Centralised progress reporting during first load
  - Easy to swap the model in tests (subclass and override ``_load``)
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only imported for type annotations — real import deferred to _load().
    from sentence_transformers import SentenceTransformer

# The model we use for all code-search embeddings.
# This string is also stored in GraphmyConfig.embedding_model so advanced
# users can override it via config.toml if they want a different model.
DEFAULT_MODEL_NAME = "flax-sentence-embeddings/st-codesearch-distilroberta-base"

# Dimensionality of the embeddings produced by DEFAULT_MODEL_NAME.
# Used by the vector store to pre-configure the collection.
EMBEDDING_DIM = 768


class Embedder:
    """
    Lazy-loading sentence-transformers model wrapper.

    Parameters
    ----------
    model_name : str
        HuggingFace model identifier. Defaults to the CodeSearchNet model.
        Change this only if you need a different embedding space — the default
        is already fine-tuned for code search.

    Usage
    -----
    >>> embedder = Embedder()
    >>> vec = embedder.embed("find all authentication functions")
    >>> # vec is a list[float] of length EMBEDDING_DIM (768)
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        self.model_name = model_name
        # The actual SentenceTransformer instance — None until first use.
        self._model: SentenceTransformer | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """
        Embed a single text string into a float vector.

        Triggers model load on first call (may take 4–8 s on CPU).

        Parameters
        ----------
        text : str
            The natural-language query or code snippet to embed.

        Returns
        -------
        list[float]
            A 768-element float list (for the default model).
        """
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a batch of text strings.

        Batching is significantly faster than repeated calls to ``embed()``
        because sentence-transformers pads and processes texts in parallel.

        Parameters
        ----------
        texts : list[str]
            The texts to embed. May be NL queries, code signatures, or docstrings.

        Returns
        -------
        list[list[float]]
            One 768-element float list per input text, in the same order.
        """
        model = self._get_model()

        # encode() returns a numpy array of shape (N, dim).
        # .tolist() converts to a plain Python list[list[float]] which is
        # JSON-serialisable and accepted by chromadb.
        vectors = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        return vectors.tolist()  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def _get_model(self) -> SentenceTransformer:
        """
        Return the loaded model, loading it on first call.

        Prints a one-time notice to stderr so users understand why the first
        query is slow (model download + load).
        """
        if self._model is None:
            self._model = self._load()
        return self._model

    def _load(self) -> SentenceTransformer:
        """
        Actually import sentence-transformers and load the model.

        Deferred to here so `import graphmy` does not trigger a heavyweight
        import chain at CLI startup.
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for natural-language queries. "
                "Install graphmy normally — it is a core dependency."
            ) from exc

        print(
            f"  [graphmy] Loading embedding model '{self.model_name}' "
            f"(first-time download may take a minute)...",
            file=sys.stderr,
        )

        model = SentenceTransformer(self.model_name)

        print("  [graphmy] Model ready.", file=sys.stderr)
        return model

    def is_loaded(self) -> bool:
        """True if the model has already been loaded into memory."""
        return self._model is not None
