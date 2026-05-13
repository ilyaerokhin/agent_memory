"""Tests for project bootstrap (``install_into_project``)."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from agent_memory import git_ops, install, paths, resources


def test_install_end_to_end(project_repo: Path, memory_remote: Path) -> None:
    report = install.install_into_project(
        project_repo,
        remote_url=str(memory_remote),
        global_store=False,
        templates_dir=resources.templates_dir(),
        hooks_templates_dir=resources.hooks_templates_dir(),
    )

    assert report.cloned is True
    assert report.gitignore_updated is True
    assert report.post_checkout_installed is True
    assert report.cursor_hooks_installed is True
    assert report.claude_hooks_installed is True

    assert (project_repo / paths.INLINE_DIR_NAME / ".git").exists()
    gitignore = (project_repo / ".gitignore").read_text(encoding="utf-8")
    assert ".agent-memory/" in gitignore

    hook = project_repo / ".git" / "hooks" / "post-checkout"
    assert hook.exists()
    body = hook.read_text(encoding="utf-8")
    assert install.BEGIN_MARK in body
    assert install.END_MARK in body
    assert "agent-memory sync-branch" in body
    if sys.platform != "win32":
        mode = hook.stat().st_mode
        assert mode & stat.S_IXUSR

    cursor_hooks = json.loads(
        (project_repo / install.CURSOR_DIRNAME / install.CURSOR_HOOKS_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert "sessionStart" in cursor_hooks["hooks"]
    assert "sessionEnd" in cursor_hooks["hooks"]

    claude_settings = json.loads(
        (project_repo / install.CLAUDE_DIRNAME / install.CLAUDE_SETTINGS_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert "SessionStart" in claude_settings["hooks"]
    assert "SessionEnd" in claude_settings["hooks"]


def test_install_is_idempotent(project_repo: Path, memory_remote: Path) -> None:
    install.install_into_project(
        project_repo,
        remote_url=str(memory_remote),
        global_store=False,
        templates_dir=resources.templates_dir(),
        hooks_templates_dir=resources.hooks_templates_dir(),
    )
    second = install.install_into_project(
        project_repo,
        remote_url=str(memory_remote),
        global_store=False,
        templates_dir=resources.templates_dir(),
        hooks_templates_dir=resources.hooks_templates_dir(),
    )
    assert second.cloned is False
    assert second.gitignore_updated is False

    hook = (project_repo / ".git" / "hooks" / "post-checkout").read_text(encoding="utf-8")
    assert hook.count(install.BEGIN_MARK) == 1
    assert hook.count(install.END_MARK) == 1


def test_install_preserves_existing_post_checkout(
    project_repo: Path, memory_remote: Path
) -> None:
    hooks_dir = project_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    existing = hooks_dir / "post-checkout"
    existing.write_text("#!/bin/sh\necho 'user hook'\nexit 0\n", encoding="utf-8")

    install.install_into_project(
        project_repo,
        remote_url=str(memory_remote),
        global_store=False,
        templates_dir=resources.templates_dir(),
        hooks_templates_dir=resources.hooks_templates_dir(),
    )

    body = existing.read_text(encoding="utf-8")
    assert "echo 'user hook'" in body
    assert install.BEGIN_MARK in body
    assert "agent-memory sync-branch" in body


def test_install_requires_remote_on_first_run(project_repo: Path) -> None:
    with pytest.raises(RuntimeError, match="--remote"):
        install.install_into_project(
            project_repo,
            remote_url=None,
            global_store=False,
            templates_dir=resources.templates_dir(),
            hooks_templates_dir=resources.hooks_templates_dir(),
        )


def test_install_global_store(
    project_repo: Path, memory_remote: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.GLOBAL_ROOT_ENV, str(tmp_path / "agent-memory-home"))
    report = install.install_into_project(
        project_repo,
        remote_url=str(memory_remote),
        global_store=True,
        templates_dir=resources.templates_dir(),
        hooks_templates_dir=resources.hooks_templates_dir(),
    )
    assert report.cloned is True
    assert report.clone_dir.is_relative_to(tmp_path / "agent-memory-home")
    assert (project_repo / paths.PROJECT_CONFIG_NAME).exists()
    assert (
        ".agent-memory/" not in (project_repo / ".gitignore").read_text(encoding="utf-8")
        if (project_repo / ".gitignore").exists()
        else True
    )
    discovered = paths.discover_location(project_repo)
    assert discovered is not None
    assert discovered.mode == "global"
    assert git_ops.is_git_repo(discovered.clone_dir)
