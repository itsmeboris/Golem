"""Tests for golem.knowledge_graph — KnowledgeGraph and GraphNode."""

from pathlib import Path

import pytest

from golem.instinct_store import InstinctStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> InstinctStore:
    """Create an InstinctStore backed by a temp file."""
    return InstinctStore(tmp_path / "instincts.json")


def _add_instinct(store, text, category="antipatterns", confidence=0.6):
    """Add an instinct and return it."""
    return store.add(text, category, initial_confidence=confidence)


# ---------------------------------------------------------------------------
# GraphNode dataclass
# ---------------------------------------------------------------------------


class TestGraphNode:
    def test_node_has_expected_fields(self):
        """GraphNode can be constructed with all fields."""
        from golem.knowledge_graph import GraphNode

        node = GraphNode(
            instinct_id="abc",
            text="avoid import cycles",
            category="architecture",
            confidence=0.7,
        )
        assert node.instinct_id == "abc"
        assert node.text == "avoid import cycles"
        assert node.category == "architecture"
        assert node.confidence == 0.7
        assert node.keywords == set()
        assert node.file_refs == set()

    def test_node_keywords_default_empty_set(self):
        """keywords field defaults to an empty set (not a shared mutable)."""
        from golem.knowledge_graph import GraphNode

        n1 = GraphNode(instinct_id="a", text="x", category="c", confidence=0.5)
        n2 = GraphNode(instinct_id="b", text="y", category="c", confidence=0.5)
        n1.keywords.add("foo")
        assert "foo" not in n2.keywords

    def test_node_file_refs_default_empty_set(self):
        """file_refs field defaults to an empty set (not shared)."""
        from golem.knowledge_graph import GraphNode

        n1 = GraphNode(instinct_id="a", text="x", category="c", confidence=0.5)
        n2 = GraphNode(instinct_id="b", text="y", category="c", confidence=0.5)
        n1.file_refs.add("foo.py")
        assert "foo.py" not in n2.file_refs


# ---------------------------------------------------------------------------
# KnowledgeGraph.build()
# ---------------------------------------------------------------------------


class TestBuild:
    def test_build_populates_nodes(self, tmp_path):
        """build() creates nodes for every active instinct."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "always run tests before shipping")
        _add_instinct(store, "avoid circular imports in modules")

        kg = KnowledgeGraph(store)
        kg.build()

        assert kg.node_count == 2

    def test_build_skips_archived(self, tmp_path):
        """build() excludes archived instincts."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        active = _add_instinct(store, "always run tests")
        archived = _add_instinct(store, "old advice to skip")

        # Archive the second one via contradict (drop confidence below 0.2)
        for _ in range(5):
            store.contradict(archived.id)

        kg = KnowledgeGraph(store)
        kg.build()

        assert kg.node_count == 1
        node_ids = list(kg._nodes.keys())
        assert active.id in node_ids

    def test_build_clears_previous_state(self, tmp_path):
        """Calling build() twice rebuilds from scratch (no stale data)."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "first pitfall text")
        kg = KnowledgeGraph(store)
        kg.build()
        assert kg.node_count == 1

        _add_instinct(store, "second pitfall text")
        kg.build()
        assert kg.node_count == 2

    def test_build_extracts_keywords(self, tmp_path):
        """build() extracts meaningful keywords (excluding stop words)."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "always verify database migrations before deploying")

        kg = KnowledgeGraph(store)
        kg.build()

        assert kg.keyword_count > 0
        # 'database', 'migrations', 'deploying' should be keywords; 'the' should not
        all_keywords = set(kg._keyword_index.keys())
        assert "database" in all_keywords or "migrations" in all_keywords
        assert "the" not in all_keywords
        assert "a" not in all_keywords

    def test_build_extracts_file_refs(self, tmp_path):
        """build() indexes file references like 'golem/verifier.py'."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "check golem/verifier.py for subprocess usage")

        kg = KnowledgeGraph(store)
        kg.build()

        # The file_index should have the file reference
        assert "golem/verifier.py" in kg._file_index

    def test_build_with_empty_store(self, tmp_path):
        """build() on empty store results in zero nodes."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        kg = KnowledgeGraph(store)
        kg.build()

        assert kg.node_count == 0
        assert kg.keyword_count == 0


# ---------------------------------------------------------------------------
# KnowledgeGraph.query()
# ---------------------------------------------------------------------------


