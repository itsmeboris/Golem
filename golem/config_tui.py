"""Interactive TUI for Golem configuration editing.

Uses prompt_toolkit for full-screen arrow-key navigation.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from golem.config_editor import (
    FieldInfo,
    get_config_by_category,
    signal_daemon_reload,
    update_config,
)
from golem.core.config import load_config

try:
    from prompt_toolkit import Application  # pragma: no cover
    from prompt_toolkit.key_binding import KeyBindings  # pragma: no cover
    from prompt_toolkit.layout import Layout  # pragma: no cover
    from prompt_toolkit.layout.containers import HSplit, Window  # pragma: no cover
    from prompt_toolkit.layout.controls import FormattedTextControl  # pragma: no cover
except ImportError:  # pragma: no cover
    Application = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cycle_choice(current: Any, choices: list[str], direction: int) -> str:
    """Cycle through choices list. direction: +1 forward, -1 backward."""
    try:
        idx = choices.index(str(current))
    except ValueError:
        idx = 0
    return choices[(idx + direction) % len(choices)]


def _render_field_display(fi: FieldInfo) -> str:
    """Render a field's value for TUI display."""
    if fi.meta.sensitive:
        return "***"
    if fi.meta.field_type == "choice" and fi.meta.choices:
        return "[◀ %s ▶]" % fi.value
    if fi.meta.field_type == "bool":
        return "[on]" if fi.value else "[off]"
    if fi.meta.field_type == "list" and isinstance(fi.value, list):
        return ", ".join(str(v) for v in fi.value)
    return str(fi.value) if fi.value is not None else ""


# ---------------------------------------------------------------------------
# TUI State
# ---------------------------------------------------------------------------

CATEGORY_ORDER = [
    "profile",
    "budget",
    "models",
    "heartbeat",
    "self_update",
    "integrations",
    "dashboard",
    "daemon",
    "logging",
    "health",
    "polling",
]


@dataclass
class ConfigTUIState:
    """Mutable state for the TUI application."""

    config_path: Path
    categories: dict[str, list[FieldInfo]] = field(default_factory=dict)
    category_names: list[str] = field(default_factory=list)
    current_category_index: int = 0
    current_field_index: int = 0
    editing: bool = False
    edit_buffer: str = ""
    unsaved_changes: dict[str, Any] = field(default_factory=dict)
    status_message: str = ""

    @classmethod
    def from_config_path(cls, config_path: str | Path) -> "ConfigTUIState":
        path = Path(config_path)
        config = load_config(str(path))
        categories = get_config_by_category(config)
        names = [c for c in CATEGORY_ORDER if c in categories]
        return cls(config_path=path, categories=categories, category_names=names)

    @property
    def current_category(self) -> str:
        return self.category_names[self.current_category_index]

    @property
    def current_fields(self) -> list[FieldInfo]:
        return self.categories.get(self.current_category, [])

    @property
    def current_field(self) -> FieldInfo | None:
        fields = self.current_fields
        if 0 <= self.current_field_index < len(fields):
            return fields[self.current_field_index]
        return None


# ---------------------------------------------------------------------------
# TUI Application
# ---------------------------------------------------------------------------


