# Overlay Two-Slot Static Caption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current retained strip animation model with a fixed two-slot overlay that uses explicit occupant metadata, no enter/exit/reflow motion, and a one-shot 6px left accent bar.

**Architecture:** Python owns logical caption eligibility, late-arrival rules, overlay tombstones, first-visibility metadata (`occupant_key`, `appearance_seq`), explicit empty-snapshot clears for overlay reset/disable/shutdown, restart/reconnect snapshot preservation, and the capped two-occupant logical visible set. Rust owns only the physical slot scene for those presenter-selected blocks: fixed slot anchors, hole retention, continuity by `occupant_key`, accent pulse state, and accent-aware damage clearing. Peer runtime churn continues to flow through `GuiController._peer_runtime_should_be_active()` and `PeerChannelRuntime.apply_policy()`; those changes do not clear overlay state, and peer `warmup()` is not a visibility boundary. This work intentionally bumps the overlay contract version so mixed Python/native builds fail fast instead of accepting an incompatible snapshot schema.

**Tech Stack:** Python 3.12, pytest, uv, direnv, Rust 2021, cargo, serde, Direct2D/DirectWrite, OpenVR

**Execution Constraints:** Work only in `/mnt/c/Users/salee/Documents/dev/puripuly_heart/.worktrees/overlay-active-self-promotion`. Use `direnv exec . env PYTHONPATH=. uv run ...` for Python commands. Use `cargo test --manifest-path native/overlay/Cargo.toml ...` for Rust commands. Do not amend previous commits; create a new commit after each task.

---

## File Map

- Modify: `src/puripuly_heart/core/overlay/protocol.py`
  - Extend snapshot block metadata with `occupant_key` and `appearance_seq`.
- Modify: `src/puripuly_heart/core/overlay/manifest.py`
  - Bump the Python-side overlay contract version to reject mixed schema builds.
- Modify: `src/puripuly_heart/core/overlay/sink.py`
  - Carry active-self occupant identity from hub into presenter.
- Modify: `src/puripuly_heart/core/orchestrator/hub.py`
  - Emit stable `occupant_key` for low-latency active-self preview based on merge identity.
- Modify: `src/puripuly_heart/core/overlay/presenter.py`
  - Keep the logical visible set capped to two occupants, assign `appearance_seq` only when a turn becomes visible, preserve `occupant_key`, tombstone displaced finalized turns so they do not re-enter, treat hidden-peer cancellation as never-visible, and publish monotonic empty snapshots only for explicit overlay reset/disable/shutdown clears.
- Modify: `src/puripuly_heart/ui/controller.py`
  - Preserve presenter state across overlay restart/reconnect, clear the live overlay through presenter-published empty snapshots only for explicit overlay disable/shutdown, and keep peer runtime policy changes (`_peer_runtime_should_be_active()` / `PeerChannelRuntime.apply_policy()`) from acting like overlay clears.
- Modify: `tests/core/test_overlay_protocol.py`
  - Lock down metadata round-trip and adapter behavior.
- Modify: `tests/core/test_overlay_manifest.py`
  - Lock down the contract version bump.
- Modify: `tests/core/test_hub_overlay_streaming.py`
  - Lock down hub `occupant_key` propagation and active-self/final merge identity.
- Modify: `tests/core/test_overlay_presenter.py`
  - Lock down presenter metadata, peer first-visibility, hidden-peer cancel-before-visible behavior, no-more-`last_updated_seq` trimming, and explicit-clear empty-snapshot behavior.
- Modify: `tests/ui/test_controller_branch_paths.py`
  - Lock down overlay restart snapshot preservation, explicit disable/shutdown empty-snapshot clears, and peer runtime policy changes that must not clear the overlay.
- Modify: `native/overlay/src/manifest.rs`
  - Bump the native-side expected contract version to match Python.
- Modify: `native/overlay/src/state.rs`
  - Replace entering/exiting strip scene with fixed two-slot state, mapping only presenter-selected blocks into stable physical slots with pulse timing.
- Modify: `native/overlay/src/runtime.rs`
  - Export fixed-slot caption blocks with no motion-derived visual state and drive redraws from accent pulse only.
- Modify: `native/overlay/src/lib.rs`
  - Keep crate exports aligned with the slot-state runtime after strip lifecycle removal.
- Modify: `native/overlay/src/renderer/types.rs`
  - Add slot anchor, accent rendering metadata, and resolved accent bounds to caption/layout structures.
- Modify: `native/overlay/src/renderer/layout.rs`
  - Stop stacking blocks by input order; use slot anchors from state/runtime.
- Modify: `native/overlay/src/renderer/backend.rs`
  - Draw the left accent bar, stop relying on animated block transforms, and widen damage clearing to cover accent chrome.
- Modify: `native/overlay/tests/state.rs`
  - Replace exiting/reflow tests with slot ownership, hole retention, and pulse tests.
- Modify: `native/overlay/tests/runtime.rs`
  - Replace motion assertions with fixed-slot, contract-version, and pulse-timing assertions.
- Modify: `native/overlay/tests/renderer.rs`
  - Lock down fixed slot layout, accent bar metadata, and accent-aware damage bounds.

---

### Task 1: Extend Overlay Snapshot Metadata And Active-Self Event Identity

**Files:**
- Modify: `src/puripuly_heart/core/overlay/protocol.py`
- Modify: `src/puripuly_heart/core/overlay/sink.py`
- Test: `tests/core/test_overlay_protocol.py`

- [ ] **Step 1: Write the failing protocol tests**

```python
from puripuly_heart.core.overlay.protocol import OverlayPresentationBlock
from puripuly_heart.core.overlay.sink import OverlayEventAdapter


def test_overlay_presentation_block_round_trips_occupant_metadata() -> None:
    block = OverlayPresentationBlock(
        id="self:1234",
        occupant_key="self:1234",
        appearance_seq=7,
        channel="self",
        block_variant="finalized",
        primary_text="hello",
        secondary_text="안녕",
        secondary_enabled=True,
    )

    encoded = block.to_dict()

    assert encoded["occupant_key"] == "self:1234"
    assert encoded["appearance_seq"] == 7
    assert OverlayPresentationBlock.from_dict(encoded) == block


def test_overlay_event_adapter_self_active_update_carries_occupant_key() -> None:
    adapter = OverlayEventAdapter()

    event = adapter.self_active_update(
        text="hello live",
        occupant_key="self:merge-1",
        created_at=11.0,
    )

    assert event.type == "self_active_update"
    assert event.occupant_key == "self:merge-1"
```

