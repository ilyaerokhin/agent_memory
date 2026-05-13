"""Path resolution for the local memory clone.

Two strategies are supported:

* ``inline``: the clone lives at ``<project>/.agent-memory/`` (default).
* ``global``: the clone lives at ``~/.agent_memory/<hash>/`` and the mapping
  ``project_root -> clone_dir`` is stored in ``~/.agent_memory/index.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import git_ops

INLINE_DIR_NAME = ".agent-memory"
GLOBAL_ROOT_ENV = "AGENT_MEMORY_HOME"
DEFAULT_GLOBAL_ROOT = Path.home() / ".agent_memory"
GLOBAL_INDEX_FILE = "index.json"
PROJECT_CONFIG_NAME = ".agent-memory.config"


@dataclass(frozen=True)
class MemoryLocation:
    """Where the memory clone lives for a given project."""

    project_root: Path
    clone_dir: Path
    mode: str  # "inline" or "global"


def global_root() -> Path:
    """Resolve the directory that stores global memory clones."""
    override = os.environ.get(GLOBAL_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_GLOBAL_ROOT


def _project_hash(project_root: Path) -> str:
    canonical = str(project_root.resolve()).lower()
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def _load_index() -> dict[str, str]:
    path = global_root() / GLOBAL_INDEX_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_index(index: dict[str, str]) -> None:
    root = global_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / GLOBAL_INDEX_FILE
    path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")


def resolve_project_root(start: Path | None = None) -> Path:
    """Find the git project root starting from ``start`` (defaults to cwd)."""
    base = (start or Path.cwd()).resolve()
    top = git_ops.get_top_level(base)
    if top is None:
        raise RuntimeError(
            f"Not inside a git repository: {base}. "
            f"agent-memory requires the target project to be a git repo."
        )
    return top


def register_global_clone(project_root: Path, clone_dir: Path) -> None:
    """Persist the mapping ``project_root -> clone_dir`` in the global index."""
    index = _load_index()
    index[str(project_root.resolve())] = str(clone_dir.resolve())
    _save_index(index)


def lookup_global_clone(project_root: Path) -> Path | None:
    """Return the registered clone for ``project_root`` if present."""
    mapping = _load_index().get(str(project_root.resolve()))
    return Path(mapping) if mapping else None


def write_project_config(project_root: Path, location: MemoryLocation) -> None:
    """Persist the chosen storage mode inside the project (used for global mode)."""
    cfg = project_root / PROJECT_CONFIG_NAME
    payload = {
        "mode": location.mode,
        "clone_dir": str(location.clone_dir.resolve()),
    }
    cfg.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_project_config(project_root: Path) -> dict | None:
    cfg = project_root / PROJECT_CONFIG_NAME
    if not cfg.exists():
        return None
    try:
        return json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def planned_location(project_root: Path, *, global_store: bool) -> MemoryLocation:
    """Compute where a fresh clone should live for ``project_root``."""
    project_root = project_root.resolve()
    if global_store:
        clone = global_root() / _project_hash(project_root)
        return MemoryLocation(project_root=project_root, clone_dir=clone, mode="global")
    return MemoryLocation(
        project_root=project_root,
        clone_dir=project_root / INLINE_DIR_NAME,
        mode="inline",
    )


def discover_location(project_root: Path) -> MemoryLocation | None:
    """Locate an existing clone for ``project_root`` (inline or global)."""
    project_root = project_root.resolve()

    inline = project_root / INLINE_DIR_NAME
    if (inline / ".git").exists():
        return MemoryLocation(project_root=project_root, clone_dir=inline, mode="inline")

    cfg = read_project_config(project_root)
    if cfg and cfg.get("mode") == "global":
        clone = Path(cfg["clone_dir"])
        if (clone / ".git").exists():
            return MemoryLocation(
                project_root=project_root, clone_dir=clone, mode="global"
            )

    registered = lookup_global_clone(project_root)
    if registered and (registered / ".git").exists():
        return MemoryLocation(
            project_root=project_root, clone_dir=registered, mode="global"
        )

    return None


def require_location(project_root: Path) -> MemoryLocation:
    """Like :func:`discover_location` but raises when nothing is set up."""
    loc = discover_location(project_root)
    if loc is None:
        raise RuntimeError(
            f"No agent-memory clone found for project {project_root}. "
            f"Run `agent-memory init {project_root} --remote <url>` first."
        )
    return loc
