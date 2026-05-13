"""Command-line interface for ``agent-memory``."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from . import branch_sync, git_ops, install, load_memory, paths, resources, summarize


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _read_transcript(transcript_path: Path | None) -> str:
    if transcript_path is None or str(transcript_path) in {"", "-"}:
        if sys.stdin.isatty():
            raise click.ClickException(
                "No transcript supplied: pass --transcript-path or pipe text on stdin."
            )
        return sys.stdin.read()
    if not transcript_path.exists():
        raise click.ClickException(f"Transcript file not found: {transcript_path}")
    return transcript_path.read_text(encoding="utf-8")


class _Group(click.Group):
    """Group that surfaces ``RuntimeError`` as user-facing CLI errors."""

    def invoke(self, ctx: click.Context):  # type: ignore[override]
        try:
            return super().invoke(ctx)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=_Group)
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Git-backed long-term memory for coding agents."""
    _configure_logging(verbose)
    ctx.ensure_object(dict)


@main.command(name="init")
@click.argument(
    "project_path",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=".",
)
@click.option("--remote", "remote_url", required=True, help="Private agent_memory remote URL.")
@click.option(
    "--global-store",
    is_flag=True,
    help="Store the clone in ~/.agent_memory/<hash>/ instead of <project>/.agent-memory/.",
)
def init_cmd(project_path: Path, remote_url: str, global_store: bool) -> None:
    """Wire ``agent-memory`` into PROJECT_PATH.

    Clones the memory repo, updates ``.gitignore``, installs the native
    post-checkout git hook, and merges hook configs for Cursor / Claude Code.
    """
    project_root = paths.resolve_project_root(project_path)
    report = install.install_into_project(
        project_root,
        remote_url=remote_url,
        global_store=global_store,
        templates_dir=resources.templates_dir(),
        hooks_templates_dir=resources.hooks_templates_dir(),
    )
    click.echo(f"clone_dir: {report.clone_dir}")
    click.echo(f"cloned: {report.cloned}")
    click.echo(f"gitignore_updated: {report.gitignore_updated}")
    click.echo(f"post_checkout_installed: {report.post_checkout_installed}")
    click.echo(f"cursor_hooks_installed: {report.cursor_hooks_installed}")
    click.echo(f"claude_hooks_installed: {report.claude_hooks_installed}")
    for note in report.notes:
        click.echo(f"note: {note}")

    try:
        outcome = branch_sync.sync_branch(
            project_root, templates_dir=resources.templates_dir()
        )
        click.echo(f"branch: {outcome.memory_branch} (created={outcome.created})")
    except RuntimeError as exc:
        click.echo(f"sync-branch warning: {exc}", err=True)


@main.command(name="install-hooks")
@click.argument(
    "project_path",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=".",
)
def install_hooks_cmd(project_path: Path) -> None:
    """Reinstall hooks for an already-initialized project."""
    project_root = paths.resolve_project_root(project_path)
    paths.require_location(project_root)
    report = install.install_into_project(
        project_root,
        remote_url=None,
        global_store=False,
        templates_dir=resources.templates_dir(),
        hooks_templates_dir=resources.hooks_templates_dir(),
    )
    click.echo(f"post_checkout_installed: {report.post_checkout_installed}")
    click.echo(f"cursor_hooks_installed: {report.cursor_hooks_installed}")
    click.echo(f"claude_hooks_installed: {report.claude_hooks_installed}")


@main.command(name="sync-branch")
@click.option(
    "--project",
    "project_path",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=".",
)
@click.option("--base", "base_branch", default=None, help="Base branch for new memory branches.")
def sync_branch_cmd(project_path: Path, base_branch: str | None) -> None:
    """Align the memory branch with the project's current branch."""
    project_root = paths.resolve_project_root(project_path)
    outcome = branch_sync.sync_branch(
        project_root,
        base_branch=base_branch,
        templates_dir=resources.templates_dir(),
    )
    click.echo(
        f"project_branch={outcome.project_branch} memory_branch={outcome.memory_branch} "
        f"created={outcome.created} network_ok={outcome.network_ok}"
    )
    if outcome.note:
        click.echo(f"note: {outcome.note}")


@main.command(name="load")
@click.option(
    "--project",
    "project_path",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=".",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["md", "json"]),
    default="json",
    help="Output format. JSON uses the Cursor hook contract (additional_context).",
)
@click.option(
    "--max-bytes",
    type=int,
    default=load_memory.DEFAULT_MAX_BYTES,
    help="Soft size limit for the rendered memory blob.",
)
def load_cmd(project_path: Path, output_format: str, max_bytes: int) -> None:
    """Dump memory files for the current branch."""
    project_root = paths.resolve_project_root(project_path)
    location = paths.require_location(project_root)
    branch = git_ops.current_branch(location.clone_dir) or "unknown"
    loaded = load_memory.load_memory(location.clone_dir, branch=branch, max_bytes=max_bytes)
    click.echo(load_memory.render(loaded, output_format=output_format))


@main.command(name="save")
@click.option(
    "--project",
    "project_path",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=".",
)
@click.option(
    "--transcript-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to the conversation transcript file (stdin if omitted).",
)
@click.option(
    "--llm",
    "llm_preference",
    type=click.Choice(["auto", "claude", "cursor-agent"]),
    default="auto",
    help="LLM CLI to use for summarization.",
)
@click.option(
    "--no-push",
    is_flag=True,
    help="Skip git pull --rebase / push at the end (offline / debug mode).",
)
def save_cmd(
    project_path: Path,
    transcript_path: Path | None,
    llm_preference: str,
    no_push: bool,
) -> None:
    """Summarize a session into memory, commit, and push."""
    project_root = paths.resolve_project_root(project_path)
    location = paths.require_location(project_root)
    transcript = _read_transcript(transcript_path)
    preference = None if llm_preference == "auto" else llm_preference
    result = summarize.save_session(
        memory_repo=location.clone_dir,
        project_root=project_root,
        transcript_text=transcript,
        prompts_dir=resources.prompts_dir(),
        llm_preference=preference,
        push=not no_push,
    )
    click.echo(
        json.dumps(
            {
                "committed": result.committed,
                "pushed": result.pushed,
                "network_ok": result.network_ok,
                "used_llm": result.used_llm,
                "provider": result.provider,
                "unpushed_count": result.unpushed_count,
                "note": result.note,
            },
            ensure_ascii=False,
        )
    )


@main.command(name="status")
@click.option(
    "--project",
    "project_path",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=".",
)
def status_cmd(project_path: Path) -> None:
    """Show a one-shot summary of the memory state for this project."""
    project_root = paths.resolve_project_root(project_path)
    location = paths.require_location(project_root)
    project_branch = git_ops.current_branch(project_root)
    memory_branch = git_ops.current_branch(location.clone_dir)
    aligned = (
        project_branch is not None
        and memory_branch == git_ops.sanitize_branch_name(project_branch)
    )
    unpushed = git_ops.unpushed_commit_count(
        location.clone_dir, "origin", memory_branch or "HEAD"
    )
    click.echo(
        json.dumps(
            {
                "project_root": str(project_root),
                "clone_dir": str(location.clone_dir),
                "mode": location.mode,
                "project_branch": project_branch,
                "memory_branch": memory_branch,
                "aligned": aligned,
                "unpushed_commits": unpushed,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    main()