class TestQuery:
    def test_query_returns_relevant_nodes_by_keyword(self, tmp_path):
        """query() returns nodes whose keywords overlap with the subject."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "always mock subprocess calls in tests")
        _add_instinct(store, "ensure database transactions are atomic")

        kg = KnowledgeGraph(store)
        kg.build()

        results = kg.query("mock subprocess boundary testing")
        texts = [n.text for n in results]

        assert any("subprocess" in t for t in texts)

    def test_query_scores_file_matches_higher(self, tmp_path):
        """query() returns file-matching nodes first (higher score)."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "check verifier.py carefully", confidence=0.6)
        _add_instinct(
            store,
            "always add tests when modifying database",
            confidence=0.6,
        )

        kg = KnowledgeGraph(store)
        kg.build()

        results = kg.query("update logic", files=["golem/verifier.py"])
        assert len(results) > 0
        assert "verifier.py" in results[0].text

    def test_query_respects_min_confidence(self, tmp_path):
        """query() excludes nodes below min_confidence."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        low_inst = _add_instinct(store, "low confidence pitfall text", confidence=0.3)
        # Lower further to just 0.25 by contradicting once
        store.contradict(low_inst.id)

        _add_instinct(store, "high confidence pitfall text", confidence=0.8)

        kg = KnowledgeGraph(store)
        kg.build()

        results = kg.query("pitfall text", min_confidence=0.5)
        texts = [n.text for n in results]
        assert all("high" in t for t in texts)
        assert not any("low" in t for t in texts)

    def test_query_respects_max_results(self, tmp_path):
        """query() returns at most max_results nodes."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        for i in range(10):
            _add_instinct(store, "testing pitfall number %d check coverage" % i)

        kg = KnowledgeGraph(store)
        kg.build()

        results = kg.query("testing coverage", max_results=3)
        assert len(results) <= 3

    def test_query_no_matches_returns_empty(self, tmp_path):
        """query() returns empty list when no keywords match."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "database migration must be reversible")

        kg = KnowledgeGraph(store)
        kg.build()

        results = kg.query("xyz_totally_unrelated_zzz_qwerty")
        assert results == []

    def test_query_triggers_build_if_not_built(self, tmp_path):
        """query() calls build() lazily if nodes are empty."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "always verify subprocess usage")

        kg = KnowledgeGraph(store)
        # Do NOT call build() — query() should build lazily
        results = kg.query("verify subprocess")
        assert len(results) == 1

    def test_query_files_none_defaults_empty(self, tmp_path):
        """query() with files=None does not crash."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "check imports carefully")

        kg = KnowledgeGraph(store)
        kg.build()

        results = kg.query("imports", files=None)
        assert isinstance(results, list)

    def test_query_basename_match_scores_file(self, tmp_path):
        """query() also matches file refs by basename (not full path)."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "watch out for bugs in verifier.py async handling")

        kg = KnowledgeGraph(store)
        kg.build()

        # Pass full path — basename 'verifier.py' should match
        results = kg.query("async handling", files=["golem/core/verifier.py"])
        assert len(results) == 1
        assert "verifier.py" in results[0].text

    def test_query_exact_path_match_scores_file(self, tmp_path):
        """query() scores a node when the exact file path is in file_refs."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        # Instinct text contains the full path — stored verbatim in file_refs
        _add_instinct(store, "watch out for golem/verifier.py subprocess calls")

        kg = KnowledgeGraph(store)
        kg.build()

        # The stored file ref is "golem/verifier.py"; pass the exact same path
        results = kg.query("subprocess calls", files=["golem/verifier.py"])
        assert len(results) == 1
        assert "golem/verifier.py" in results[0].text

    @pytest.mark.parametrize(
        "subject,expected_present",
        [
            ("subprocess mock testing", "subprocess"),
            ("database migration atomic", "database"),
            ("circular import module", "circular"),
        ],
    )
    def test_query_keyword_matching_parametrized(
        self, tmp_path, subject, expected_present
    ):
        """query() matches the keyword expected for various subjects."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "always mock subprocess calls in tests")
        _add_instinct(store, "ensure database transactions are atomic")
        _add_instinct(store, "avoid circular import chains in module structure")

        kg = KnowledgeGraph(store)
        kg.build()

        results = kg.query(subject)
        texts = " ".join(n.text for n in results)
        assert expected_present in texts


# ---------------------------------------------------------------------------
# KnowledgeGraph.query_for_context()
# ---------------------------------------------------------------------------


