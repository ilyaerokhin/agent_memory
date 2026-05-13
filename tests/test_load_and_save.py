"""Tests for ``load_memory`` and the offline path of ``save_session``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_memory import (
    branch_sync,
    git_ops,
    install,
    load_memory,
    paths,
    resources,
    summarize,
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _bootstrap(project_repo: Path, memory_remote: Path) -> Path:
    install.install_into_project(
        project_repo,
        remote_url=str(memory_remote),
        global_store=False,
        templates_dir=resources.templates_dir(),
        hooks_templates_dir=resources.hooks_templates_dir(),
    )
    _git(["checkout", "-b", "feat/login"], project_repo)
    branch_sync.sync_branch(project_repo, templates_dir=resources.templates_dir())
    return project_repo / paths.INLINE_DIR_NAME


def test_load_returns_json_with_metadata(project_repo: Path, memory_remote: Path) -> None:
    clone = _bootstrap(project_repo, memory_remote)
    branch = git_ops.current_branch(clone) or "main"
    loaded = load_memory.load_memory(clone, branch=branch)

    rendered = load_memory.render(loaded, output_format="json")
    parsed = json.loads(rendered)
    assert "additional_context" in parsed
    assert "CONTEXT.md" in parsed["additional_context"]
    assert "CONTEXT.md" in parsed["metadata"]["files_used"]
    assert parsed["metadata"]["truncated"] is False


def test_load_truncates_when_over_limit(project_repo: Path, memory_remote: Path) -> None:
    clone = _bootstrap(project_repo, memory_remote)
    branch = git_ops.current_branch(clone) or "main"
    loaded = load_memory.load_memory(clone, branch=branch, max_bytes=256)
    assert loaded.truncated is True
    assert load_memory.TRUNCATION_MARK.strip() in loaded.text


def test_save_without_llm_writes_fallback_session(
    project_repo: Path, memory_remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(summarize.llm, "detect_provider", lambda *_args, **_kw: None)

    clone = _bootstrap(project_repo, memory_remote)
    result = summarize.save_session(
        memory_repo=clone,
        project_root=project_repo,
        transcript_text="user: hi\nagent: hi back",
        prompts_dir=resources.prompts_dir(),
        llm_preference=None,
        push=False,
    )
    assert result.used_llm is False
    assert "raw transcript" in result.note
    sessions = list((clone / "memory" / "sessions").glob("*.md"))
    assert sessions, "fallback session file should be created"
    assert "user: hi" in sessions[0].read_text(encoding="utf-8")


def test_save_redacts_secrets(
    project_repo: Path, memory_remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(summarize.llm, "detect_provider", lambda *_args, **_kw: None)
    clone = _bootstrap(project_repo, memory_remote)

    transcript = (
        "user: here is my token\n"
        "agent: noted ghp_abcdefghijklmnopqrst1234\n"
        "agent: and openai sk-abcdefghijklmnopqrstuvwx\n"
    )
    result = summarize.save_session(
        memory_repo=clone,
        project_root=project_repo,
        transcript_text=transcript,
        prompts_dir=resources.prompts_dir(),
        llm_preference=None,
        push=False,
    )
    assert "github_pat" in result.note
    assert "openai" in result.note
    session = next((clone / "memory" / "sessions").glob("*.md"))
    body = session.read_text(encoding="utf-8")
    assert "ghp_abcdefghijklmnopqrst1234" not in body
    assert "<REDACTED:github_pat>" in body
    assert "<REDACTED:openai>" in body
