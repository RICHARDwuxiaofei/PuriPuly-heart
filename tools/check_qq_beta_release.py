from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def inspect_paths(paths):
    findings = []
    checked = 0
    forbidden = {
        ".env",
        ".env.local",
        "secrets.json",
        "credential-keys.json",
        "qq-auth-stages.jsonl",
    }
    patterns = [
        rb"gh[pousr]_[A-Za-z0-9]{30,}",
        rb"github_pat_[A-Za-z0-9_]{40,}",
        rb"sk-or-v1-[a-f0-9]{48,}",
        rb"sk-proj-[A-Za-z0-9_-]{40,}",
        rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----(?:\r?\n|\\n)[A-Za-z0-9+/=]{32,}",
    ]
    for path, label in paths:
        if not path.is_file():
            continue
        checked += 1
        if path.name.casefold() in forbidden or any(
            part in {".git", ".codex", "isolated-data", "preview-data"}
            for part in Path(label).parts
        ):
            findings.append({"path": label, "reason": "private_state_or_secret_filename"})
        if path.suffix.lower() in {
            ".py",
            ".json",
            ".toml",
            ".md",
            ".txt",
            ".ps1",
            ".cmd",
            ".yml",
            ".yaml",
            ".iss",
            ".cs",
            ".ts",
            ".mjs",
            ".env",
        }:
            data = path.read_bytes()
            if any(re.search(pattern, data) for pattern in patterns):
                findings.append({"path": label, "reason": "credential_pattern"})
    return dict(files_checked=checked, findings=findings, passed=not findings)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo).decode().split("\0")
    report = {"source": inspect_paths((repo / name, name) for name in tracked if name)}
    if args.payload:
        root = args.payload.resolve()
        report["payload"] = inspect_paths(
            (path, path.relative_to(root).as_posix()) for path in root.rglob("*")
        )
        report["payload_files_sha256"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.iterdir()
            if path.is_file()
        }
    report["passed"] = all(
        item["passed"] for key, item in report.items() if key in {"source", "payload"}
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "source_files": report["source"]["files_checked"],
                "findings": report["source"]["findings"],
            },
            ensure_ascii=True,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
