## Verification

- Level: L2
- Commands run:
  - `$env:PYTHONPATH='src'; & 'C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe' -m pytest tests/core/test_hub_overlay_streaming.py tests/core/test_hub_low_latency.py tests/ui/test_event_bridge.py tests/core/test_fallback_racing_llm_provider.py -v`
  - `$env:PYTHONPATH='src'; & 'C:\Users\salee\Documents\dev\puripuly_heart\.venv\Scripts\python.exe' -m pytest tests/app/test_overlay_process_manager.py -k "peer_first_render_trace_passthrough" -v`
- Result: PASS
- Skips: none
- Notes:
  - Peer overlay publication now uses final-only `translate()` publication rather than peer `stream_translate()` publication.
  - Successful peer runs no longer emit `translation_stream_update` or peer `llm_first_chunk` traces.
  - Detailed dashboard apply marker added: `dashboard_translation_applied`.
