"""Detect and invoke an external LLM CLI for session summarization.

Supported CLIs (in detection order):

1. ``claude`` (Claude Code) -- invoked as ``claude -p <prompt>``
2. ``cursor-agent`` (Cursor CLI) -- invoked as ``cursor-agent -p <prompt>``

The ``--llm`` flag of ``agent-memory save`` lets the user pin a specific CLI.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMProvider:
    name: str
    executable: str


SUPPORTED_PROVIDERS: tuple[LLMProvider, ...] = (
    LLMProvider(name="claude", executable="claude"),
    LLMProvider(name="cursor-agent", executable="cursor-agent"),
)


def detect_provider(preferred: str | None = None) -> LLMProvider | None:
    """Return the first available provider.

    When ``preferred`` is set, only that provider is considered.
    """
    candidates: list[LLMProvider]
    if preferred:
        match = next((p for p in SUPPORTED_PROVIDERS if p.name == preferred), None)
        if match is None:
            raise ValueError(
                f"Unknown LLM provider: {preferred}. "
                f"Supported: {[p.name for p in SUPPORTED_PROVIDERS]}"
            )
        candidates = [match]
    else:
        candidates = list(SUPPORTED_PROVIDERS)

    for provider in candidates:
        if shutil.which(provider.executable):
            return provider
    return None


def run_provider(
    provider: LLMProvider,
    prompt: str,
    *,
    cwd: Path,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    """Run the provider with the supplied prompt as a one-shot.

    The LLM is expected to interact with files in ``cwd`` directly (via its
    own ``Write`` / ``Edit`` tools). We capture stdout/stderr for diagnostics
    but do not parse them.
    """
    cmd = [provider.executable, "-p", prompt]
    logger.info("Running LLM provider: %s (cwd=%s)", provider.name, cwd)
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
