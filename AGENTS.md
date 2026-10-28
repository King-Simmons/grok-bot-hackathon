# Agent instructions

Lofi News Radio uses [pstack](https://github.com/backnotprop/pstack), Lauren Tan's (poteto) MIT-licensed Agent Skills stack. One copy lives in `vendor/pstack/`. Symlinks make it a project skill for Cursor, Claude Code, Codex, and Pi. `LICENSE` and `NOTICE` in that directory name the author and the upstream commit.

## Start with poteto-mode

For any non-trivial task, read `vendor/pstack/skills/poteto-mode/SKILL.md` and follow it before editing. A non-trivial task is a bug fix, a feature, a refactor, a review, an investigation, or any change that spans more than one step. The playbook in that file picks the rest of the skills.

Invoke it as `/poteto-mode` in Cursor. In Claude Code, read the same file and follow it. If Claude Code loaded pstack as a plugin, the slash name is `/pstack:poteto-mode`. Codex and other harnesses read `.agents/skills/poteto-mode/SKILL.md`, which is the same file.

A one-line typo fix can skip the playbook. The pull-request rules below still apply.

## Delegates

Use the `poteto-agent` subagent for delegates. Cursor loads `.cursor/agents/poteto-agent.md`. Claude Code loads `.claude/agents/poteto-agent.md`. Both are symlinks to `vendor/pstack/agents/poteto-agent.md`.

The parent chat owns the playbook. Spawn a fresh `poteto-agent` for each new task. In a harness that does not register that subagent, start a general subagent whose prompt tells it to read `vendor/pstack/skills/poteto-mode/SKILL.md` in full, including the Principles section, before any work.

## Verify

Prove a change to the control room or the HTTP API with `.cursor/skills/verify-lofi-news-radio/SKILL.md`. The same skill is linked from `.claude/skills/` and `.agents/skills/`.

Run tests with this command and no other:

```bash
./scripts/test.sh
```

`scripts/test.sh` is the single test entry point. When the suite or CI runner changes, edit that file's exec line and leave every other instruction calling `./scripts/test.sh`.

## Pull requests

Branch from `main`. Keep each pull request small and based on `main`. Never push to `main`. Open a pull request.

This is a public repository. Do not commit secrets, tokens, or `.env` files.

## Where the files are

| Harness | Skills | Subagents |
| --- | --- | --- |
| Cursor | `.cursor/skills/` | `.cursor/agents/` |
| Claude Code | `.claude/skills/` | `.claude/agents/` |
| Codex, OpenCode | `.agents/skills/` | `vendor/pstack/agents/poteto-agent.md` |
| Pi | `.pi/skills/` | `vendor/pstack/agents/poteto-agent.md` |

`scripts/link-pstack.sh` rebuilds the symlinks. `scripts/update-pstack.sh` pulls a newer mirror commit into `vendor/pstack/` and relinks. Neither script edits the verification skill.

Claude Code does not read `.cursor/rules`. This file is the shared instruction. Cursor also loads `.cursor/rules/poteto-mode.mdc`.