- [ ] **Step 2: Run the protocol tests to verify they fail**

Run:

```bash
direnv exec . env PYTHONPATH=. uv run pytest tests/core/test_overlay_protocol.py -q
```

Expected:

- FAIL because `OverlayPresentationBlock` does not accept `occupant_key` / `appearance_seq`
- FAIL because `OverlayEventAdapter.self_active_update()` does not accept `occupant_key`

- [ ] **Step 3: Implement the snapshot metadata and event field**

```python
@dataclass(frozen=True, slots=True)
class OverlayPresentationBlock:
    id: str
    occupant_key: str
    appearance_seq: int
    channel: ChannelId
    block_variant: BlockVariant
    primary_text: str
    secondary_text: str
    secondary_enabled: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "occupant_key": self.occupant_key,
            "appearance_seq": self.appearance_seq,
            "channel": self.channel,
            "block_variant": self.block_variant,
            "primary_text": self.primary_text,
            "secondary_text": self.secondary_text,
            "secondary_enabled": self.secondary_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "OverlayPresentationBlock":
        appearance_seq = int(data.get("appearance_seq"))
        if appearance_seq < 0:
            raise ValueError("appearance_seq must be non-negative")
        occupant_key = _require_string_field(data, "occupant_key")
        if not occupant_key.strip():
            raise ValueError("occupant_key must be non-empty")
        return cls(
            id=_require_string_field(data, "id"),
            occupant_key=occupant_key,
            appearance_seq=appearance_seq,
            channel=channel,
            block_variant=block_variant,
            primary_text=_require_string_field(data, "primary_text"),
            secondary_text=_require_string_field(data, "secondary_text"),
            secondary_enabled=_require_bool_field(data, "secondary_enabled"),
        )
```

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class SelfActiveUpdate(OverlayEvent):
    text: str
    occupant_key: str

    EVENT_TYPE: ClassVar[str] = "self_active_update"

    def __post_init__(self) -> None:
        if self.channel != "self":
            raise ValueError("SelfActiveUpdate requires channel='self'")
        if not self.occupant_key.strip():
            raise ValueError("SelfActiveUpdate requires non-empty occupant_key")


def self_active_update(
    self,
    *,
    text: str,
    occupant_key: str,
    created_at: float | None = None,
) -> SelfActiveUpdate:
    return SelfActiveUpdate(
        **self._common_event_fields(
            utterance_id=None,
            channel="self",
            created_at=created_at,
        ),
        text=text,
        occupant_key=occupant_key,
    )
```

- [ ] **Step 4: Run the protocol tests to verify they pass**

Run:

```bash
direnv exec . env PYTHONPATH=. uv run pytest tests/core/test_overlay_protocol.py -q
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add src/puripuly_heart/core/overlay/protocol.py \
        src/puripuly_heart/core/overlay/sink.py \
        tests/core/test_overlay_protocol.py
git commit -m "Add overlay occupant metadata plumbing"
```

---

### Task 2: Bump Overlay Contract Version For The Richer Snapshot Schema

**Files:**
- Modify: `src/puripuly_heart/core/overlay/manifest.py`
- Modify: `native/overlay/src/manifest.rs`
- Test: `tests/core/test_overlay_manifest.py`
- Test: `native/overlay/tests/runtime.rs`

- [ ] **Step 1: Write the failing manifest/runtime contract tests**

```python
from puripuly_heart.core.overlay.manifest import OVERLAY_CONTRACT_VERSION, OverlayLaunchManifest


def test_overlay_manifest_uses_contract_version_four() -> None:
    manifest = OverlayLaunchManifest(
        contract_version=OVERLAY_CONTRACT_VERSION,
        app_version="1.2.3",
        overlay_instance_id="overlay-1",
        bridge_url="ws://127.0.0.1:9000",
        session_token="token",
        parent_pid=1234,
        startup_deadline_ms=5000,
        log_dir="logs",
        log_level="info",
        locale="ko-KR",
        diagnostics_enabled=False,
    )

    assert OVERLAY_CONTRACT_VERSION == 4
    assert manifest.to_dict()["contract_version"] == 4
```

```rust
#[test]
fn check_startup_contract_reports_current_contract_version() {
    let payload = startup_ready_payload("overlay-test", "ws://127.0.0.1:9000", "token");
    assert_eq!(payload["contract_version"], 4);
}
```

- [ ] **Step 2: Run the manifest/runtime tests to verify they fail**

Run:

```bash
direnv exec . env PYTHONPATH=. uv run pytest tests/core/test_overlay_manifest.py -q
cargo test --manifest-path native/overlay/Cargo.toml contract_version -- --nocapture
```

Expected:

- FAIL because the Python manifest still advertises contract version `3`
- FAIL because the native runtime still reports/accepts contract version `3`

- [ ] **Step 3: Bump the contract version on both sides**

```python
OVERLAY_CONTRACT_VERSION = 4
```

```rust
pub const EXPECTED_CONTRACT_VERSION: u32 = 4;
```

- [ ] **Step 4: Run the manifest/runtime tests to verify they pass**

Run:

```bash
direnv exec . env PYTHONPATH=. uv run pytest tests/core/test_overlay_manifest.py -q
cargo test --manifest-path native/overlay/Cargo.toml contract_version -- --nocapture
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add src/puripuly_heart/core/overlay/manifest.py \
        native/overlay/src/manifest.rs \
        tests/core/test_overlay_manifest.py \
        native/overlay/tests/runtime.rs
git commit -m "Bump overlay contract version for slot snapshots"
```

---

### Task 3: Propagate Stable Active-Self Occupant Keys From Hub

**Files:**
- Modify: `src/puripuly_heart/core/orchestrator/hub.py`
- Test: `tests/core/test_hub_overlay_streaming.py`

- [ ] **Step 1: Write the failing hub tests**

```python
@pytest.mark.asyncio
async def test_low_latency_self_final_emits_active_update_with_merge_occupant_key() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
    )
    utterance_id = uuid4()

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello live",
                is_final=True,
                created_at=11.0,
            ),
        )
    )

    active_event = sink.events[-1]
    assert active_event.type == "self_active_update"
    assert active_event.occupant_key == f"self:{hub._merge_buffer.merge_id}"


