# agent-memory

Git-backed long-term memory for coding agents. Designed for **Cursor** and
**Claude Code**, but the underlying CLI is agent-agnostic.

The idea: every git branch in your project gets its own "memory area" — a
folder of Markdown files that lives on a matching branch in a private
`agent_memory` repository. When you check out a branch in your project, the
memory follows. When a session ends, the LLM summarises the transcript into
those files and commits them back.

```
your-project (feat/login)               agent_memory (private remote)
├── src/                                ├── main           (templates + CLI)
├── ...                                 ├── feat/login     (memory/*.md)
└── .agent-memory/        <-- clone --> └── fix/auth-bug   (memory/*.md)
        (in .gitignore)
```

## How it works

1. `agent-memory init` clones a private `agent_memory` repo into
   `<project>/.agent-memory/` and installs three hooks:
   - **native git `post-checkout`** in the project — switches the memory clone
     to the matching branch on every `git checkout`, even when no agent is
     running;
   - **`sessionStart`** in Cursor / `SessionStart` in Claude Code — loads the
     current branch's `memory/*.md` into the agent's initial context;
   - **`sessionEnd`** in Cursor / `SessionEnd` in Claude Code — when the chat
     closes, summarises the transcript with an LLM, commits, and pushes.
2. Memory is **per-branch, per-developer**. Branch names in the memory repo
   mirror branch names in the project. The remote is private; nothing is ever
   shared with other people.
3. Synchronisation across machines is automatic via `git pull --rebase` +
   `git push` on every save. Network failures are non-fatal: commits queue
   locally and ship on the next successful save.

## Memory layout

Each per-branch memory area contains five Markdown files:

| File | Purpose |
| --- | --- |
| `memory/CONTEXT.md` | Goal, scope, and constraints of the feature on this branch. |
| `memory/PROGRESS.md` | What is done, in progress, and next. The most important file. |
| `memory/DECISIONS.md` | Architecture / implementation decisions taken (ADR-style). |
| `memory/GOTCHAS.md` | Non-obvious traps discovered while working. |
| `memory/OPEN_QUESTIONS.md` | Unresolved questions to surface at the next session. |
| `memory/sessions/<date>.md` | Per-session audit log (one entry per save). |

The LLM is instructed to keep each file under ~2 KB by compressing older
entries instead of dropping them.

## Requirements

