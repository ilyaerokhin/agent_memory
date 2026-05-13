"""Tests for ``branch_sync.sync_branch``."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_memory import branch_sync, git_ops, paths, resources


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _wire_clone(project_root: Path, memory_clone: Path) -> None:
    target = project_root / paths.INLINE_DIR_NAME
    if target.exists():
        return
    target.symlink_to(memory_clone, target_is_directory=True) if False else None
    import shutil

    shutil.copytree(memory_clone, target)


def test_sync_creates_new_branch(project_repo: Path, memory_clone: Path) -> None:
    _wire_clone(project_repo, memory_clone)
    _git(["checkout", "-b", "feat/login"], project_repo)

    outcome = branch_sync.sync_branch(
        project_repo,
        templates_dir=resources.templates_dir(),
    )
    assert outcome.project_branch == "feat/login"
    assert outcome.memory_branch == "feat/login"
    assert outcome.created is True

    inline_clone = project_repo / paths.INLINE_DIR_NAME
    assert git_ops.current_branch(inline_clone) == "feat/login"
    assert (inline_clone / "memory" / "CONTEXT.md").exists()


def test_sync_reuses_existing_branch(project_repo: Path, memory_clone: Path) -> None:
    _wire_clone(project_repo, memory_clone)
    _git(["checkout", "-b", "feat/login"], project_repo)
    branch_sync.sync_branch(project_repo, templates_dir=resources.templates_dir())

    _git(["checkout", "main"], project_repo)
    outcome_main = branch_sync.sync_branch(
        project_repo, templates_dir=resources.templates_dir()
    )
    assert outcome_main.memory_branch == "main"
    assert outcome_main.created is False

    _git(["checkout", "feat/login"], project_repo)
    outcome_login = branch_sync.sync_branch(
        project_repo, templates_dir=resources.templates_dir()
    )
    assert outcome_login.memory_branch == "feat/login"
    assert outcome_login.created is False


def test_sync_preserves_namespaced_branches(
    project_repo: Path, memory_clone: Path
) -> None:
    _wire_clone(project_repo, memory_clone)
    _git(["checkout", "-b", "fix/payments-bug"], project_repo)
    outcome = branch_sync.sync_branch(
        project_repo, templates_dir=resources.templates_dir()
    )
    assert outcome.memory_branch == "fix/payments-bug"
    inline_clone = project_repo / paths.INLINE_DIR_NAME
    assert git_ops.current_branch(inline_clone) == "fix/payments-bug"