@pytest.mark.asyncio
async def test_low_latency_merge_commit_reuses_merge_identity_for_active_and_final() -> None:
    sink = RecordingOverlaySink()
    hub = ClientHub(
        stt=None,
        llm=None,
        osc=RecordingOscQueue(),
        overlay_sink=sink,
        clock=FakeClock(_now=10.0),
        low_latency_mode=True,
        low_latency_finalize_wait_ms=0,
    )
    utterance_id = uuid4()

    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello live",
                is_final=True,
                created_at=11.0,
            ),
        )
    )
    active_occupant_key = sink.events[-1].occupant_key
    await hub.handle_vad_event(SpeechEnd(utterance_id))
    await hub._handle_stt_event(
        STTFinalEvent(
            utterance_id=utterance_id,
            transcript=Transcript(
                utterance_id=utterance_id,
                text="hello live",
                is_final=True,
                created_at=12.0,
            ),
        )
    )

    final_event = next(event for event in sink.events if event.type == "self_transcript_final")
    assert active_occupant_key == f"self:{final_event.utterance_id}"
```

- [ ] **Step 2: Run the hub tests to verify they fail**

Run:

```bash
direnv exec . env PYTHONPATH=. uv run pytest tests/core/test_hub_overlay_streaming.py -k "merge_identity or occupant_key" -q
```

Expected:

- FAIL because `self_active_update` events do not have `occupant_key`

- [ ] **Step 3: Thread the merge identity into active-self events**

```python
def _active_self_occupant_key(self, buffer: _MergeBuffer) -> str:
    return f"self:{buffer.merge_id}"


async def _sync_overlay_active_self(
    self, buffer: _MergeBuffer | None, *, created_at: float | None = None
) -> None:
    if self.overlay_sink is None or buffer is None:
        return

    active_text = self._merge_text(buffer.parts)
    if not active_text or active_text == self._overlay_active_self_text:
        return

    await self._emit_overlay_active_self_event(
        self.overlay_event_adapter.self_active_update(
            text=active_text,
            occupant_key=self._active_self_occupant_key(buffer),
            created_at=created_at,
        )
    )
```

```python
async def _commit_merge(self, buffer: _MergeBuffer, *, reason: str) -> None:
    final_text = self._merge_text(buffer.parts)
    if not final_text:
        await self.reset_overlay_preview()
        return

    transcript = Transcript(
        utterance_id=buffer.merge_id,
        text=final_text,
        is_final=True,
        created_at=self.clock.now(),
    )
    self._overlay_active_self_text = None
    await self._handle_transcript(transcript, is_final=True, source="Mic")
```

- [ ] **Step 4: Run the focused hub tests to verify they pass**

Run:

```bash
direnv exec . env PYTHONPATH=. uv run pytest tests/core/test_hub_overlay_streaming.py -k "merge_identity or occupant_key or promotes_active_self_without_emitting_clear" -q
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add src/puripuly_heart/core/orchestrator/hub.py \
        tests/core/test_hub_overlay_streaming.py
git commit -m "Propagate active self occupant keys from hub"
```

---

### Task 4: Rewrite Presenter Logical Visible-Set Ownership Around `occupant_key` And `appearance_seq`

**Files:**
- Modify: `src/puripuly_heart/core/overlay/presenter.py`
- Modify: `src/puripuly_heart/ui/controller.py`
- Test: `tests/core/test_overlay_presenter.py`
- Test: `tests/ui/test_controller_branch_paths.py`

- [ ] **Step 1: Write the failing presenter tests**

```python
@pytest.mark.asyncio
async def test_presenter_keeps_active_self_and_matching_final_on_same_occupant_key() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    utterance_id = uuid4()

    await presenter.emit(
        adapter.self_active_update(
            text="hello live",
            occupant_key=f"self:{utterance_id}",
            created_at=10.0,
        )
    )
    await presenter.emit(
        SelfTranscriptFinal(
            event_id="final-1",
            seq=2,
            utterance_id=utterance_id,
            channel="self",
            text="hello live",
            source_language="ko",
            target_language="en",
            created_at=11.0,
        )
    )

    blocks = presenter.snapshot().blocks
    assert len(blocks) == 1
    assert blocks[0].occupant_key == f"self:{utterance_id}"
    assert blocks[0].block_variant == "finalized"


@pytest.mark.asyncio
async def test_presenter_does_not_reorder_existing_turn_when_translation_updates() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    first_peer = Transcript(utterance_id=uuid4(), channel="peer", text="peer one", is_final=True, created_at=11.0)
    second_self = Transcript(utterance_id=uuid4(), channel="self", text="self two", is_final=True, created_at=12.0)

    await presenter.emit(adapter.transcript_final(first_peer, source_language="en", target_language="ko"))
    await presenter.emit(
        adapter.translation_final(
            utterance_id=first_peer.utterance_id,
            channel="peer",
            text="피어 하나",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=12.5,
        )
    )
    await presenter.emit(adapter.transcript_final(second_self, source_language="ko", target_language="en"))
    first_order = presenter.snapshot().blocks[0].appearance_seq

    await presenter.emit(
        adapter.translation_final(
            utterance_id=first_peer.utterance_id,
            channel="peer",
            text="피어 하나 수정",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=13.0,
        )
    )

    assert presenter.snapshot().blocks[0].appearance_seq == first_order
    assert [block.occupant_key for block in presenter.snapshot().blocks] == [
        f"peer:{first_peer.utterance_id}",
        f"self:{second_self.utterance_id}",
    ]


@pytest.mark.asyncio
async def test_presenter_displaces_oldest_finalized_turn_and_tombstones_it() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))

    first = Transcript(utterance_id=uuid4(), channel="self", text="one", is_final=True, created_at=11.0)
    second = Transcript(utterance_id=uuid4(), channel="self", text="two", is_final=True, created_at=12.0)
    third = Transcript(utterance_id=uuid4(), channel="self", text="three", is_final=True, created_at=13.0)

    for transcript in (first, second, third):
        await presenter.emit(adapter.transcript_final(transcript, source_language="ko", target_language="en"))

    assert [block.primary_text for block in presenter.snapshot().blocks] == ["two", "three"]

    await presenter.emit(
        adapter.translation_final(
            utterance_id=first.utterance_id,
            channel="self",
            text="하나",
            source_language="ko",
            target_language="en",
            applied_context_mode=None,
            created_at=14.0,
        )
    )

    assert [block.primary_text for block in presenter.snapshot().blocks] == ["two", "three"]
    assert all(
        block.occupant_key != f"self:{first.utterance_id}"
        for block in presenter.snapshot().blocks
    )


