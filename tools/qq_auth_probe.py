import os
import sys
from pathlib import Path

bundle = Path(__file__).resolve().parent
if (bundle / "app" / "qq_auth_entry.py").is_file():
    runtime = bundle / "runtime" / "python.exe"
    os.execv(
        str(runtime),
        [str(runtime), str(bundle / "app" / "qq_auth_entry.py"), "probe", *sys.argv[1:]],
    )
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from puripuly_heart.diagnostics.qq_auth_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
