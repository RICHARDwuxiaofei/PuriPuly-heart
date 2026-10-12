from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Output directory already exists; choose a fresh versioned directory")
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo, text=True
    ).strip():
        raise SystemExit("Commit tracked application changes before building an identified preview")
    flet_archive = repo / "build" / "flet" / "flet-windows.zip"
    with flet_archive.open("rb") as reader:
        if (
            hashlib.file_digest(reader, "sha256").hexdigest()
            != "758f21506fbb9ad180bd93c7460a2ca55630401c6026a9bc9e2273444014491d"
        ):
            raise SystemExit("Pinned Flet archive checksum mismatch")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=repo, text=True
    ).strip()
    output.mkdir(parents=True)
    shutil.copy2(repo / "scripts" / "preview" / "README.md", output / "README.md")
    shutil.copy2(repo / "docs" / "desktop-feature-pr-notes.md", output / "FEATURES.md")
    runtime = output / "runtime"
    shutil.copytree(
        Path(sys.base_prefix),
        runtime,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "test",
            "tests",
            "ensurepip",
            "tkinter",
            "tcl",
            "idlelib",
            "pip*",
            "Scripts",
        ),
    )
    source_packages = repo / ".venv" / "Lib" / "site-packages"
    shutil.copytree(
        source_packages,
        runtime / "Lib" / "site-packages",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "*.pth",
            "*.egg-link",
            "cmake",
            "clang",
            "ruff",
            "pytest*",
            "_pytest",
            "black*",
            "blib2to3",
            "coverage*",
            "virtualenv*",
            "libclang*",
            "pip*",
        ),
    )
    app = output / "app"
    app.mkdir()
    shutil.copytree(
        repo / "src",
        app / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
    )
    for name in ("pyproject.toml", "LICENSE", "uv.lock"):
        shutil.copy2(repo / name, app / name)
    shutil.copy2(repo / "scripts" / "preview" / "entry.py", app / "preview_entry.py")
    shutil.copytree(repo / "build" / "llama.cpp", app / "build" / "llama.cpp")
    shutil.copytree(repo / "third_party" / "llama.cpp", app / "third_party" / "llama.cpp")
    shutil.unpack_archive(flet_archive, output / "flet")
    compiler = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:winexe",
            "/reference:System.Windows.Forms.dll",
            f"/out:{output / 'PuriPulyHeartPreview.exe'}",
            str(repo / "scripts" / "preview" / "Launcher.cs"),
        ],
        check=True,
    )
    (output / "BUILD.json").write_text(
        json.dumps(
            dict(
                branch=branch,
                commit=revision,
                qq_baseline="677a9ee837b7e19abfa758998e86f8bce6a28b3a",
                upstream_baseline="eac4fb465665f15edcaa7d794f467411185ca947",
                version="2.8.2-preview",
                packaging="portable bundled CPython + locked Flet desktop (not native release)",
                python=sys.version,
                isolated_keyring="puripuly-heart.test.desktop-preview",
                data_directory="preview-data",
            ),
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