@pytest.mark.asyncio
async def test_presenter_assigns_peer_appearance_seq_on_first_visible_translation() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    peer_turn = Transcript(
        utterance_id=uuid4(),
        channel="peer",
        text="peer one",
        is_final=True,
        created_at=11.0,
    )

    await presenter.emit(
        adapter.transcript_final(peer_turn, source_language="en", target_language="ko")
    )
    assert presenter.snapshot().blocks == []

    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_turn.utterance_id,
            channel="peer",
            text="피어 하나",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=12.0,
        )
    )
    first_visible = presenter.snapshot().blocks[0]

    await presenter.emit(
        adapter.translation_final(
            utterance_id=peer_turn.utterance_id,
            channel="peer",
            text="피어 하나 수정",
            source_language="en",
            target_language="ko",
            applied_context_mode=None,
            created_at=13.0,
        )
    )

    assert presenter.snapshot().blocks[0].occupant_key == f"peer:{peer_turn.utterance_id}"
    assert presenter.snapshot().blocks[0].appearance_seq == first_visible.appearance_seq


@pytest.mark.asyncio
async def test_presenter_hidden_peer_cancel_before_first_visibility_never_assigns_metadata() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    peer_turn = Transcript(
        utterance_id=uuid4(),
        channel="peer",
        text="peer one",
        is_final=True,
        created_at=11.0,
    )

    await presenter.emit(
        adapter.transcript_final(peer_turn, source_language="en", target_language="ko")
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=peer_turn.utterance_id,
            channel="peer",
            created_at=11.5,
            is_final=False,
        )
    )

    assert presenter.snapshot().blocks == []
    assert bridge.snapshots[-1].blocks == []


@pytest.mark.asyncio
async def test_presenter_clear_for_runtime_detach_publishes_empty_snapshot_with_higher_revision() -> None:
    bridge = RecordingPresentationBridge()
    presenter = OverlayPresenter(bridge=bridge, calibration=OverlayCalibration())
    adapter = OverlayEventAdapter(clock=FakeClock(_now=10.0))
    utterance_id = uuid4()

    await presenter.emit(
        adapter.transcript_final(
            Transcript(
                utterance_id=utterance_id,
                channel="self",
                text="hello",
                is_final=True,
                created_at=11.0,
            ),
            source_language="ko",
            target_language="en",
        )
    )
    revision_before_clear = presenter.snapshot().revision

    await presenter.clear_for_runtime_detach()

    assert presenter.snapshot().blocks == []
    assert presenter.snapshot().revision == revision_before_clear + 1
    assert bridge.snapshots[-1].blocks == []


@pytest.mark.asyncio
async def test_overlay_restart_reuses_presenter_scene_for_new_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)
    FakeOverlayProcessManager.instances[0].complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    presenter = controller._overlay_presenter
    bridge = FakeOverlayBridge.instances[0]
    assert presenter is not None

    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=1,
            utterance_id=uuid4(),
            channel="self",
            created_at=10.0,
            text="persist me",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )
    saved_snapshot = presenter.snapshot()

    await controller._teardown_overlay_runtime(preserve_presenter_state=True)

    assert bridge.snapshots[-1] == saved_snapshot
    assert controller._overlay_presenter is presenter


@pytest.mark.asyncio
async def test_controller_shutdown_clears_overlay_with_empty_snapshot_before_detach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)
    manager = FakeOverlayProcessManager.instances[0]
    manager.complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    presenter = controller._overlay_presenter
    bridge = FakeOverlayBridge.instances[0]
    assert presenter is not None

    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=1,
            utterance_id=uuid4(),
            channel="self",
            created_at=10.0,
            text="discard me",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )

    await controller.set_overlay_enabled(False)

    assert bridge.shutdown_calls == 1
    assert bridge.snapshots[-1].blocks == []
    assert controller._overlay_presenter is None


