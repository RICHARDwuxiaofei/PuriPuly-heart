from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))
for name in tuple(os.environ):
    if name.startswith(("PURIPULY_HEART_", "FLET_")) or name.endswith(
        ("_API_KEY", "_TOKEN", "_PSK")
    ):
        os.environ.pop(name, None)
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ["PYTHONPATH"] = str(root / "src")
os.environ["FLET_VIEW_PATH"] = str(root.parent / "flet" / "flet")
os.environ["FLET_SERVER_IP"] = "127.0.0.1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.chdir(root)

from puripuly_heart.qq_auth_beta import cleanup_data, configure_channel, has_reparse

if sys.argv[1:2] == ["cleanup-data"]:
    import json

    print(json.dumps(cleanup_data()))
elif sys.argv[1:2] == ["check-idle"]:
    candidate = Path(os.environ["LOCALAPPDATA"]) / "PuriPulyHeartQQAuthBeta"
    if candidate.exists():
        data = configure_channel()
        from puripuly_heart.core.control_instance import acquire

        lease = acquire(data / "settings.json")
        lease.close()
else:
    data = configure_channel()
    if "--config" in sys.argv:
        selected = Path(sys.argv[sys.argv.index("--config") + 1])
        if selected.resolve() != (data / "settings.json").resolve() or has_reparse(selected):
            raise ValueError("QQ Auth Beta config must stay in its dedicated data directory")
    if sys.argv[1:2] == ["probe"]:
        from puripuly_heart.diagnostics.qq_auth_probe import main

        raise SystemExit(main(sys.argv[2:]))
    from puripuly_heart.main import main

    main()