class TestQueryForContext:
    def test_returns_formatted_text_when_matches(self, tmp_path):
        """query_for_context() returns a non-empty markdown section."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "always mock subprocess boundaries in tests")

        kg = KnowledgeGraph(store)
        kg.build()

        text = kg.query_for_context("mock subprocess testing")
        assert text != ""
        assert "## Relevant Knowledge" in text
        assert "subprocess" in text

    def test_returns_empty_string_for_no_matches(self, tmp_path):
        """query_for_context() returns '' when no nodes match."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "database migration must be reversible")

        kg = KnowledgeGraph(store)
        kg.build()

        text = kg.query_for_context("xyz_totally_unrelated_zzz_qwerty")
        assert text == ""

    def test_output_includes_category(self, tmp_path):
        """query_for_context() output includes the node category."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "subprocess calls must be mocked", category="antipatterns")

        kg = KnowledgeGraph(store)
        kg.build()

        text = kg.query_for_context("subprocess mock")
        assert "antipatterns" in text

    def test_output_includes_pitfall_text(self, tmp_path):
        """query_for_context() includes the exact pitfall text."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "always verify subprocess isolation in tests")

        kg = KnowledgeGraph(store)
        kg.build()

        text = kg.query_for_context("subprocess isolation")
        assert "always verify subprocess isolation in tests" in text

    def test_respects_max_results(self, tmp_path):
        """query_for_context() passes max_results to query()."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        for i in range(8):
            _add_instinct(
                store, "testing pitfall number %d requires coverage check" % i
            )

        kg = KnowledgeGraph(store)
        kg.build()

        text_2 = kg.query_for_context("testing coverage", max_results=2)
        text_5 = kg.query_for_context("testing coverage", max_results=5)

        # text_5 should contain more pitfall lines than text_2
        bullet_count_2 = text_2.count("\n- ")
        bullet_count_5 = text_5.count("\n- ")
        assert bullet_count_5 >= bullet_count_2


# ---------------------------------------------------------------------------
# Keyword extraction (stop words / identifiers)
# ---------------------------------------------------------------------------


class TestKeywordExtraction:
    @pytest.mark.parametrize(
        "stop_word",
        ["a", "an", "the", "is", "are", "in", "of", "to", "it", "and", "or"],
    )
    def test_stop_words_excluded(self, tmp_path, stop_word):
        """Common stop words are not included in the keyword index."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        # Text contains only stop words plus one real keyword
        _add_instinct(store, "%s the database migration" % stop_word)

        kg = KnowledgeGraph(store)
        kg.build()

        assert stop_word not in kg._keyword_index

    def test_identifiers_extracted(self, tmp_path):
        """Python-style identifiers like function names are indexed."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "call run_verification before committing changes")

        kg = KnowledgeGraph(store)
        kg.build()

        all_keywords = set(kg._keyword_index.keys())
        assert "run_verification" in all_keywords

    def test_short_words_excluded(self, tmp_path):
        """Words of 2 characters or fewer are not indexed as keywords."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        # 'do' and 'it' are short/stop words; 'run' is 3 chars — valid
        _add_instinct(store, "do it run pytest")

        kg = KnowledgeGraph(store)
        kg.build()

        all_keywords = set(kg._keyword_index.keys())
        assert "do" not in all_keywords
        assert "it" not in all_keywords


# ---------------------------------------------------------------------------
# File pattern extraction
# ---------------------------------------------------------------------------


class TestFilePatternExtraction:
    @pytest.mark.parametrize(
        "text,expected_ref",
        [
            ("check golem/verifier.py for subprocess", "golem/verifier.py"),
            ("see config.yaml for default values", "config.yaml"),
            ("update golem/tests/test_flow.py coverage", "golem/tests/test_flow.py"),
        ],
    )
    def test_file_refs_extracted(self, tmp_path, text, expected_ref):
        """File-like references are extracted into the file index."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, text)

        kg = KnowledgeGraph(store)
        kg.build()

        assert expected_ref in kg._file_index

    def test_no_false_file_refs(self, tmp_path):
        """Plain words are not treated as file references."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "always run tests before shipping code")

        kg = KnowledgeGraph(store)
        kg.build()

        # Nothing in the text matches the file pattern (\w+\.\w{1,4})
        # 'tests' and 'code' don't have dots
        assert len(kg._file_index) == 0


# ---------------------------------------------------------------------------
# node_count and keyword_count properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_node_count_zero_before_build(self, tmp_path):
        """node_count is 0 before build() is called."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "some pitfall text here")

        kg = KnowledgeGraph(store)
        assert kg.node_count == 0

    def test_node_count_after_build(self, tmp_path):
        """node_count reflects the number of active instincts after build."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "pitfall one")
        _add_instinct(store, "pitfall two check")
        _add_instinct(store, "pitfall three test coverage")

        kg = KnowledgeGraph(store)
        kg.build()

        assert kg.node_count == 3

    def test_keyword_count_zero_before_build(self, tmp_path):
        """keyword_count is 0 before build() is called."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "some pitfall text")

        kg = KnowledgeGraph(store)
        assert kg.keyword_count == 0

    def test_keyword_count_after_build(self, tmp_path):
        """keyword_count reflects indexed keywords after build."""
        from golem.knowledge_graph import KnowledgeGraph

        store = _make_store(tmp_path)
        _add_instinct(store, "always validate database migrations carefully")

        kg = KnowledgeGraph(store)
        kg.build()

        assert kg.keyword_count > 0