@pytest.mark.asyncio
async def test_peer_runtime_policy_refresh_does_not_clear_overlay_scene(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_overlay_runtime(monkeypatch)
    monkeypatch.setattr(GuiController, "_save_settings", lambda self: None)

    controller = _make_controller(app=SimpleNamespace())
    controller.settings = AppSettings()
    controller.hub = DummyHub(peer_stt=object())
    controller._peer_runtime = DummyPeerRuntime()

    await controller.set_overlay_enabled(True)
    await _wait_until(lambda: len(FakeOverlayProcessManager.instances) == 1)
    FakeOverlayProcessManager.instances[0].complete_startup()
    await _wait_until(lambda: controller.overlay_state == "connected")

    presenter = controller._overlay_presenter
    bridge = FakeOverlayBridge.instances[0]
    assert presenter is not None

    await presenter.emit(
        SelfTranscriptFinal(
            event_id="self-final",
            seq=1,
            utterance_id=uuid4(),
            channel="self",
            created_at=10.0,
            text="stay visible",
            source_language="ko",
            target_language="en",
            is_final=True,
        )
    )
    saved_snapshot = bridge.snapshots[-1]

    await controller._refresh_overlay_runtime_dependencies()

    assert controller._peer_runtime.policy_calls[-1]["desired_active"] is True
    assert bridge.snapshots[-1] == saved_snapshot
```

- [ ] **Step 2: Run the presenter tests to verify they fail**

Run:

```bash
direnv exec . env PYTHONPATH=. uv run pytest tests/core/test_overlay_presenter.py -q
```

Expected:

- FAIL because blocks do not carry `occupant_key` / `appearance_seq`
- FAIL because presenter does not tombstone displaced finalized turns
- FAIL because peer first-visible translation does not yet own `appearance_seq`
- FAIL because hidden peer cancel-before-visible still has no explicit metadata contract
- FAIL because presenter has no explicit empty-snapshot clear path for disable/shutdown
- FAIL because controller teardown does not distinguish restart-preserve from explicit clear
- FAIL because peer runtime policy refresh still has no no-clear regression coverage

- [ ] **Step 3: Rewrite presenter metadata and publishability ordering**

```python
@dataclass(slots=True)
class _LogicalCaptionEntry:
    channel: str
    utterance_id: UUID
    original_text: str = ""
    translation_text: str = ""
    occupant_key: str = ""
    appearance_seq: int | None = None
    ever_publishable: bool = False
    visible_since: float | None = None
    last_updated_seq: int = 0
    closed_seq: int | None = None
    closed_at: float | None = None


@dataclass(slots=True)
class _ActiveSelfEntry:
    text: str
    last_updated_seq: int
    occupant_key: str
    appearance_seq: int
```

```python
def _finalized_occupant_key(self, channel: str, utterance_id: UUID) -> str:
    return f"{channel}:{utterance_id}"


def _next_appearance_seq(self) -> int:
    self._appearance_seq += 1
    return self._appearance_seq


def _ensure_entry_visibility_metadata(
    self,
    entry: _LogicalCaptionEntry,
    *,
    occupant_key: str,
) -> None:
    if not entry.occupant_key:
        entry.occupant_key = occupant_key
    if entry.appearance_seq is None:
        entry.appearance_seq = self._next_appearance_seq()
```

```python
if isinstance(event, SelfActiveUpdate):
    if self._active_self is not None and event.seq < self._active_self.last_updated_seq:
        return False
    appearance_seq = (
        self._active_self.appearance_seq
        if self._active_self is not None and self._active_self.occupant_key == event.occupant_key
        else self._next_appearance_seq()
    )
    self._active_self = _ActiveSelfEntry(
        text=event.text,
        last_updated_seq=event.seq,
        occupant_key=event.occupant_key,
        appearance_seq=appearance_seq,
    )
    return True
```

```python
def _logical_visible_entry_keys(self) -> list[tuple[str, UUID]]:
    finalized_limit = self.visible_window_target_blocks
    if self._active_self is not None and self._active_self.text:
        finalized_limit = max(finalized_limit - 1, 0)

    publishable: list[tuple[int, str, tuple[str, UUID]]] = []
    for key, entry in self._entries.items():
        if not self._entry_is_publishable(entry):
            continue
        self._ensure_entry_visibility_metadata(
            entry,
            occupant_key=self._finalized_occupant_key(entry.channel, entry.utterance_id),
        )
        publishable.append((entry.appearance_seq or 0, entry.occupant_key, key))

    publishable.sort(key=lambda item: (item[0], item[1]))
    return [key for _, _, key in publishable[-finalized_limit:]]


def _prune_displaced_finalized_entries(
    self,
    visible_entry_keys: set[tuple[str, UUID]],
) -> None:
    displaced_keys = [
        key
        for key, entry in self._entries.items()
        if self._entry_is_publishable(entry) and key not in visible_entry_keys
    ]
    for key in displaced_keys:
        entry = self._entries.get(key)
        if entry is None:
            continue
        self._remove_entry(key, tombstone_seq=entry.last_updated_seq)


def _visible_blocks(self) -> list[OverlayPresentationBlock]:
    self._expire_closed_entries(now=self.clock.now())
    visible_entry_keys = self._logical_visible_entry_keys()
    self._prune_displaced_finalized_entries(set(visible_entry_keys))
    blocks = [
        block
        for key in visible_entry_keys
        if (block := self._build_presentation_block(self._entries[key])) is not None
    ]

    if self._active_self is not None and self._active_self.text:
        blocks.append(
            OverlayPresentationBlock(
                id=_ACTIVE_SELF_BLOCK_ID,
                occupant_key=self._active_self.occupant_key,
                appearance_seq=self._active_self.appearance_seq,
                channel="self",
                block_variant="active_self",
                primary_text=self._active_self.text,
                secondary_text="",
                secondary_enabled=self.show_translation,
            )
        )

    blocks.sort(key=lambda block: (block.appearance_seq, block.occupant_key))
    return blocks[-self.visible_window_target_blocks :]
```

```python
def _refresh_entry_visibility_and_expiration(
    self,
    key: tuple[str, UUID],
    entry: _LogicalCaptionEntry,
    *,
    now: float,
) -> None:
    if self._entry_is_publishable(entry):
        self._ensure_entry_visibility_metadata(
            entry,
            occupant_key=self._finalized_occupant_key(entry.channel, entry.utterance_id),
        )
        entry.ever_publishable = True
        if entry.visible_since is None:
            entry.visible_since = now
    if entry.closed_seq is not None:
        self._schedule_expiration(key, entry)


async def _shutdown_overlay_runtime(self, *, preserve_failure_reason: bool) -> None:
    ...
    await self._emit_overlay_shutdown()
    await self._teardown_overlay_runtime(preserve_presenter_state=False)
    ...


async def clear_for_runtime_detach(self) -> None:
    self._cancel_all_expiration_tasks()
    self._entries.clear()
    self._closed_tombstones.clear()
    self._active_self = None
    self._revision += 1
    self._snapshot = OverlayPresentationSnapshot(
        revision=self._revision,
        calibration=_calibration_from_overlay(self.calibration),
        blocks=[],
    )
    if self.bridge is not None:
        await self.bridge.replace_snapshot(self._snapshot)


def _remove_entry(
    self,
    key: tuple[str, UUID],
    *,
    current_task: asyncio.Task[None] | None = None,
    tombstone_seq: int | None = None,
) -> None:
    self._cancel_expiration_task(key, current_task=current_task)
    entry = self._entries.pop(key, None)
    if entry is None:
        return
    seq = tombstone_seq if tombstone_seq is not None else entry.closed_seq
    if seq is not None:
        self._remember_tombstone(key, seq)
```

```python
async def _teardown_overlay_runtime(self, *, preserve_presenter_state: bool) -> None:
    ...
    presenter = self._overlay_presenter
    if not preserve_presenter_state and presenter is not None and presenter.bridge is not None:
        with contextlib.suppress(Exception):
            await presenter.clear_for_runtime_detach()
    if presenter is not None:
        presenter.detach_bridge()
    ...
    if not preserve_presenter_state and presenter is not None:
        presenter.reset_scene()
        self._overlay_presenter = None
```

```python
async def _refresh_peer_stt_runtime(self) -> None:
    if self.settings is None or self.hub is None or self._peer_runtime is None:
        return

    config = self._build_peer_runtime_config(self.settings)
    desired_active = self._peer_runtime_should_be_active(self.settings)
    await self._peer_runtime.apply_policy(config=config, desired_active=desired_active)
    if desired_active:
        with contextlib.suppress(Exception):
            await self._peer_runtime.warmup()
```

- [ ] **Step 4: Run the presenter tests to verify they pass**

Run:

```bash
direnv exec . env PYTHONPATH=. uv run pytest tests/core/test_overlay_presenter.py -q
direnv exec . env PYTHONPATH=. uv run pytest \
  tests/ui/test_controller_branch_paths.py::test_overlay_restart_reuses_presenter_scene_for_new_bridge \
  tests/ui/test_controller_branch_paths.py::test_explicit_overlay_disable_resets_presenter_scene_for_next_session \
  tests/ui/test_controller_branch_paths.py::test_overlay_toggle_off_sends_shutdown_event_before_teardown \
  tests/ui/test_controller_branch_paths.py::test_controller_shutdown_clears_overlay_with_empty_snapshot_before_detach \
  tests/ui/test_controller_branch_paths.py::test_peer_runtime_policy_refresh_does_not_clear_overlay_scene \
  tests/ui/test_controller_branch_paths.py::test_refresh_overlay_runtime_dependencies_applies_peer_runtime_policy \
  tests/ui/test_controller_branch_paths.py::test_refresh_overlay_runtime_dependencies_disables_peer_runtime_when_overlay_fails \
  -q
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add src/puripuly_heart/core/overlay/presenter.py \
        src/puripuly_heart/ui/controller.py \
        tests/core/test_overlay_presenter.py \
        tests/ui/test_controller_branch_paths.py
git commit -m "Restore presenter overlay ownership and clear semantics"
```

---

### Task 5: Replace Native Strip Lifecycle With Fixed Two-Slot Scene State

**Files:**
- Modify: `native/overlay/src/state.rs`
- Modify: `native/overlay/src/runtime.rs`
- Modify: `native/overlay/src/lib.rs`
- Test: `native/overlay/tests/state.rs`

- [ ] **Step 1: Write the failing native state tests**

```rust
fn block(
    id: &str,
    occupant_key: &str,
    appearance_seq: u64,
    channel: &str,
    primary_text: &str,
    secondary_text: &str,
    secondary_enabled: bool,
) -> OverlayPresentationBlock {
    OverlayPresentationBlock {
        id: id.to_string(),
        occupant_key: occupant_key.to_string(),
        appearance_seq,
        channel: channel.to_string(),
        block_variant: OverlayPresentationBlockVariant::Finalized,
        primary_text: primary_text.to_string(),
        secondary_text: secondary_text.to_string(),
        secondary_enabled,
    }
}

#[test]
fn overlay_state_keeps_slot_two_anchor_when_slot_one_disappears() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self:1", 1, "self", "one", "", true),
            block("peer:2", "peer:2", 2, "peer", "two", "", true),
        ],
    }));

    let second_top = state.scene().slots()[1].as_ref().unwrap().anchor_top_px;

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:2", "peer:2", 2, "peer", "two", "", true)],
    }));

    assert!(state.scene().slots()[0].is_none());
    assert_eq!(
        state.scene().slots()[1].as_ref().unwrap().anchor_top_px,
        second_top
    );
}

