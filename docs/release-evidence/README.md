# Windows process-isolation evidence

Run from a clean checkout with supported Windows CPython 3.12 AMD64:

```powershell
$env:PYTHONPATH = "src"
python scripts/release/windows-process-isolation.py --evidence docs/release-evidence/windows-process-isolation-host.json
```

The harness uses no credentials or network access. It isolates worker `TEMP`, `TMP`, and
`PYTHONPATH`, and does not pass secret-bearing environment variables to emitters. Exit code `0`
means all checked-in numeric isolation and lifecycle contracts passed. Exit code `1` records a
measured contract failure. Exit code `2` records an explicit unsupported/capability-blocked
classification; callback arrival alone can never produce a pass.

The checked-in host artifact records the latest execution result. Release qualification requires
`status: passed`; a checked-in `blocked` artifact is honest host evidence, not release approval.

## Official ApplicationLoopback A/B

The A/B evidence pins Microsoft's unmodified
`https://github.com/microsoft/Windows-classic-samples.git` at
`77f217b3f89d4dac7864a62cc91ff7b569f26a50`. Clone and build it outside this repository with
MSBuild `Release|x64` and `PlatformToolset=v143`, then run:

```powershell
$env:PYTHONPATH = "src"
python scripts/release/windows-application-loopback-ab.py `
  --binary <ApplicationLoopback.exe> `
  --source-commit 77f217b3f89d4dac7864a62cc91ff7b569f26a50 `
  --proctap-evidence docs/release-evidence/windows-process-isolation-host.json `
  --thresholds scripts/release/windows-process-isolation-thresholds.json `
  --evidence docs/release-evidence/windows-application-loopback-ab-host.json `
  --msbuild-version 17.14.23.42201 --toolset v143-14.44.35207 `
  --configuration Release --architecture x64
```

The external source, NuGet restore, build products, WAV files, and emitters remain in isolated
temporary paths. Only provenance, hashes, topology, timings, and numeric A/B results are checked
in.

## Windows process-distribution closure

`windows-process-distribution-host.json` reports technical packaging independently from client
execution. `technical_status: passed` means packaged, installed, and reinstalled strict ProcTap
runtime checks passed. `manual_matrix_status: waived` means acceptance authority waived VRChat and
Discord Stable execution because no credential-free automatic client-audio action was available
and the user declined assisted manual execution. Those cells remain `waived` with result
`not_tested`; they are never counted as successful runs. PTB and Canary remain unavailable, and
the multilevel ancestry risk remains attached to every client cell.

Distribution schema v2 keeps installer identities separate:
`release_installer_sha256` identifies the production-identity release artifact, while
`isolated_installer_sha256` identifies the exact alternate-AppId artifact used by installer smoke.
The isolated artifact timestamp and associated install-log timestamp/hash are recorded alongside
them; validators reject equal or file-swapped provenance.
