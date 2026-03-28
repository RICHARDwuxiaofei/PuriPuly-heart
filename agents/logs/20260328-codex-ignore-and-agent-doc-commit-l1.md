Verification Level: L1

Commands run:
- `git status --short`
- `git check-ignore -v .codex/config.toml .codex/agents/default.toml .codex/agents/worker.toml`
- `git diff --check -- .gitignore AGENTS.md docs/codex-subagent-usage.md`

Outcome:
- PASS: `.codex/` is ignored again via `.gitignore`.
- PASS: `AGENTS.md` and `docs/codex-subagent-usage.md` remain available as tracked commit candidates.
- PASS: diff whitespace check passed for the commit-target files.

Skipped:
- No runtime reload verification was performed from inside this session.
- No broader test suite was run because the change scope is repository routing/docs and gitignore behavior.

Notes:
- Commit scope intentionally excludes ignored `.codex/` files after restoring `.codex/` to `.gitignore`.
- Commit scope includes tracked agent-routing documentation and the `.gitignore` change.