#[test]
fn overlay_state_promotes_matching_occupant_key_without_replaying_pulse() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![OverlayPresentationBlock {
            id: "self:active".into(),
            occupant_key: "self:merge-1".into(),
            appearance_seq: 1,
            channel: "self".into(),
            block_variant: OverlayPresentationBlockVariant::ActiveSelf,
            primary_text: "hello live".into(),
            secondary_text: String::new(),
            secondary_enabled: true,
        }],
    }));
    let started = state.scene().slots()[0].as_ref().unwrap().accent_started_at_s;

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:merge-1", "self:merge-1", 1, "self", "hello live", "", true)],
    }));

    let slot = state.scene().slots()[0].as_ref().unwrap();
    assert_eq!(slot.occupant_key, "self:merge-1");
    assert_eq!(slot.accent_started_at_s, started);
}
```

- [ ] **Step 2: Run the native state tests to verify they fail**

Run:

```bash
cargo test --manifest-path native/overlay/Cargo.toml --lib state -- --nocapture
cargo test --manifest-path native/overlay/Cargo.toml --test state -- --nocapture
```

Expected:

- FAIL because `OverlayPresentationBlock` does not have `occupant_key` / `appearance_seq`
- FAIL because `OverlayScene` does not expose fixed slots or `anchor_top_px`
- FAIL because runtime/lib still depend on strip-era state APIs

- [ ] **Step 3: Replace strip lifecycle state with fixed slots for presenter-selected blocks**

```rust
const ACCENT_PULSE_DURATION_SECONDS: f32 = 0.12;
const SLOT_COUNT: usize = 2;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OverlayPresentationBlock {
    pub id: String,
    pub occupant_key: String,
    pub appearance_seq: u64,
    pub channel: String,
    pub block_variant: OverlayPresentationBlockVariant,
    pub primary_text: String,
    #[serde(default)]
    pub secondary_text: String,
    #[serde(default = "default_secondary_enabled")]
    pub secondary_enabled: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OverlaySlot {
    pub slot_index: usize,
    pub anchor_top_px: f32,
    pub id: String,
    pub occupant_key: String,
    pub appearance_seq: u64,
    pub channel: String,
    pub block_variant: OverlayPresentationBlockVariant,
    pub primary_text: String,
    pub secondary_text: String,
    pub secondary_enabled: bool,
    pub slot_entry_order: u64,
    pub accent_started_at_s: Option<f32>,
    pub accent_progress: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OverlayScene {
    slots: [Option<OverlaySlot>; SLOT_COUNT],
    next_slot_entry_order: u64,
}
```

```rust
fn assign_snapshot(&mut self, blocks: &[OverlayPresentationBlock], text_scale: f32) {
    let mut sorted = blocks.iter().cloned().collect::<Vec<_>>();
    sorted.sort_by(|left, right| {
        left.appearance_seq
            .cmp(&right.appearance_seq)
            .then_with(|| left.occupant_key.cmp(&right.occupant_key))
    });
    debug_assert!(sorted.len() <= SLOT_COUNT, "presenter must cap visible blocks to two");

    self.update_existing_slots(&sorted, text_scale);
    self.clear_missing_slots(&sorted);
    self.fill_empty_slots(&sorted, text_scale);
    self.recompute_slot_anchors(text_scale);
}

fn pulse_for_new_occupant(slot: &mut OverlaySlot) {
    slot.accent_started_at_s = Some(0.0);
    slot.accent_progress = 0.0;
}
```

```rust
fn has_active_animation(&self) -> bool {
    self.scene
        .slots()
        .iter()
        .flatten()
        .any(|slot| slot.accent_progress < 1.0)
}

fn sample_animations(&mut self, delta_seconds: f32, _sample_rate_hz: u32) -> bool {
    let previous = self.scene.clone();
    for slot in self.scene.slots_mut().iter_mut().flatten() {
        if slot.accent_progress < 1.0 {
            slot.accent_progress =
                (slot.accent_progress + delta_seconds / ACCENT_PULSE_DURATION_SECONDS).min(1.0);
        }
    }
    previous != self.scene
}
```

- [ ] **Step 4: Run the state tests to verify they pass**

Run:

```bash
cargo test --manifest-path native/overlay/Cargo.toml --lib state -- --nocapture
cargo test --manifest-path native/overlay/Cargo.toml --test state -- --nocapture
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add native/overlay/src/state.rs \
        native/overlay/src/runtime.rs \
        native/overlay/src/lib.rs \
        native/overlay/tests/state.rs
git commit -m "Replace strip lifecycle with fixed slot state"
```

---

### Task 6: Render Fixed Slot Anchors And One-Shot Accent Bars

**Files:**
- Modify: `native/overlay/src/runtime.rs`
- Modify: `native/overlay/src/renderer/types.rs`
- Modify: `native/overlay/src/renderer/layout.rs`
- Modify: `native/overlay/src/renderer/backend.rs`
- Test: `native/overlay/tests/runtime.rs`
- Test: `native/overlay/tests/renderer.rs`

- [ ] **Step 1: Write the failing runtime and renderer tests**

```rust
#[test]
fn runtime_keeps_slot_two_top_fixed_when_slot_one_secondary_changes() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self:1", 1, "self", "one", "", true),
            block("peer:2", "peer:2", 2, "peer", "two", "", true),
        ],
    });
    let first = runtime.caption_blocks();

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self:1", 1, "self", "one", "번역", true),
            block("peer:2", "peer:2", 2, "peer", "two", "", true),
        ],
    });

    let second = runtime.caption_blocks();
    assert_eq!(first[1].slot_top_px, second[1].slot_top_px);
}

