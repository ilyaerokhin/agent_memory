"""Keep the memory clone's branch aligned with the project's current branch.

Called from:

* the native ``post-checkout`` hook installed in the project ``.git/hooks/``
* the ``sessionStart`` hook of Cursor / Claude Code
* explicitly by the user via ``agent-memory sync-branch``
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import git_ops, paths

logger = logging.getLogger(__name__)

DEFAULT_BASE_BRANCH = "main"
TEMPLATE_DIR_NAME = "memory"


@dataclass
class SyncOutcome:
    project_branch: str
    memory_branch: str
    created: bool
    network_ok: bool
    note: str = ""


def _pick_base_branch(memory_repo: Path, preferred: str | None) -> str:
    """Pick the base branch the new memory branch should be created from."""
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    candidates.extend([DEFAULT_BASE_BRANCH, "master"])
    for candidate in candidates:
        if git_ops.branch_exists_remote(memory_repo, candidate):
            return f"origin/{candidate}"
        if git_ops.branch_exists_local(memory_repo, candidate):
            return candidate
    return "HEAD"


def _seed_memory_branch(memory_repo: Path, templates_dir: Path) -> bool:
    """Copy default memory templates into a freshly created branch.

    Returns ``True`` when files were copied (caller should commit them).
    """
    target = memory_repo / TEMPLATE_DIR_NAME
    if target.exists() and any(target.iterdir()):
        return False
    source = templates_dir / TEMPLATE_DIR_NAME
    if not source.exists():
        logger.warning("Template directory missing: %s", source)
        return False
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_file():
            shutil.copy2(item, target / item.name)
    return True


def sync_branch(
    project_root: Path,
    *,
    base_branch: str | None = None,
    templates_dir: Path | None = None,
) -> SyncOutcome:
    """Align the memory clone with the project's current branch."""
    project_root = project_root.resolve()
    location = paths.require_location(project_root)
    memory_repo = location.clone_dir

    project_branch = git_ops.current_branch(project_root)
    if not project_branch:
        raise RuntimeError(
            f"Project at {project_root} is in a detached HEAD state; "
            "agent-memory needs an active branch to mirror."
        )

    fetch_result = git_ops.fetch(memory_repo)
    network_ok = fetch_result.ok or not fetch_result.network_error
    if fetch_result.network_error:
        logger.warning("Network error during fetch: %s", fetch_result.stderr.strip())

    memory_branch = git_ops.sanitize_branch_name(project_branch)
    created = False

    if git_ops.branch_exists_local(memory_repo, memory_branch):
        checkout_result = git_ops.checkout(memory_repo, memory_branch)
        if not checkout_result.ok:
            raise RuntimeError(
                f"Failed to checkout existing memory branch '{memory_branch}': "
                f"{checkout_result.output}"
            )
    elif git_ops.branch_exists_remote(memory_repo, memory_branch):
        checkout_result = git_ops.run_git(
            ["checkout", "--track", f"origin/{memory_branch}"], cwd=memory_repo
        )
        if not checkout_result.ok:
            raise RuntimeError(
                f"Failed to track remote memory branch '{memory_branch}': "
                f"{checkout_result.output}"
            )
    else:
        base = _pick_base_branch(memory_repo, base_branch)
        result = git_ops.checkout_new_from(memory_repo, memory_branch, base)
        if not result.ok:
            raise RuntimeError(
                f"Failed to create memory branch '{memory_branch}' from '{base}': "
                f"{result.output}"
            )
        created = True
        if templates_dir is not None:
            if _seed_memory_branch(memory_repo, templates_dir):
                git_ops.commit_all(
                    memory_repo, f"chore: seed memory templates for {memory_branch}"
                )

    note = ""
    if not network_ok:
        note = "offline: working with local snapshot"

    return SyncOutcome(
        project_branch=project_branch,
        memory_branch=memory_branch,
        created=created,
        network_ok=network_ok,
        note=note,
    )
