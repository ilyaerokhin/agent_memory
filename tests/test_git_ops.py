"""Tests for low-level git helpers."""

from __future__ import annotations

from pathlib import Path

from agent_memory import git_ops


def test_current_branch_returns_main(project_repo: Path) -> None:
    assert git_ops.current_branch(project_repo) == "main"


def test_is_git_repo(project_repo: Path, tmp_path: Path) -> None:
    assert git_ops.is_git_repo(project_repo) is True
    assert git_ops.is_git_repo(tmp_path / "not-a-repo") is False


def test_branch_exists_local_and_remote(memory_clone: Path) -> None:
    assert git_ops.branch_exists_local(memory_clone, "main") is True
    assert git_ops.branch_exists_remote(memory_clone, "main") is True
    assert git_ops.branch_exists_local(memory_clone, "missing/branch") is False


def test_checkout_new_branch_and_commit(memory_clone: Path) -> None:
    result = git_ops.checkout_new_from(memory_clone, "feat/x", "origin/main")
    assert result.ok, result.output

    (memory_clone / "note.txt").write_text("hi", encoding="utf-8")
    commit = git_ops.commit_all(memory_clone, "feat: note")
    assert commit.ok, commit.output
    assert git_ops.current_branch(memory_clone) == "feat/x"


def test_sanitize_branch_name() -> None:
    assert git_ops.sanitize_branch_name("feat/login") == "feat/login"
    assert git_ops.sanitize_branch_name("bad name!!") == "bad-name"
    assert git_ops.sanitize_branch_name("///") == "unnamed"


def test_network_error_detection() -> None:
    fake_stderr = "fatal: unable to access: Could not resolve host: example.invalid"
    fake = git_ops.GitResult(
        ok=False,
        stdout="",
        stderr=fake_stderr,
        returncode=128,
        network_error=git_ops._looks_like_network_error(fake_stderr),
    )
    assert fake.network_error is True
