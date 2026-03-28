# Codex Subagent Usage

## Scope

This repository provides a project-scoped Codex setup for:

- `build`: default implementation-oriented CLI profile
- `plan`: read-only planning CLI profile
- `implement`: scoped write-capable worker
- `review`: read-only reviewer
- `web_research`: read-only current-docs and source-comparison helper
- `default`: locked fallback shim that only redirects to repo-scoped roles
- `worker`: locked fallback shim that only redirects to `implement`
- `explorer`: compatibility shim that redirects instead of exploring

## Prerequisites

- Trust the repository in Codex so project-scoped `.codex/` config is loaded.
- Restart Codex after changing `.codex/config.toml`, `.codex/agents/`, or `.codex/rules/`.
- Treat `build` and `plan` as CLI-only profiles until Codex IDE profile support exists.

## Entry Points

Use the CLI directly:

```bash
codex --profile build
codex --profile plan
```

## Routing Rules

- Start any write-capable work from `build`.
- Use `plan` only for analysis, decomposition, and handoff preparation.
- Use `implement` for scoped edits and local verification.
- Use `review` for read-only review with findings first.
- Use `web_research` for current external docs and source comparison.
- `default` and `worker` are fallback-only locks. If either appears, it should redirect instead of doing repo work.
- Do not use `explorer` for normal repo work.

Because Codex reapplies parent runtime overrides to spawned children, a read-only `plan` session must not be used to launch write-capable implementation. Switch back to `build` first.

## Reload Notes

- If runtime behavior still does not match `.codex/config.toml` or `.codex/agents/*.toml`, trust the repository and restart Codex before testing again.
- The repo intentionally ships redirect-only `default` and `worker` agents so environments that expose generic fallback roles do not silently bypass `implement` and `review`.

## Command Policy

`.codex/rules/default.rules` carries the high-risk command policy for this repository.

Current defaults:

- prompt: `git push`, `sudo`, `ssh`, `scp`, `npm publish`, `pnpm publish`, `docker push`
- forbidden: `git push --force`, `git push --force-with-lease`, `git reset --hard`, destructive `git clean`, `rm -rf /`

Some `opencode` glob rules require manual approximation when translated to Codex `prefix_rule()` entries. The repo covers the common high-risk forms explicitly, and any unmatched variants should still fall back to the broader prompt rules. Keep the rule file and the migration plan aligned when extending coverage.
