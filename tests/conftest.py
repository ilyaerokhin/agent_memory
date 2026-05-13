"""Shared pytest fixtures.

We exercise the real ``git`` binary because the entire library is a thin
wrapper around it; mocking would prove nothing. Repositories are created in
isolated ``tmp_path`` directories so tests stay hermetic.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_memory import git_ops


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(path: Path, default_branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", default_branch], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Test Bot"], path)
    _git(["commit", "--allow-empty", "-m", "init"], path)


@pytest.fixture()
def project_repo(tmp_path: Path) -> Path:
    """An empty git repository to stand in for the user's project."""
    repo = tmp_path / "project"
    _init_repo(repo)
    return repo


@pytest.fixture()
def memory_remote(tmp_path: Path) -> Path:
    """A bare repository that simulates the private ``agent_memory`` remote."""
    remote = tmp_path / "memory-remote.git"
    remote.mkdir()
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )

    seed = tmp_path / "memory-seed"
    _init_repo(seed)
    (seed / "README.md").write_text("# memory remote seed\n", encoding="utf-8")
    _git(["add", "README.md"], seed)
    _git(["commit", "-m", "seed"], seed)
    _git(["remote", "add", "origin", str(remote)], seed)
    _git(["push", "origin", "main"], seed)
    return remote


@pytest.fixture()
def memory_clone(tmp_path: Path, memory_remote: Path) -> Path:
    """A working clone of ``memory_remote``."""
    target = tmp_path / "memory-clone"
    git_ops.clone(str(memory_remote), target)
    return target
