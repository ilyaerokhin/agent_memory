"""Installation logic: clone, .gitignore, native git hook, agent configs.

The two markers below are critical: anything between them belongs to
agent-memory and can be safely rewritten by repeated runs of ``init`` /
``install-hooks``. Everything else is preserved.
"""

from __future__ import annotations

import json
import logging
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from . import git_ops, paths

logger = logging.getLogger(__name__)

BEGIN_MARK = "# >>> agent-memory >>>"
END_MARK = "# <<< agent-memory <<<"

GITIGNORE_ENTRY = ".agent-memory/"
GITIGNORE_HEADER = "# agent-memory local clone (per-machine, do not commit)"

POST_CHECKOUT_FILENAME = "post-checkout"
CURSOR_DIRNAME = ".cursor"
CURSOR_HOOKS_FILENAME = "hooks.json"
CLAUDE_DIRNAME = ".claude"
CLAUDE_SETTINGS_FILENAME = "settings.json"


@dataclass
class InstallReport:
    cloned: bool
    gitignore_updated: bool
    post_checkout_installed: bool
    cursor_hooks_installed: bool
    claude_hooks_installed: bool
    clone_dir: Path
    notes: list[str]


def _ensure_remote_clone(remote_url: str, target: Path) -> bool:
    """Clone ``remote_url`` into ``target`` if not already a git repo there.

    Returns ``True`` when an actual clone happened.
    """
    if (target / ".git").exists():
        logger.info("Existing clone reused at %s", target)
        return False
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(
            f"Clone target {target} exists and is not empty; refuse to overwrite."
        )
    result = git_ops.clone(remote_url, target)
    if not result.ok:
        raise RuntimeError(
            f"git clone failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return True


def _update_gitignore(project_root: Path) -> bool:
    """Make sure ``.agent-memory/`` is ignored. Returns ``True`` on change."""
    gitignore = project_root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if any(line.strip() == GITIGNORE_ENTRY for line in existing.splitlines()):
        return False
    addition = ""
    if existing and not existing.endswith("\n"):
        addition += "\n"
    addition += f"\n{GITIGNORE_HEADER}\n{GITIGNORE_ENTRY}\n"
    gitignore.write_text(existing + addition, encoding="utf-8")
    return True


def _replace_marker_block(content: str, block: str) -> str:
    """Return ``content`` with the marker block replaced/appended."""
    lines = content.splitlines(keepends=False)
    out: list[str] = []
    in_block = False
    replaced = False
    for line in lines:
        if line.strip() == BEGIN_MARK:
            in_block = True
            out.append(block)
            replaced = True
            continue
        if line.strip() == END_MARK:
            in_block = False
            continue
        if not in_block:
            out.append(line)
    new_text = "\n".join(out)
    if not replaced:
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_text += block + "\n"
    elif not new_text.endswith("\n"):
        new_text += "\n"
    return new_text


def _post_checkout_block(template_body: str) -> str:
    body = template_body.strip("\n")
    return f"{BEGIN_MARK}\n{body}\n{END_MARK}"


def _install_post_checkout(project_root: Path, templates_dir: Path) -> bool:
    """Install or update ``.git/hooks/post-checkout`` with marker block."""
    hooks_dir = project_root / ".git" / "hooks"
    if not hooks_dir.exists():
        logger.warning("No .git/hooks directory under %s; skipping", project_root)
        return False

    template_path = templates_dir / POST_CHECKOUT_FILENAME
    if not template_path.exists():
        raise RuntimeError(f"post-checkout template missing: {template_path}")

    block = _post_checkout_block(template_path.read_text(encoding="utf-8"))
    target = hooks_dir / POST_CHECKOUT_FILENAME

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        new_text = _replace_marker_block(existing, block)
    else:
        new_text = f"#!/bin/sh\n{block}\n"

    target.write_text(new_text, encoding="utf-8")
    current = target.stat().st_mode
    target.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return True


def _merge_cursor_hooks(project_root: Path, source: Path) -> bool:
    """Merge our hooks into ``.cursor/hooks.json``, preserving user entries."""
    cursor_dir = project_root / CURSOR_DIRNAME
    cursor_dir.mkdir(parents=True, exist_ok=True)
    target = cursor_dir / CURSOR_HOOKS_FILENAME
    incoming = json.loads(source.read_text(encoding="utf-8"))

    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = target.with_suffix(".json.broken")
            shutil.copy2(target, backup)
            existing = {"version": 1, "hooks": {}}
    else:
        existing = {"version": 1, "hooks": {}}

    existing.setdefault("version", incoming.get("version", 1))
    existing.setdefault("hooks", {})

    for event, definitions in incoming.get("hooks", {}).items():
        bucket = existing["hooks"].setdefault(event, [])
        for definition in definitions:
            command = definition.get("command")
            already = any(
                isinstance(item, dict) and item.get("command") == command
                for item in bucket
            )
            if not already:
                bucket.append(definition)

    target.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def _merge_claude_settings(project_root: Path, source: Path) -> bool:
    """Merge our hooks into ``.claude/settings.json``, preserving user entries.

    Claude Code stores hooks under ``hooks.<EventName>[].hooks[].command``.
    """
    claude_dir = project_root / CLAUDE_DIRNAME
    claude_dir.mkdir(parents=True, exist_ok=True)
    target = claude_dir / CLAUDE_SETTINGS_FILENAME
    incoming = json.loads(source.read_text(encoding="utf-8"))

    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = target.with_suffix(".json.broken")
            shutil.copy2(target, backup)
            existing = {}
    else:
        existing = {}

    existing.setdefault("hooks", {})

    for event, matchers in incoming.get("hooks", {}).items():
        existing_event = existing["hooks"].setdefault(event, [])
        for entry in matchers:
            existing_event.append(entry)

    target.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def install_into_project(
    project_root: Path,
    *,
    remote_url: str | None,
    global_store: bool,
    templates_dir: Path,
    hooks_templates_dir: Path,
) -> InstallReport:
    """Clone the memory repo and wire up all hooks for ``project_root``.

    ``remote_url`` may be ``None`` if a clone already exists and we are only
    reinstalling hooks (``agent-memory install-hooks``).
    """
    project_root = project_root.resolve()
    existing = paths.discover_location(project_root)
    notes: list[str] = []

    if existing is not None:
        location = existing
        cloned = False
        if remote_url:
            notes.append("clone already present; --remote ignored")
    else:
        if not remote_url:
            raise RuntimeError(
                "No clone exists yet; --remote <url> is required for first init."
            )
        location = paths.planned_location(project_root, global_store=global_store)
        cloned = _ensure_remote_clone(remote_url, location.clone_dir)
        if location.mode == "global":
            paths.register_global_clone(project_root, location.clone_dir)
            paths.write_project_config(project_root, location)

    gitignore_changed = False
    if location.mode == "inline":
        gitignore_changed = _update_gitignore(project_root)
    else:
        notes.append("global store: .gitignore not modified")

    post_checkout_ok = _install_post_checkout(project_root, templates_dir)

    cursor_src = hooks_templates_dir / "cursor" / CURSOR_HOOKS_FILENAME
    cursor_ok = _merge_cursor_hooks(project_root, cursor_src) if cursor_src.exists() else False

    claude_src = hooks_templates_dir / "claude" / CLAUDE_SETTINGS_FILENAME
    claude_ok = _merge_claude_settings(project_root, claude_src) if claude_src.exists() else False

    return InstallReport(
        cloned=cloned,
        gitignore_updated=gitignore_changed,
        post_checkout_installed=post_checkout_ok,
        cursor_hooks_installed=cursor_ok,
        claude_hooks_installed=claude_ok,
        clone_dir=location.clone_dir,
        notes=notes,
    )
