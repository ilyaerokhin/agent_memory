"""Locate bundled data directories (templates, hooks, prompts).

We support two layouts:

* development layout: the package lives in ``src/agent_memory/`` and the data
  directories live next to ``src/`` (``templates/``, ``hooks-templates/``,
  ``prompts/``).
* installed layout (future): same directories shipped inside the package via
  hatch ``force-include`` -- not yet enabled because the simpler dev layout
  covers v1 use.
"""

from __future__ import annotations

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


def _project_root() -> Path:
    return _PACKAGE_DIR.parent.parent


def templates_dir() -> Path:
    return _project_root() / "templates"


def hooks_templates_dir() -> Path:
    return _project_root() / "hooks-templates"


def prompts_dir() -> Path:
    return _project_root() / "prompts"
