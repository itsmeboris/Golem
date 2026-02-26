# pylint: disable=too-few-public-methods
"""Tests for golem.core.report — shared report writer."""

from golem.core.report import ReportWriter


class TestReportWriter:
    def test_write_detail(self, tmp_path):
        rw = ReportWriter(tmp_path / "reports", tmp_path / "index.md")
        path = rw.write_detail("test.md", "# Report\nContent here")
        assert path.exists()
        assert path.read_text() == "# Report\nContent here"

    def test_write_detail_creates_dirs(self, tmp_path):
        rw = ReportWriter(tmp_path / "deep" / "reports", tmp_path / "deep" / "index.md")
        path = rw.write_detail("r.md", "data")
        assert path.exists()

    def test_append_index_creates_with_header(self, tmp_path):
        rw = ReportWriter(tmp_path / "reports", tmp_path / "index.md")
        rw.append_index("| row1 |\n", header="# Index\n\n| Col |\n")
        content = (tmp_path / "index.md").read_text()
        assert "# Index" in content
        assert "| row1 |" in content

    def test_append_index_no_duplicate_header(self, tmp_path):
        idx = tmp_path / "index.md"
        rw = ReportWriter(tmp_path / "reports", idx)
        rw.append_index("| row1 |\n", header="# H\n")
        rw.append_index("| row2 |\n", header="# H\n")
        content = idx.read_text()
        assert content.count("# H") == 1
        assert "row1" in content
        assert "row2" in content

    def test_detail_link(self, tmp_path):
        rw = ReportWriter(
            tmp_path / "reports" / "golem", tmp_path / "reports" / "index.md"
        )
        link = rw.detail_link("42.md")
        assert "42.md" in link
        assert "[report]" in link
