"""Dump the current branch's memory files in a form suitable for hooks.

The Cursor ``sessionStart`` hook expects ``{"additional_context": "..."}`` on
stdout; the same payload is fine as a free-form blob for Claude Code's
``SessionStart``. We always emit valid JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_BYTES = 16 * 1024
TRUNCATION_MARK = "\n\n... [truncated by agent-memory] ..."

PRIORITY_ORDER = (
    "CONTEXT.md",
    "PROGRESS.md",
    "OPEN_QUESTIONS.md",
    "DECISIONS.md",
    "GOTCHAS.md",
)


@dataclass
class LoadedMemory:
    text: str
    files_used: list[str]
    truncated: bool
    branch: str


def _ordered_files(memory_dir: Path) -> list[Path]:
    by_name = {p.name: p for p in memory_dir.glob("*.md") if p.is_file()}
    ordered = [by_name[name] for name in PRIORITY_ORDER if name in by_name]
    leftovers = sorted(p for p in by_name.values() if p not in ordered)
    return ordered + leftovers


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_memory(
    memory_repo: Path,
    *,
    branch: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> LoadedMemory:
    """Build the text blob for ``additional_context``.

    Files are concatenated in priority order; once we exceed ``max_bytes`` the
    rest is replaced with a truncation marker.
    """
    memory_dir = memory_repo / "memory"
    if not memory_dir.exists():
        return LoadedMemory(text="", files_used=[], truncated=False, branch=branch)

    chunks: list[str] = []
    used: list[str] = []
    truncated = False
    running = 0
    header = f"# agent-memory ({branch})\n"
    chunks.append(header)
    running += len(header.encode("utf-8"))

    for path in _ordered_files(memory_dir):
        body = _read_file(path).rstrip() + "\n"
        section = f"\n## {path.name}\n\n{body}"
        section_bytes = len(section.encode("utf-8"))
        if running + section_bytes > max_bytes:
            remaining = max_bytes - running
            if remaining > len(TRUNCATION_MARK):
                chunks.append(section[: remaining - len(TRUNCATION_MARK)])
                chunks.append(TRUNCATION_MARK)
            else:
                chunks.append(TRUNCATION_MARK)
            truncated = True
            break
        chunks.append(section)
        used.append(path.name)
        running += section_bytes

    return LoadedMemory(text="".join(chunks), files_used=used, truncated=truncated, branch=branch)


def render(loaded: LoadedMemory, *, output_format: str) -> str:
    """Render to ``md`` (plain text) or ``json`` (Cursor hook contract)."""
    if output_format == "md":
        return loaded.text
    if output_format == "json":
        payload = {
            "additional_context": loaded.text,
            "metadata": {
                "branch": loaded.branch,
                "files_used": loaded.files_used,
                "truncated": loaded.truncated,
            },
        }
        return json.dumps(payload, ensure_ascii=False)
    raise ValueError(f"Unknown output format: {output_format}")