- Python 3.10 or newer
- `git` on `PATH`
- An LLM CLI for summarisation. Either is fine; the tool auto-detects:
  - [`claude`](https://docs.anthropic.com/en/docs/claude-code) (Claude Code)
  - [`cursor-agent`](https://cursor.com/cli) (Cursor CLI)
- A **private** git remote for the memory repo (GitHub / GitLab / self-hosted).

## Install

```bash
pip install -e .              # development install from this repo
# or, once published:
pipx install agent-memory
```

You can also point users at `uv tool install agent-memory` if they prefer
[`uv`](https://github.com/astral-sh/uv).

## First-time setup for a project

```bash
cd /path/to/your/project
agent-memory init . --remote git@github.com:you/agent_memory.git
```

This will:

- clone the remote into `./.agent-memory/`
- append `.agent-memory/` to your `.gitignore`
- install `.git/hooks/post-checkout` (a marker block is added so any existing
  hook is preserved)
- merge `.cursor/hooks.json` and `.claude/settings.json`
- create a memory branch matching your current branch (seeded with templates)

Use `--global-store` to put the clone in `~/.agent_memory/<hash>/` instead of
inside the project. The mapping is stored in `~/.agent_memory/index.json`.

## Commands

| Command | Description |
| --- | --- |
| `agent-memory init <project> --remote <url>` | First-time wiring. `--remote` is required. |
| `agent-memory install-hooks <project>` | Reinstall hooks for an already-initialised project. |
| `agent-memory sync-branch [--project <path>]` | Align the memory branch with the project's current branch. Idempotent. |
| `agent-memory load [--format md\|json] [--max-bytes N]` | Dump the current branch's memory. Used by `sessionStart`. |
| `agent-memory save --transcript-path <p> [--llm auto\|claude\|cursor-agent] [--no-push]` | Summarise a transcript into memory, commit, and push. Used by `sessionEnd`. |
| `agent-memory status [--project <path>]` | Show clone location, mode, branch alignment, and unpushed commit count. |

All commands accept `-v / --verbose` for debug logging on stderr.

### Examples

```bash
agent-memory status
# {
#   "project_root": "/repos/checkout",
#   "clone_dir": "/repos/checkout/.agent-memory",
#   "mode": "inline",
#   "project_branch": "feat/login",
#   "memory_branch": "feat/login",
#   "aligned": true,
#   "unpushed_commits": 0
# }

# Manual save from a saved transcript (useful after a Cursor window crash):
agent-memory save --transcript-path ~/Downloads/last-session.txt

# Dry-run save without touching the remote:
agent-memory save --transcript-path ~/Downloads/last-session.txt --no-push
```

## Hook contracts

### Cursor (`.cursor/hooks.json`)

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [
      { "command": "agent-memory sync-branch && agent-memory load" }
    ],
    "sessionEnd": [
      { "command": "agent-memory save --transcript-path \"$CURSOR_TRANSCRIPT_PATH\"" }
    ]
  }
}
```

`load --format json` emits `{"additional_context": "..."}` so Cursor pastes
the memory directly into the system prompt.

### Claude Code (`.claude/settings.json`)

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "agent-memory sync-branch && agent-memory load" }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "agent-memory save --transcript-path \"$CLAUDE_TRANSCRIPT_PATH\"" }
        ]
      }
    ]
  }
}
```

### Native git (`.git/hooks/post-checkout`)

A POSIX shell hook (works on Linux, macOS, and Git for Windows) that runs
`agent-memory sync-branch --project <toplevel> &` whenever you check out a
branch. The hook is enclosed in marker comments so re-running `init` updates
only the agent-memory block.

## Lifecycle

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant Proj as Project .git
    participant Hook as post-checkout
    participant CLI as agent-memory
    participant Mem as .agent-memory/
    participant Agent as Cursor / Claude Code
    participant Remote as private remote

    Dev->>Proj: git checkout feat/login
    Proj->>Hook: trigger
    Hook->>CLI: sync-branch (background)
    CLI->>Mem: fetch + checkout/create branch
    Mem->>Remote: pull (fail-open if offline)

    Dev->>Agent: open chat
    Agent->>CLI: sessionStart -> sync-branch + load
    CLI-->>Agent: memory/*.md as additional_context

    Note over Dev,Agent: agent works through many turns; nothing is written yet

    Dev->>Agent: close chat / window
    Agent->>CLI: sessionEnd + transcript_path
    CLI->>CLI: redact secrets, summarise via LLM
    CLI->>Mem: edit memory/*.md, commit
    CLI->>Remote: pull --rebase && push (fail-open)
```

## Safety

- **Secret scanning.** Before the transcript reaches the LLM, common token
  shapes (`ghp_*`, `sk-*`, AWS keys, bearer tokens, private keys, ...) are
  redacted. Findings are surfaced in the `save` output `note` field.
- **No force pushes.** Conflicts are handled with `git pull --rebase`. If the
  rebase fails, the save is left committed locally and the issue is flagged
  in `agent-memory status`.
- **Hook overwrite protection.** `post-checkout`, `.cursor/hooks.json`, and
  `.claude/settings.json` are merged with the existing files; the agent-memory
  block is delimited by `# >>> agent-memory >>>` / `# <<< agent-memory <<<`.
- **Network failures are non-fatal.** `sessionEnd` will not crash the agent
  because the remote is briefly unreachable; commits queue and `status`
  shows the backlog.

## Known limitations (v1)

- **Hard window-close.** If Cursor's window is killed mid-`sessionEnd`, the
  child `agent-memory save` process may be terminated before the LLM call
  finishes. The transcript file remains on disk and can be replayed manually
  with `agent-memory save --transcript-path <path>`. A detached daemon /
  persistent queue is on the v2 roadmap.
- **No in-session writes.** The agent does not write into memory between
  turns; everything is captured at `sessionEnd`. An optional `memory.add`
  tool (or MCP server) may land in v2.
- **Single developer.** This is personal memory. Sharing a memory repo with
  teammates is explicitly out of scope and not safe (concurrent writes from
  multiple machines under the same identity are fine; concurrent writes from
  different developers would require a different conflict-resolution story).

## Development

```bash
python -m venv .venv
.\.venv\Scripts\activate     # PowerShell
# or: source .venv/bin/activate

pip install -e ".[dev]"
pytest
```

The test suite drives the real `git` binary against ephemeral repositories
under `tmp_path`, so it stays hermetic without requiring network access.

## License

MIT.