#[test]
fn renderer_uses_slot_top_px_instead_of_stacking_input_order() {
    let policy = CaptionLayoutPolicy::default();
    let layout = policy.layout_blocks(
        vec![
            CaptionBlock::new("peer:2", "two")
                .with_slot(1, 420.0)
                .with_channel(CaptionChannel::PeerChannel),
            CaptionBlock::new("self:1", "one")
                .with_slot(0, 40.0)
                .with_channel(CaptionChannel::SelfChannel),
        ],
        3840,
        1024,
    );

    assert_eq!(layout.visible_blocks[0].bounds.top_px, 420.0);
    assert_eq!(layout.visible_blocks[1].bounds.top_px, 40.0);
}

#[test]
fn renderer_damage_bounds_include_accent_bar_extent() {
    let policy = CaptionLayoutPolicy::default();
    let layout = policy.layout_blocks(
        vec![CaptionBlock::new("self:1", "one")
            .with_slot(0, 40.0)
            .with_channel(CaptionChannel::SelfChannel)
            .with_accent_opacity(1.0)],
        3840,
        1024,
    );

    let block = &layout.visible_blocks[0];
    assert!(block.visual_bounds.left_px <= block.bounds.left_px);
}
```

- [ ] **Step 2: Run the native runtime and renderer tests to verify they fail**

Run:

```bash
cargo test --manifest-path native/overlay/Cargo.toml --test runtime --test renderer -- --nocapture
```

Expected:

- FAIL because `CaptionBlock` does not carry slot anchor data
- FAIL because layout still stacks blocks incrementally and runtime still derives motion state
- FAIL because accent chrome is not included in resolved bounds / damage clearing

- [ ] **Step 3: Add fixed-slot caption fields and accent bar rendering**

```rust
pub(crate) const SLOT_ACCENT_WIDTH_PX: f32 = 6.0;
pub(crate) const SELF_SLOT_ACCENT_COLOR: (f32, f32, f32, f32) =
    (195.0 / 255.0, 206.0 / 255.0, 218.0 / 255.0, 1.0);
pub(crate) const PEER_SLOT_ACCENT_COLOR: (f32, f32, f32, f32) =
    (210.0 / 255.0, 162.0 / 255.0, 79.0 / 255.0, 1.0);

#[derive(Debug, Clone, PartialEq)]
pub struct CaptionBlock {
    pub id: String,
    pub primary_text: String,
    pub secondary_text: String,
    pub secondary_enabled: bool,
    pub block_variant: CaptionBlockVariant,
    pub channel: Option<CaptionChannel>,
    pub opacity: f32,
    pub offset_y_px: f32,
    pub height_scale: f32,
    pub slot_index: usize,
    pub slot_top_px: f32,
    pub accent_opacity: f32,
}

impl CaptionBlock {
    pub fn with_slot(mut self, slot_index: usize, slot_top_px: f32) -> Self {
        self.slot_index = slot_index;
        self.slot_top_px = slot_top_px;
        self
    }

