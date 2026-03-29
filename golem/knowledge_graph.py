"""A-Mem knowledge graph: topic-indexed pitfall retrieval.

Indexes instincts from the InstinctStore by extracted keywords and
file patterns, enabling selective injection of only relevant
knowledge into agent prompts.

Key exports:
- ``KnowledgeGraph`` — builds and queries a keyword-indexed graph.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .instinct_store import InstinctStore

logger = logging.getLogger("golem.knowledge_graph")

# Common stop words to exclude from keyword extraction
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "not",
        "no",
        "nor",
        "and",
        "but",
        "or",
        "if",
        "when",
        "while",
    }
)

# Pattern for extracting file-like references (e.g. golem/verifier.py, config.yaml)
_FILE_PATTERN = re.compile(r"\b[\w/]+\.\w{1,4}\b")
# Pattern for extracting Python identifiers (snake_case, camelCase-ish)
_IDENTIFIER_PATTERN = re.compile(r"\b[a-z_][a-z0-9_]{2,}\b")


@dataclass
class GraphNode:
    """A node in the knowledge graph representing one instinct."""

    instinct_id: str
    text: str
    category: str
    confidence: float
    keywords: set = field(default_factory=set)
    file_refs: set = field(default_factory=set)


class KnowledgeGraph:
    """Topic-indexed knowledge graph for selective pitfall injection.

    Indexes active instincts by keywords and file references so that
    only contextually relevant pitfalls are injected into agent prompts,
    avoiding wasted context budget on unrelated knowledge.
    """

    def __init__(self, store: InstinctStore) -> None:
        self._store = store
        self._nodes: dict[str, GraphNode] = {}
        self._keyword_index: dict[str, set] = {}  # keyword → node IDs
        self._file_index: dict[str, set] = {}  # file ref → node IDs

    # -- Graph construction ---------------------------------------------------

    def build(self) -> None:
        """Build the graph from the instinct store.

        Loads all active instincts, extracts keywords and file references,
        and populates the keyword and file indexes.
        """
        instincts = self._store._load()  # pylint: disable=protected-access
        self._nodes.clear()
        self._keyword_index.clear()
        self._file_index.clear()

        for inst in instincts:
            if inst.archived:
                continue

            node = GraphNode(
                instinct_id=inst.id,
                text=inst.text,
                category=inst.category,
                confidence=inst.confidence,
            )

            # Extract bag-of-words keywords (length > 2, not stop words)
            words = set(inst.text.lower().split())
            keywords = {w for w in words if len(w) > 2 and w not in _STOP_WORDS}
            # Also extract identifiers (function names, variable names, etc.)
            # Identifiers are also filtered against stop words
            identifiers = {
                tok
                for tok in _IDENTIFIER_PATTERN.findall(inst.text.lower())
                if tok not in _STOP_WORDS
            }
            node.keywords = keywords | identifiers

            # Extract file references (e.g. golem/verifier.py, config.yaml)
            file_refs = set(_FILE_PATTERN.findall(inst.text))
            node.file_refs = file_refs

            self._nodes[inst.id] = node

            # Build keyword index
            for kw in node.keywords:
                self._keyword_index.setdefault(kw, set()).add(inst.id)

            # Build file index
            for fref in node.file_refs:
                self._file_index.setdefault(fref, set()).add(inst.id)

    # -- Querying -------------------------------------------------------------

    def query(
        self,
        subject: str = "",
        files: list | None = None,
        *,
        max_results: int = 10,
        min_confidence: float = 0.3,
    ) -> list:
        """Query relevant knowledge for a task.

        Scores each node by keyword overlap with the subject plus file
        reference matches.  Returns the top-N nodes above ``min_confidence``,
        sorted by relevance score descending.

        Args:
            subject: Free-form task description to match against.
            files: List of file paths involved in the task.
            max_results: Maximum number of nodes to return.
            min_confidence: Exclude nodes below this confidence threshold.

        Returns:
            List of ``GraphNode`` objects ordered by relevance.
        """
        if not self._nodes:
            self.build()

        files = files or []
        subject_words = set(subject.lower().split()) - _STOP_WORDS
        subject_ids = {
            tok
            for tok in _IDENTIFIER_PATTERN.findall(subject.lower())
            if tok not in _STOP_WORDS
        }
        query_keywords = subject_words | subject_ids

        scores: dict[str, float] = {}

        for node_id, node in self._nodes.items():
            if node.confidence < min_confidence:
                continue

            score = 0.0

            # Keyword overlap between query and node
            overlap = query_keywords & node.keywords
            if overlap:
                score += len(overlap) * 1.0

            # File reference matches — strong signal
            for f in files:
                if f in node.file_refs:
                    score += 3.0
                # Also check basename match
                basename = Path(f).name
                if basename in node.file_refs:
                    score += 2.0

            # Confidence-weighted boost
            score *= 0.5 + node.confidence

            if score > 0:
                scores[node_id] = score

        # Sort by score descending, take top N
        top_ids = sorted(scores, key=scores.__getitem__, reverse=True)[:max_results]
        return [self._nodes[nid] for nid in top_ids]

    def query_for_context(
        self,
        subject: str = "",
        files: list | None = None,
        max_results: int = 10,
    ) -> str:
        """Query and format relevant knowledge for prompt injection.

        Returns a markdown-formatted section of relevant pitfalls, or an
        empty string if no relevant knowledge is found.
        """
        nodes = self.query(subject, files, max_results=max_results)
        if not nodes:
            return ""

        lines = ["## Relevant Knowledge\n"]
        lines.append("The following learned pitfalls are relevant to this task:\n")

        for node in nodes:
            lines.append("- **[%s]** %s" % (node.category, node.text))

        return "\n".join(lines)

    # -- Properties -----------------------------------------------------------

    @property
    def node_count(self) -> int:
        """Number of nodes currently in the graph."""
        return len(self._nodes)

    @property
    def keyword_count(self) -> int:
        """Number of unique keywords in the keyword index."""
        return len(self._keyword_index)
