"""Orchestration of ``agent-memory save``.

Workflow:

1. Read the agent transcript.
2. Run a basic secret scan; redact obvious matches before they leak.
3. Build a prompt that injects the current ``memory/*.md`` plus the (redacted)
   transcript and tells the LLM to update the memory files in place.
4. Invoke the detected LLM provider so it can write into ``memory/``.
5. If no provider is available, write the raw transcript into
   ``memory/sessions/<date>.md`` as a fallback so nothing is lost.
6. Commit, pull --rebase, push (each step is fail-open on network errors).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import git_ops, llm

logger = logging.getLogger(__name__)

MEMORY_DIR_NAME = "memory"
SESSIONS_DIR_NAME = "sessions"
PROMPT_FILENAME = "summarize_session.md"

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github_oauth", re.compile(r"gho_[A-Za-z0-9]{20,}")),
    ("github_app", re.compile(r"ghu_[A-Za-z0-9]{20,}|ghs_[A-Za-z0-9]{20,}")),
    ("openai", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9-]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}")),
)


@dataclass
class SaveResult:
    committed: bool
    pushed: bool
    network_ok: bool
    used_llm: bool
    provider: str | None
    unpushed_count: int
    note: str = ""


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """Replace obvious secrets with placeholders and return the list of hits."""
    hits: list[str] = []
    redacted = text
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(redacted):
            hits.append(name)
            redacted = pattern.sub(f"<REDACTED:{name}>", redacted)
    return redacted, hits


def _memory_snapshot(memory_repo: Path) -> str:
    memory_dir = memory_repo / MEMORY_DIR_NAME
    if not memory_dir.exists():
        return "(no memory files yet)"
    chunks: list[str] = []
    for path in sorted(memory_dir.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        chunks.append(f"### {path.name}\n\n{content}")
    return "\n\n".join(chunks) if chunks else "(no memory files yet)"


def _load_prompt_template(prompts_dir: Path) -> str:
    template = prompts_dir / PROMPT_FILENAME
    return template.read_text(encoding="utf-8")


def _build_prompt(
    template: str,
    *,
    memory_snapshot: str,
    transcript: str,
    branch: str,
    project_root: Path,
    today: str,
) -> str:
    return (
        template.replace("{{BRANCH}}", branch)
        .replace("{{PROJECT_ROOT}}", str(project_root))
        .replace("{{TODAY}}", today)
        .replace("{{MEMORY_SNAPSHOT}}", memory_snapshot)
        .replace("{{TRANSCRIPT}}", transcript)
    )


def _write_session_fallback(memory_repo: Path, transcript: str, today: str) -> Path:
    sessions_dir = memory_repo / MEMORY_DIR_NAME / SESSIONS_DIR_NAME
    sessions_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    path = sessions_dir / f"{stamp}.md"
    body = (
        f"# Session {stamp}\n\n"
        f"_LLM provider unavailable; raw transcript saved as fallback._\n\n"
        f"```\n{transcript}\n```\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def save_session(
    *,
    memory_repo: Path,
    project_root: Path,
    transcript_text: str,
    prompts_dir: Path,
    llm_preference: str | None,
    push: bool,
    remote: str = "origin",
) -> SaveResult:
    """Run the save pipeline end to end.

    ``memory_repo`` must already be on the correct branch (caller's job, usually
    delegated to ``branch_sync.sync_branch``).
    """
    branch = git_ops.current_branch(memory_repo) or "unknown"
    today = dt.date.today().isoformat()

    redacted_transcript, hits = redact_secrets(transcript_text)
    if hits:
        logger.warning("Redacted secrets before save: %s", ", ".join(hits))

    provider = llm.detect_provider(llm_preference)
    used_llm = False
    note_parts: list[str] = []

    if hits:
        note_parts.append("secrets redacted: " + ", ".join(hits))

    if provider is not None:
        snapshot = _memory_snapshot(memory_repo)
        template = _load_prompt_template(prompts_dir)
        prompt = _build_prompt(
            template,
            memory_snapshot=snapshot,
            transcript=redacted_transcript,
            branch=branch,
            project_root=project_root,
            today=today,
        )
        try:
            completed = llm.run_provider(provider, prompt, cwd=memory_repo)
            if completed.returncode != 0:
                logger.warning(
                    "LLM provider exited with %s: %s",
                    completed.returncode,
                    completed.stderr.strip(),
                )
                _write_session_fallback(memory_repo, redacted_transcript, today)
                note_parts.append("LLM failed; saved raw transcript")
            else:
                used_llm = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM invocation raised: %s", exc)
            _write_session_fallback(memory_repo, redacted_transcript, today)
            note_parts.append("LLM crashed; saved raw transcript")
    else:
        _write_session_fallback(memory_repo, redacted_transcript, today)
        note_parts.append("no LLM in PATH; saved raw transcript")

    commit_msg = f"session: {today} ({branch})"
    commit_res = git_ops.commit_all(memory_repo, commit_msg)
    committed = commit_res.ok and "nothing to commit" not in commit_res.stdout

    network_ok = True
    pushed = False
    if push:
        pull_res = git_ops.pull_rebase(memory_repo, remote, branch)
        if pull_res.network_error:
            network_ok = False
            note_parts.append("offline: pull --rebase skipped")
        elif not pull_res.ok:
            note_parts.append(f"pull --rebase conflict: {pull_res.stderr.strip()}")

        push_res = git_ops.push(memory_repo, remote, branch, set_upstream=True)
        if push_res.network_error:
            network_ok = False
            note_parts.append("offline: push deferred")
        elif push_res.ok:
            pushed = True
        else:
            note_parts.append(f"push failed: {push_res.stderr.strip()}")

    unpushed = git_ops.unpushed_commit_count(memory_repo, remote, branch)

    return SaveResult(
        committed=committed,
        pushed=pushed,
        network_ok=network_ok,
        used_llm=used_llm,
        provider=provider.name if provider else None,
        unpushed_count=unpushed,
        note="; ".join(note_parts),
    )