    pub fn with_accent_opacity(mut self, accent_opacity: f32) -> Self {
        self.accent_opacity = accent_opacity.clamp(0.0, 1.0);
        self
    }
}
```

```rust
pub fn caption_blocks(&self) -> Vec<CaptionBlock> {
    self.state
        .scene()
        .slots()
        .iter()
        .flatten()
        .map(|slot| {
            CaptionBlock::new(slot.id.clone(), slot.primary_text.clone())
                .with_channel(slot_channel(slot))
                .with_variant(slot_variant(slot))
                .with_secondary_text(slot.secondary_text.clone(), slot.secondary_enabled)
                .with_visual_state(1.0, 0.0, 1.0)
                .with_slot(slot.slot_index, slot.anchor_top_px)
                .with_accent_opacity(slot.accent_opacity())
        })
        .collect()
}
```

```rust
fn resolve_blocks_for_presentation_fallback(...) -> ResolvedFrameLayout {
    let strip_left_px = self.horizontal_padding_px as f32;
    let mut resolved_blocks = Vec::with_capacity(blocks.len());

    for block in blocks {
        let layout_cache_key = layout_cache_key_for_block(&block, content_width_px, text_scale);
        let template = self.build_fallback_block_template(&block, content_width_px, text_scale);
        resolved_blocks.push(materialize_resolved_block_layout(
            &block,
            layout_cache_key,
            &template,
            strip_left_px,
            block.slot_top_px,
        ));
    }
```

```rust
pub struct ResolvedBlockLayout {
    ...
    pub accent_opacity: f32,
    pub accent_bounds: Option<PixelRect>,
}

let accent_bounds = if block.accent_opacity > f32::EPSILON {
    Some(PixelRect::new(
        block.bounds.left_px,
        block.bounds.top_px,
        block.bounds.left_px + SLOT_ACCENT_WIDTH_PX,
        block.bounds.bottom_px,
    ))
} else {
    None
};
```

```rust
if block.accent_opacity > f32::EPSILON {
    let accent_color = match block.channel {
        Some(CaptionChannel::PeerChannel) => PEER_SLOT_ACCENT_COLOR,
        _ => SELF_SLOT_ACCENT_COLOR,
    };
    self.draw_accent_bar(
        block.bounds.left_px,
        block.bounds.top_px,
        block.bounds.bottom_px,
        SLOT_ACCENT_WIDTH_PX,
        accent_color,
        block.accent_opacity,
    )?;
}
```

```rust
fn damage_band_for_block(block: &ResolvedBlockLayout) -> DamageBand {
    let mut bounds = block.visual_bounds;
    if let Some(accent_bounds) = block.accent_bounds {
        bounds = bounds.union(accent_bounds);
    }
    DamageBand::from_rect(bounds)
}
```

- [ ] **Step 4: Run the native runtime and renderer tests to verify they pass**

Run:

```bash
cargo test --manifest-path native/overlay/Cargo.toml --test runtime --test renderer -- --nocapture
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add native/overlay/src/runtime.rs \
        native/overlay/src/renderer/types.rs \
        native/overlay/src/renderer/layout.rs \
        native/overlay/src/renderer/backend.rs \
        native/overlay/tests/runtime.rs \
        native/overlay/tests/renderer.rs
git commit -m "Render fixed overlay slots with accent-safe damage"
```

---

### Task 7: Run Full Regression Verification And Update Evidence

**Files:**
- Modify: `agents/logs/2026-04-04-overlay-two-slot-static-caption-implementation-l3.md`

- [ ] **Step 1: Run the focused Python regression suite**

Run:

```bash
direnv exec . env PYTHONPATH=. uv run pytest \
  tests/core/test_overlay_protocol.py \
  tests/core/test_overlay_manifest.py \
  tests/core/test_overlay_bridge.py \
  tests/core/test_hub_overlay_streaming.py \
  tests/core/test_overlay_presenter.py \
  tests/core/test_hub_low_latency.py \
  tests/core/test_peer_channel_routing.py \
  tests/core/test_peer_channel_runtime.py \
  tests/ui/test_controller_branch_paths.py \
  -q
```

Expected:

- PASS

- [ ] **Step 2: Run the native regression suite**

Run:

```bash
cargo test --manifest-path native/overlay/Cargo.toml -- --nocapture
```

Expected:

- PASS

- [ ] **Step 3: Run the Windows/headset manual smoke checklist**

Manual:

1. Launch the app from the worktree using the normal local overlay startup flow.
2. Connect the native overlay runtime and speak one self turn that promotes from active to final.
3. Trigger a peer turn that first becomes visible when translation arrives.
4. Let one slot expire while the other remains visible.
5. Shut the overlay down from the UI.

Expected:

- Slot positions never jump vertically when text finalizes, translations attach, or one slot expires.
- `active_self -> finalized` shows no extra accent pulse and no empty-frame clear.
- Peer first-visible translation triggers exactly one accent pulse.
- The 6px accent bar disappears cleanly with no visible ghosting.
- Overlay runtime restart/reconnect seeds the next bridge from the saved presenter snapshot without an injected empty clear.
- Explicit overlay disable/shutdown leaves the overlay empty before disconnect.
- Peer runtime policy churn and warmup do not clear visible slots by themselves.

- [ ] **Step 4: Write the verification evidence note**

```markdown
## Verification

- Level: L3
- Scope: overlay two-slot static caption rewrite

### Commands run

1. `direnv exec . env PYTHONPATH=. uv run pytest tests/core/test_overlay_protocol.py tests/core/test_overlay_manifest.py tests/core/test_overlay_bridge.py tests/core/test_hub_overlay_streaming.py tests/core/test_overlay_presenter.py tests/core/test_hub_low_latency.py tests/ui/test_controller_branch_paths.py -q`
   - PASS
2. `cargo test --manifest-path native/overlay/Cargo.toml -- --nocapture`
   - PASS
3. Windows/headset manual smoke
   - PASS

### Outcome

- PASS

### Skipped

- None

### Notes

- Overlay contract version is now `4`; mixed Python/native builds intentionally fail fast.
- Active-self continuity is now keyed by explicit `occupant_key`.
- Presenter owns the capped two-occupant logical visible set and tombstones displaced finalized turns.
- Presenter clears the live runtime with a monotonic empty snapshot before explicit overlay disable/shutdown teardown, while restart/reconnect preserves the presenter snapshot.
- Native state preserves physical slots only for the presenter-selected blocks, renders no enter/exit/reflow motion, and widens damage bounds to cover accent chrome.
```

- [ ] **Step 5: Commit**

```bash
git add agents/logs/2026-04-04-overlay-two-slot-static-caption-implementation-l3.md
git commit -m "Add overlay two-slot implementation verification log"
```

---

## Self-Review

### Spec coverage

- `occupant_key` / `appearance_seq` internal contract: Task 1, Task 3, Task 4, Task 5
- Contract-version fast failure for mixed builds: Task 2, Task 7
- Presenter ownership of the capped logical visible set: Task 4
- Native ownership of physical slot continuity only: Task 5
- Fixed slot anchors and no compaction: Task 5, Task 6
- One-shot 6px accent bar with `0.12s` timing: Task 5, Task 6
- Peer first-visible translation semantics: Task 4, Task 5
- Hidden peer cancel-before-visible semantics: Task 4
- Overlay restart/reconnect preservation vs explicit disable/shutdown clear split: Task 4, Task 7
- Peer runtime policy/warmup not acting as visibility boundaries: Task 4, Task 7
- Explicit disable/shutdown clearing slot and pulse state: Task 4, Task 7
- Accent-aware damage clearing: Task 6, Task 7
- Testability requirements for `clear -> final -> close`, secondary-text hole stability, and timing epsilon: Task 4, Task 5, Task 6, Task 7

### Placeholder scan

- No unfinished marker keywords or cross-task shorthand remain.

### Type consistency

- `occupant_key` is introduced in protocol/sink first, propagated by hub, assigned by presenter, then consumed by native state.
- `appearance_seq` is presenter-authored and native-consumed everywhere in the plan.
- `contract_version` is intentionally bumped to `4` in both Python and native manifest code before slot-state snapshots ship.
- Slot rendering fields (`slot_index`, `slot_top_px`, `accent_opacity`) are introduced in runtime/types before layout/backend use them.
