# README i18n sync verification

- Verification level: L0 docs-only.
- Commands run:
  - `git diff -- README.ko.md` — PASS; inspected Korean source changes for FAQ, support line, usage note, China note removal, and API-key recommendation.
  - `git diff -- README.md README.ja.md README.zh-CN.md` — PASS; pre-edit check showed no existing target README diffs, and final diff inspection confirmed the five propagated changes only.
  - `git diff --check` — PASS; no whitespace errors reported.
- Skips/notes:
  - Runtime tests/builds skipped because this was a root README documentation-only synchronization.