def run_config_tui(config_path: Path) -> int:  # pragma: no cover
    """Launch the full-screen config editor. Returns 0 on success."""
    if Application is None:
        print("prompt_toolkit not installed", file=sys.stderr)
        return 1

    state = ConfigTUIState.from_config_path(config_path)
    kb = KeyBindings()

    @kb.add("q")
    def _quit(event):
        if state.editing:
            return
        if state.unsaved_changes:
            state.status_message = (
                "Unsaved changes! Press 's' to save or Ctrl+C to discard."
            )
            return
        event.app.exit(0)

    @kb.add("c-c")
    def _force_quit(event):
        event.app.exit(0)

    @kb.add("s")
    def _save(event):
        if state.editing:
            return
        if not state.unsaved_changes:
            state.status_message = "No changes to save."
            return
        errors = update_config(state.config_path, state.unsaved_changes)
        if errors:
            state.status_message = "Errors: " + "; ".join(errors)
            return
        pid_file = Path(os.environ.get("GOLEM_DATA_DIR", "data")) / "daemon.pid"
        reloaded = signal_daemon_reload(pid_file)
        state.unsaved_changes.clear()
        if reloaded:
            state.status_message = "Config saved. Daemon reload triggered."
        else:
            state.status_message = "Config saved. No running daemon."
        new_state = ConfigTUIState.from_config_path(state.config_path)
        state.categories = new_state.categories

    @kb.add("left")
    def _left(event):
        if state.editing:
            return
        fi = state.current_field
        if fi and fi.meta.field_type == "choice" and fi.meta.choices:
            new_val = _cycle_choice(fi.value, fi.meta.choices, -1)
            fi.value = new_val
            state.unsaved_changes[fi.key] = new_val
        elif fi and fi.meta.field_type == "bool":
            fi.value = not fi.value
            state.unsaved_changes[fi.key] = fi.value
        else:
            state.current_category_index = (state.current_category_index - 1) % len(
                state.category_names
            )
            state.current_field_index = 0

    @kb.add("right")
    def _right(event):
        if state.editing:
            return
        fi = state.current_field
        if fi and fi.meta.field_type == "choice" and fi.meta.choices:
            new_val = _cycle_choice(fi.value, fi.meta.choices, 1)
            fi.value = new_val
            state.unsaved_changes[fi.key] = new_val
        elif fi and fi.meta.field_type == "bool":
            fi.value = not fi.value
            state.unsaved_changes[fi.key] = fi.value
        else:
            state.current_category_index = (state.current_category_index + 1) % len(
                state.category_names
            )
            state.current_field_index = 0

    @kb.add("up")
    def _up(event):
        if state.editing:
            return
        if state.current_field_index > 0:
            state.current_field_index -= 1

    @kb.add("down")
    def _down(event):
        if state.editing:
            return
        if state.current_field_index < len(state.current_fields) - 1:
            state.current_field_index += 1

    @kb.add("tab")
    def _tab_next(event):
        if state.editing:
            return
        state.current_category_index = (state.current_category_index + 1) % len(
            state.category_names
        )
        state.current_field_index = 0

    @kb.add("s-tab")
    def _tab_prev(event):
        if state.editing:
            return
        state.current_category_index = (state.current_category_index - 1) % len(
            state.category_names
        )
        state.current_field_index = 0

    @kb.add("enter")
    def _enter(event):
        fi = state.current_field
        if fi is None:
            return
        if state.editing:
            state.editing = False
            fi.value = state.edit_buffer
            state.unsaved_changes[fi.key] = state.edit_buffer
            state.edit_buffer = ""
        elif fi.meta.field_type in ("str", "int", "float", "list"):
            state.editing = True
            state.edit_buffer = str(fi.value) if fi.value is not None else ""

    @kb.add("escape")
    def _escape(event):
        if state.editing:
            state.editing = False
            state.edit_buffer = ""

    def _get_tab_bar():
        parts = []
        for i, name in enumerate(state.category_names):
            if i == state.current_category_index:
                parts.append(("bold", " ▸ %s " % name.title()))
            else:
                parts.append(("", " %s " % name.title()))
            if i < len(state.category_names) - 1:
                parts.append(("", "│"))
        return parts

    def _get_fields():
        lines = []
        cat = state.current_category
        lines.append(("bold", "\n %s\n" % cat.title()))
        lines.append(("", " " + "─" * 40 + "\n"))
        for i, fi in enumerate(state.current_fields):
            prefix = " ▸ " if i == state.current_field_index else "   "
            name = fi.key.split(".", 1)[1]
            if state.editing and i == state.current_field_index:
                display = state.edit_buffer + "█"
            else:
                display = _render_field_display(fi)
            desc = fi.meta.description
            lines.append(
                ("", "%s%-30s %-25s %s\n" % (prefix, name + ":", display, desc))
            )
        return lines

    def _get_status():
        msg = (
            state.status_message
            or "s=save  q=quit  ←→=switch tab  ↑↓=navigate  enter=edit"
        )
        if state.unsaved_changes:
            msg = "* UNSAVED * " + msg
        return [("", "\n " + msg)]

    layout = Layout(
        HSplit(
            [
                Window(FormattedTextControl(_get_tab_bar), height=1),
                Window(FormattedTextControl(_get_fields)),
                Window(FormattedTextControl(_get_status), height=2),
            ]
        )
    )

    app = Application(layout=layout, key_bindings=kb, full_screen=True)
    return app.run()
