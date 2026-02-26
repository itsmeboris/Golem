"""Shared report writer — manages detail files and index tables."""

import logging
from pathlib import Path

logger = logging.getLogger("Tools.AgentAutomation.Report")


class ReportWriter:
    """Handles report directory creation, detail file writing, and index appending."""

    def __init__(self, report_dir: Path, index_path: Path):
        self.report_dir = report_dir
        self.index_path = index_path

    def _ensure_dirs(self) -> None:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

    def write_detail(self, filename: str, content: str) -> Path:
        """Write *content* to a detail file and return its path."""
        self._ensure_dirs()
        detail = self.report_dir / filename
        detail.write_text(content, encoding="utf-8")
        return detail

    def append_index(self, row: str, header: str = "") -> None:
        """Append *row* to the index file, creating it with *header* if missing."""
        self._ensure_dirs()
        if not self.index_path.exists() and header:
            self.index_path.write_text(header, encoding="utf-8")
        with open(self.index_path, "a", encoding="utf-8") as fh:
            fh.write(row)

    def detail_link(self, filename: str) -> str:
        """Return a relative markdown link from the index to a detail file."""
        rel = self.report_dir.relative_to(self.index_path.parent)
        return f"[report]({rel}/{filename})"
