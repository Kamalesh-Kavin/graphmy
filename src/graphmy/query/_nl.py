"""
graphmy/query/_nl.py
=====================
Natural-language query engine: vector search + graph expansion + optional LLM synthesis.

Query pipeline:
  1. ``embed``   — embed the NL query with the same model used to embed symbols
  2. ``search``  — find top-K nearest neighbours in the VectorStore
  3. ``expand``  — for each hit, pull callers + callees from the GraphStore
                   (so related symbols float to the surface automatically)
  4. ``explain`` — (optional, requires OpenAI key) send hits to GPT with the
                   user's query and ask for a natural-language synthesis

The expand step is the "knowledge graph advantage" — pure vector search would
miss that callee B is highly relevant because caller A scored highly.

Result ranking:
  - Primary sort: vector distance (lower = more similar)
  - Secondary: graph distance from the top hit (1-hop expansion nodes ranked last)
  - Duplicates removed (a node can appear as both a direct hit and an expansion)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from graphmy.graph._model import SymbolNode
from graphmy.graph._store import GraphStore
from graphmy.search._embedder import Embedder
from graphmy.search._vector_store import VectorStore


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class NLQueryResult:
    """
    The result of a natural-language query.

    Fields
    ------
    query : str
        The original NL query string.
    hits : list[NLHit]
        Ranked list of matching symbols with metadata.
    explanation : str
        LLM-synthesized explanation (empty string if --explain not used or
        no API key is configured).
    """

    query: str
    hits: list["NLHit"] = field(default_factory=list)
    explanation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "hits": [h.as_dict() for h in self.hits],
            "explanation": self.explanation,
        }


@dataclass
class NLHit:
    """
    A single result from a natural-language query.

    Fields
    ------
    node : SymbolNode
        The matching symbol.
    distance : float
        Cosine distance from the query embedding (lower = more similar).
        Set to 1.0 for expansion nodes (they were added by graph traversal,
        not by vector search).
    is_expansion : bool
        True if this node was added by the graph-expansion step (not a direct
        vector-search hit). Expansions are shown after direct hits in the UI.
    callers : list[SymbolNode]
        Direct callers of this symbol (1-hop, included for context).
    callees : list[SymbolNode]
        Direct callees of this symbol (1-hop, included for context).
    """

    node: SymbolNode
    distance: float = 1.0
    is_expansion: bool = False
    callers: list[SymbolNode] = field(default_factory=list)
    callees: list[SymbolNode] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node.to_dict(),
            "distance": self.distance,
            "is_expansion": self.is_expansion,
            "callers": [n.to_dict() for n in self.callers],
            "callees": [n.to_dict() for n in self.callees],
        }


# ---------------------------------------------------------------------------
# NL Query engine
# ---------------------------------------------------------------------------


class NLQuery:
    """
    Natural-language query engine.

    Parameters
    ----------
    graph : GraphStore
        The knowledge graph to search.
    vector_store : VectorStore
        The vector store containing symbol embeddings.
    embedder : Embedder | None
        The embedding model. If None, a default Embedder is created. Pass the
        same Embedder instance used to build the VectorStore so the model is
        only loaded once.
    openai_api_key : str | None
        If set, enables the ``--explain`` synthesis mode via OpenAI.
    openai_model : str
        OpenAI model name (default: "gpt-4o-mini").

    Usage
    -----
    >>> nlq = NLQuery(graph, vector_store)
    >>> result = nlq.run("find all authentication functions", limit=10)
    >>> result_with_explain = nlq.run("...", limit=10, explain=True)
    """

    def __init__(
        self,
        graph: GraphStore,
        vector_store: VectorStore,
        embedder: Embedder | None = None,
        openai_api_key: str | None = None,
        openai_model: str = "gpt-4o-mini",
    ) -> None:
        self.graph = graph
        self.vector_store = vector_store
        self.embedder = embedder or Embedder()
        self.openai_api_key = openai_api_key
        self.openai_model = openai_model

    def run(
        self,
        query: str,
        limit: int = 10,
        explain: bool = False,
    ) -> NLQueryResult:
        """
        Execute a natural-language query.

        Parameters
        ----------
        query : str
            The natural-language search string.
        limit : int
            Maximum number of hits to return (including expansion nodes).
        explain : bool
            If True and an OpenAI API key is configured, synthesize an
            explanation of the results using GPT.

        Returns
        -------
        NLQueryResult
            Ranked hits with optional LLM explanation.
        """
        # Step 1: vector search — find the top-K nearest symbols.
        raw_hits = self.vector_store.query(query, n_results=max(limit, 20))

        # Step 2: build NLHit objects for direct vector-search hits.
        seen_ids: set[str] = set()
        hits: list[NLHit] = []

        for raw in raw_hits:
            node_id = raw["node_id"]
            node = self.graph.get_node(node_id)
            if node is None or node_id in seen_ids:
                continue
            seen_ids.add(node_id)
            hits.append(
                NLHit(
                    node=node,
                    distance=raw["distance"],
                    is_expansion=False,
                    callers=self.graph.callers(node_id),
                    callees=self.graph.callees(node_id),
                )
            )

        # Step 3: graph expansion — add 1-hop callers and callees of top hits.
        # We only expand the top-3 direct hits to avoid flooding the results.
        expansion_candidates: dict[str, SymbolNode] = {}
        for hit in hits[:3]:
            for neighbour in hit.callers + hit.callees:
                if neighbour.node_id not in seen_ids:
                    expansion_candidates[neighbour.node_id] = neighbour

        for nid, node in expansion_candidates.items():
            seen_ids.add(nid)
            hits.append(
                NLHit(
                    node=node,
                    distance=1.0,  # expansion nodes get max distance (lowest priority)
                    is_expansion=True,
                    callers=self.graph.callers(nid),
                    callees=self.graph.callees(nid),
                )
            )

        # Step 4: sort by distance (direct hits first, expansions last).
        hits.sort(key=lambda h: (h.is_expansion, h.distance))

        # Step 5: cap to requested limit.
        hits = hits[:limit]

        # Step 6: optional LLM synthesis.
        explanation = ""
        if explain:
            if self.openai_api_key:
                explanation = self._synthesize(query, hits)
            else:
                explanation = (
                    "OpenAI API key not configured. "
                    "Set GRAPHMY_OPENAI_API_KEY or add it to .graphmy/config.toml."
                )

        return NLQueryResult(query=query, hits=hits, explanation=explanation)

    # ------------------------------------------------------------------
    # LLM synthesis
    # ------------------------------------------------------------------

    def _synthesize(self, query: str, hits: list[NLHit]) -> str:
        """
        Send the top hits to OpenAI and ask for a natural-language explanation.

        We send the name, signature, docstring, and file:line of the top-5
        direct hits (not expansion nodes) to keep the prompt concise.

        Parameters
        ----------
        query : str
            The original user query.
        hits : list[NLHit]
            Ranked hits (already capped to the requested limit).

        Returns
        -------
        str
            A paragraph or two of LLM-synthesized explanation.
        """
        try:
            from openai import OpenAI
        except ImportError:
            return "openai package not installed. Run: pip install 'graphmy[openai]'"

        # Build a compact summary of the top-5 direct hits for the prompt.
        direct_hits = [h for h in hits if not h.is_expansion][:5]
        symbol_summaries: list[str] = []
        for i, h in enumerate(direct_hits, 1):
            n = h.node
            loc = f"{n.file}:{n.line}" if n.line else n.file
            doc = f"\n  Docs: {n.docstring}" if n.docstring else ""
            sig = f"\n  Sig:  {n.signature}" if n.signature else ""
            symbol_summaries.append(f"{i}. {n.kind.value} `{n.name}` ({loc}){sig}{doc}")

        symbols_text = (
            "\n".join(symbol_summaries) if symbol_summaries else "No matching symbols found."
        )

        system_prompt = (
            "You are a code navigator assistant. "
            "Given a developer's question and a list of relevant symbols from the codebase, "
            "provide a concise explanation of how those symbols relate to the question. "
            "Be direct and technical. Mention file locations. Do not repeat the question."
        )

        user_prompt = (
            f"Developer question: {query}\n\n"
            f"Relevant symbols found in the codebase:\n{symbols_text}\n\n"
            f"Please explain how these symbols answer the question."
        )

        client = OpenAI(api_key=self.openai_api_key)
        response = client.chat.completions.create(
            model=self.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=512,
            temperature=0.2,
        )

        return response.choices[0].message.content or ""
