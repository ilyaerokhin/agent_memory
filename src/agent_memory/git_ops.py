"""Thin wrappers around the git CLI.

All functions invoke ``git`` as a subprocess. Network operations return a
``GitResult`` so callers can distinguish a genuine failure (we want to keep
going) from a programmer error (we want to crash).
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NETWORK_ERROR_FRAGMENTS = (
    "could not resolve host",
    "could not resolve hostname",
    "connection refused",
    "connection timed out",
    "network is unreachable",
    "operation timed out",
    "ssl_error",
    "ssl handshake",
    "failed to connect",
    "no such host",
    "temporary failure in name resolution",
    "could not read from remote repository",
    "the remote end hung up",
)


@dataclass(frozen=True)
class GitResult:
    """Outcome of a git subprocess call."""

    ok: bool
    stdout: str
    stderr: str
    returncode: int
    network_error: bool = False

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


def _looks_like_network_error(stderr: str) -> bool:
    lower = stderr.lower()
    return any(fragment in lower for fragment in NETWORK_ERROR_FRAGMENTS)


def run_git(
    args: list[str],
    *,
    cwd: Path | str,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> GitResult:
    """Execute ``git <args>`` inside ``cwd``.

    Set ``check=True`` to raise ``subprocess.CalledProcessError`` for non-zero
    exit codes (use this only for programmer errors, never for network ops).
    """
    cmd = ["git", *args]
    logger.debug("git %s (cwd=%s)", " ".join(args), cwd)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    result = GitResult(
        ok=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
        network_error=proc.returncode != 0 and _looks_like_network_error(proc.stderr),
    )
    if check and not result.ok:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr
        )
    return result


def is_git_repo(path: Path) -> bool:
    """Return ``True`` if ``path`` is inside a git working tree."""
    if not path.exists():
        return False
    result = run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return result.ok and result.stdout.strip() == "true"


def get_top_level(path: Path) -> Path | None:
    """Return the absolute path of the git working tree root or ``None``."""
    result = run_git(["rev-parse", "--show-toplevel"], cwd=path)
    if not result.ok:
        return None
    return Path(result.stdout.strip())


def current_branch(repo: Path) -> str | None:
    """Return the current branch name or ``None`` for detached HEAD."""
    result = run_git(["branch", "--show-current"], cwd=repo, check=True)
    branch = result.stdout.strip()
    return branch or None


def branch_exists_local(repo: Path, branch: str) -> bool:
    result = run_git(["rev-parse", "--verify", f"refs/heads/{branch}"], cwd=repo)
    return result.ok


def branch_exists_remote(repo: Path, branch: str, remote: str = "origin") -> bool:
    result = run_git(
        ["rev-parse", "--verify", f"refs/remotes/{remote}/{branch}"], cwd=repo
    )
    return result.ok


def fetch(repo: Path, remote: str = "origin") -> GitResult:
    """Fetch from ``remote``. Network errors are surfaced via ``GitResult``."""
    return run_git(["fetch", "--prune", remote], cwd=repo)


def checkout(repo: Path, branch: str) -> GitResult:
    return run_git(["checkout", branch], cwd=repo)


def checkout_new_from(repo: Path, branch: str, base: str) -> GitResult:
    return run_git(["checkout", "-b", branch, base], cwd=repo)


def commit_all(repo: Path, message: str) -> GitResult:
    add = run_git(["add", "--all"], cwd=repo)
    if not add.ok:
        return add
    status = run_git(["status", "--porcelain"], cwd=repo)
    if status.ok and not status.stdout.strip():
        return GitResult(ok=True, stdout="nothing to commit", stderr="", returncode=0)
    return run_git(["commit", "-m", message], cwd=repo)


def pull_rebase(repo: Path, remote: str, branch: str) -> GitResult:
    return run_git(["pull", "--rebase", remote, branch], cwd=repo)


def push(repo: Path, remote: str, branch: str, *, set_upstream: bool = False) -> GitResult:
    args = ["push"]
    if set_upstream:
        args.extend(["--set-upstream", remote, branch])
    else:
        args.extend([remote, branch])
    return run_git(args, cwd=repo)


def unpushed_commit_count(repo: Path, remote: str, branch: str) -> int:
    """Return the number of local commits not present in ``remote/branch``.

    Falls back to ``0`` when the remote branch is unknown (e.g. brand new).
    """
    spec = f"{remote}/{branch}..HEAD"
    result = run_git(["rev-list", "--count", spec], cwd=repo)
    if not result.ok:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def clone(remote_url: str, target: Path) -> GitResult:
    """Clone ``remote_url`` into ``target``. Target must not exist yet."""
    target.parent.mkdir(parents=True, exist_ok=True)
    return run_git(["clone", remote_url, str(target)], cwd=target.parent)


_SAFE_BRANCH_RE = re.compile(r"[^a-zA-Z0-9._/-]")


def sanitize_branch_name(name: str) -> str:
    """Make a branch name safe for git refs."""
    cleaned = _SAFE_BRANCH_RE.sub("-", name).strip("-/.")
    return cleaned or "unnamed"
