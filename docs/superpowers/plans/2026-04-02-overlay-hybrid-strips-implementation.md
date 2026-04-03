# Overlay Hybrid Strips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the VR overlay into a retained-mode hybrid strip renderer with 5-second presenter TTL, width-based centered text wrapping, 36/45 fps adaptive animation, and targeted redraw that removes the current per-frame `Flush()` path.

**Architecture:** Python keeps ownership of logical turn lifetime and publishes ordered snapshots; Rust owns the visual strip scene, animation state, layout measurement, damage tracking, and rasterization into one OpenVR texture. The OpenVR boundary stays single-texture and head-locked, but the renderer moves from immediate full-surface redraw to retained scene diffing plus damage-band redraw.

**Tech Stack:** Python 3.12, pytest, uv + `.venv-wsl`, Rust 2021, tokio, Direct3D11, Direct2D/DirectWrite, OpenVR

**Execution Constraints:** Work only in `/mnt/c/Users/salee/Documents/dev/puripuly_heart/.worktrees/overlay-hybrid-strips-rewrite`. Use `UV_PROJECT_ENVIRONMENT=/mnt/c/Users/salee/Documents/dev/puripuly_heart/.venv-wsl` for Python commands. Do not create intermediate git commits; the user explicitly requested local-only progress until review.

---

## File Map

- Modify: `src/puripuly_heart/core/overlay/presenter.py`
- Modify: `tests/core/test_overlay_presenter.py`
- Modify: `native/overlay/src/lib.rs`
- Modify: `native/overlay/src/runtime.rs`
- Modify: `native/overlay/src/openvr.rs`
- Delete: `native/overlay/src/renderer.rs`
- Create: `native/overlay/src/renderer/mod.rs`
- Create: `native/overlay/src/renderer/types.rs`
- Create: `native/overlay/src/renderer/layout.rs`
- Create: `native/overlay/src/renderer/backend.rs`
- Modify: `native/overlay/tests/renderer.rs`
- Modify: `native/overlay/tests/runtime.rs`

> Superseded implementation note (2026-04-03): retained strip scene/lifecycle logic stays in `native/overlay/src/state.rs` and `native/overlay/src/runtime.rs`. It was not split into `renderer/scene.rs`, `renderer/animation.rs`, or `renderer/damage.rs`, and the corresponding standalone test files are not planned.

## Baseline Already Verified

- Worktree created at `/mnt/c/Users/salee/Documents/dev/puripuly_heart/.worktrees/overlay-hybrid-strips-rewrite`
- `cargo test --test renderer --test runtime --test state` passed in `native/overlay`
- `UV_PROJECT_ENVIRONMENT=/mnt/c/Users/salee/Documents/dev/puripuly_heart/.venv-wsl uv run pytest tests/app/test_overlay_process_manager.py tests/ui/test_controller_branch_paths.py -q` passed

### Task 1: Presenter TTL And Shutdown-Safe Expiry

**Files:**
- Modify: `src/puripuly_heart/core/overlay/presenter.py`
- Modify: `tests/core/test_overlay_presenter.py`

- [ ] **Step 1: Write the failing presenter TTL tests**

```python
import asyncio


class FakeSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self._waiters: list[asyncio.Future[None]] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        await fut

    def wake_next(self) -> None:
        waiter = self._waiters.pop(0)
        waiter.set_result(None)


@pytest.mark.asyncio
async def test_presenter_expires_closed_turn_five_seconds_after_close() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    sleeper = FakeSleeper()
    adapter = OverlayEventAdapter(clock=clock)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        sleep_fn=sleeper,
    )
    transcript = Transcript(
        utterance_id=uuid4(),
        channel="self",
        text="expire me",
        is_final=True,
        created_at=10.0,
    )

    await presenter.emit(
        adapter.transcript_final(transcript, source_language="ko", target_language="en")
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=transcript.utterance_id,
            channel="self",
            is_final=True,
            created_at=10.1,
        )
    )

    assert sleeper.delays == [5.0]
    assert presenter.snapshot().blocks[-1].id == f"self:{transcript.utterance_id}"

    clock._now = 15.2
    sleeper.wake_next()
    await asyncio.sleep(0)

    assert presenter.snapshot().blocks == []


@pytest.mark.asyncio
async def test_presenter_reset_scene_cancels_pending_expiry_tasks() -> None:
    bridge = RecordingPresentationBridge()
    clock = FakeClock(_now=10.0)
    sleeper = FakeSleeper()
    adapter = OverlayEventAdapter(clock=clock)
    presenter = OverlayPresenter(
        bridge=bridge,
        calibration=OverlayCalibration(),
        clock=clock,
        sleep_fn=sleeper,
    )
    transcript = Transcript(
        utterance_id=uuid4(),
        channel="self",
        text="cancel me",
        is_final=True,
        created_at=10.0,
    )

    await presenter.emit(
        adapter.transcript_final(transcript, source_language="ko", target_language="en")
    )
    await presenter.emit(
        adapter.utterance_closed(
            utterance_id=transcript.utterance_id,
            channel="self",
            is_final=True,
            created_at=10.1,
        )
    )
    presenter.reset_scene()

    clock._now = 16.0
    sleeper.wake_next()
    await asyncio.sleep(0)

    assert presenter.snapshot().blocks == []
    assert bridge.snapshots[-1].blocks == []
```

- [ ] **Step 2: Run the presenter tests to verify they fail**

Run:

```bash
UV_PROJECT_ENVIRONMENT=/mnt/c/Users/salee/Documents/dev/puripuly_heart/.venv-wsl \
uv run pytest tests/core/test_overlay_presenter.py -q
```

Expected:

- `TypeError` because `OverlayPresenter` does not yet accept `sleep_fn`
- No 5-second TTL behavior for closed turns

- [ ] **Step 3: Implement presenter-side 5 second expiration**

```python
import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable

FINALIZED_TURN_TTL_S = 5.0


@dataclass(slots=True)
class _LogicalCaptionEntry:
    channel: str
    utterance_id: UUID
    original_text: str = ""
    translation_text: str = ""
    last_updated_seq: int = 0
    closed_seq: int | None = None
    closed_at: float | None = None


@dataclass(slots=True)
class OverlayPresenter(OverlaySink):
    calibration: OverlayCalibration
    bridge: OverlayPresentationTransport | None = None
    clock: Clock = field(default_factory=SystemClock)
    visible_window_target_blocks: int = VISIBLE_WINDOW_TARGET_BLOCKS
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep
    _expiry_tasks: dict[tuple[str, UUID], asyncio.Task[None]] = field(
        init=False,
        default_factory=dict,
    )

    def reset_scene(self) -> None:
        for task in self._expiry_tasks.values():
            task.cancel()
        self._expiry_tasks.clear()
        self._entries.clear()
        self._closed_tombstones.clear()
        self._active_self = None
        self._revision = 0
        self._snapshot = OverlayPresentationSnapshot(
            revision=0,
            calibration=_calibration_from_overlay(self.calibration),
            blocks=[],
        )

    def _apply_event(self, event: OverlayEventUnion) -> bool:
        if isinstance(event, UtteranceClosed):
            key = self._entry_key(event.channel, event.utterance_id)
            if key in self._closed_tombstones:
                return False
            entry = self._entries.get(key)
            if entry is None:
                return False
            if event.seq < entry.last_updated_seq:
                return False
            if entry.closed_seq == event.seq:
                return False
            entry.closed_seq = event.seq
            entry.closed_at = self.clock.now()
            entry.last_updated_seq = event.seq
            self._schedule_expiry(key, entry.closed_at + FINALIZED_TURN_TTL_S)
            return True
        return False

    def _schedule_expiry(self, key: tuple[str, UUID], deadline: float) -> None:
        existing = self._expiry_tasks.pop(key, None)
        if existing is not None:
            existing.cancel()
        self._expiry_tasks[key] = asyncio.create_task(self._expire_entry_after_delay(key, deadline))

    async def _expire_entry_after_delay(self, key: tuple[str, UUID], deadline: float) -> None:
        try:
            remaining = max(0.0, deadline - self.clock.now())
            await self.sleep_fn(remaining)
            entry = self._entries.get(key)
            if entry is None or entry.closed_at is None:
                return
            if self.clock.now() < entry.closed_at + FINALIZED_TURN_TTL_S:
                return
            self._entries.pop(key, None)
            self._closed_tombstones[key] = self._revision + 1
            while len(self._closed_tombstones) > _CLOSED_TOMBSTONE_LIMIT:
                self._closed_tombstones.popitem(last=False)
            await self._publish_if_changed()
        except asyncio.CancelledError:
            raise
        finally:
            self._expiry_tasks.pop(key, None)
```

Implementation notes:

- Leave active self outside TTL handling
- Keep tombstone behavior for expired finalized ids so late translation updates stay ignored
- Cancel any pending expiry task when `reset_scene()` is called

- [ ] **Step 4: Re-run the presenter tests and confirm the overlay Python path still passes**

Run:

```bash
UV_PROJECT_ENVIRONMENT=/mnt/c/Users/salee/Documents/dev/puripuly_heart/.venv-wsl \
uv run pytest tests/core/test_overlay_presenter.py tests/ui/test_controller_branch_paths.py -q
```

Expected:

- presenter tests PASS
- controller overlay branch tests still PASS with the new presenter constructor

### Task 2: Replace Immediate Caption Blocks With A Retained Strip Scene

> Superseded architecture note (2026-04-03): `OverlayScene` remains in `state.rs`; there is no separate `renderer/scene.rs`.

**Files:**
- Delete: `native/overlay/src/renderer.rs`
- Create: `native/overlay/src/renderer/mod.rs`
- Create: `native/overlay/src/renderer/types.rs`
- Create: `native/overlay/src/renderer/backend.rs`
- Modify: `native/overlay/src/lib.rs`

- [ ] **Step 1: Write failing strip-scene tests**

```rust
use puripuly_heart_overlay::renderer::scene::{RetainedStripScene, StripPhase};
use puripuly_heart_overlay::{
    OverlayPresentationBlock, OverlayPresentationCalibration, OverlayPresentationSnapshot,
};

fn block(id: &str, channel: &str, text: &str) -> OverlayPresentationBlock {
    OverlayPresentationBlock {
        id: id.to_string(),
        channel: channel.to_string(),
        text: text.to_string(),
    }
}

#[test]
fn scene_marks_new_snapshot_blocks_as_entering() {
    let mut scene = RetainedStripScene::default();

    scene.sync_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello")],
    });

    assert_eq!(scene.strips()[0].id, "self:1");
    assert_eq!(scene.strips()[0].phase, StripPhase::Entering);
}

#[test]
fn scene_turns_missing_snapshot_blocks_into_exiting_nodes() {
    let mut scene = RetainedStripScene::default();

    scene.sync_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "first")],
    });
    scene.mark_all_steady_for_test();

    scene.sync_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![],
    });

    assert_eq!(scene.strips()[0].phase, StripPhase::Exiting);
}

#[test]
fn scene_updates_stream_text_in_place_without_reentering() {
    let mut scene = RetainedStripScene::default();

    scene.sync_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:1", "peer", "hello")],
    });
    scene.mark_all_steady_for_test();

    scene.sync_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:1", "peer", "hello world")],
    });

    assert_eq!(scene.strips()[0].phase, StripPhase::Steady);
    assert_eq!(scene.strips()[0].text, "hello world");
}
```

- [ ] **Step 2: Run the new scene tests to verify they fail**

Run:

```bash
cargo test --test scene
```

Expected:

- compile failure because `renderer::scene` and `RetainedStripScene` do not exist

- [ ] **Step 3: Split the renderer module and add retained strip scene types**

```rust
// native/overlay/src/renderer/types.rs
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StripPhase {
    Entering,
    Steady,
    Exiting,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StripBounds {
    pub top: f32,
    pub bottom: f32,
    pub left: f32,
    pub right: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StripNode {
    pub id: String,
    pub channel: CaptionChannel,
    pub text: String,
    pub phase: StripPhase,
    pub order_key: usize,
    pub alpha: f32,
    pub offset_y: f32,
    pub current_bounds: Option<StripBounds>,
    pub previous_bounds: Option<StripBounds>,
}

// native/overlay/src/renderer/scene.rs
#[derive(Debug, Default)]
pub struct RetainedStripScene {
    strips: Vec<StripNode>,
    revision: u64,
}

impl RetainedStripScene {
    pub fn sync_snapshot(&mut self, snapshot: &OverlayPresentationSnapshot) {
        if snapshot.revision <= self.revision {
            return;
        }
        self.revision = snapshot.revision;

        let mut next = Vec::with_capacity(snapshot.blocks.len().max(self.strips.len()));
        for (index, block) in snapshot.blocks.iter().enumerate() {
            let channel = if block.channel == "peer" {
                CaptionChannel::PeerChannel
            } else {
                CaptionChannel::SelfChannel
            };
            if let Some(existing) = self.strips.iter().find(|strip| strip.id == block.id) {
                let mut strip = existing.clone();
                strip.text = block.text.clone();
                strip.channel = channel;
                strip.order_key = index;
                if strip.phase == StripPhase::Exiting {
                    strip.phase = StripPhase::Entering;
                    strip.alpha = 0.0;
                }
                next.push(strip);
            } else {
                next.push(StripNode {
                    id: block.id.clone(),
                    channel,
                    text: block.text.clone(),
                    phase: StripPhase::Entering,
                    order_key: index,
                    alpha: 0.0,
                    offset_y: 8.0,
                    current_bounds: None,
                    previous_bounds: None,
                });
            }
        }

        for old in &self.strips {
            if snapshot.blocks.iter().all(|block| block.id != old.id) {
                let mut strip = old.clone();
                strip.phase = StripPhase::Exiting;
                next.push(strip);
            }
        }

        next.sort_by_key(|strip| strip.order_key);
        self.strips = next;
    }

    pub fn strips(&self) -> &[StripNode] {
        &self.strips
    }
}

// native/overlay/src/renderer/mod.rs
pub mod backend;
pub mod scene;
pub mod types;

pub use backend::{CaptionPresentation, CaptionRenderError, CaptionRenderer, RenderedFrame};
pub use scene::RetainedStripScene;
pub use types::{CaptionBlock, CaptionChannel, StripBounds, StripNode, StripPhase};
```

Implementation notes:

- Move the existing Direct3D/Direct2D backend code into `backend.rs`
- Keep `CaptionRenderer` and `RenderedFrame` re-exported from `renderer/mod.rs`
- Keep `CaptionBlock` exported temporarily so existing runtime tests continue compiling during the transition

- [ ] **Step 4: Re-run the scene tests and the existing renderer smoke tests**

Run:

```bash
cargo test --test scene --test renderer
```

Expected:

- new scene tests PASS
- existing renderer tests still compile through the new module exports

### Task 3: Width-Based Wrapping, Center Alignment, And Overflow Clamping

**Files:**
- Create: `native/overlay/src/renderer/layout.rs`
- Modify: `native/overlay/src/renderer/mod.rs`
- Modify: `native/overlay/src/renderer/backend.rs`
- Modify: `native/overlay/tests/renderer.rs`

- [ ] **Step 1: Write failing layout tests for wrapping, centering, and no overflow**

```rust
use puripuly_heart_overlay::renderer::layout::{
    CenteredStripLayoutEngine, LayoutConfig, TestTextMeasurer,
};
use puripuly_heart_overlay::renderer::types::{StripNode, StripPhase};
use puripuly_heart_overlay::CaptionChannel;

#[test]
fn layout_wraps_text_to_strip_width_without_crossing_right_edge() {
    let measurer = TestTextMeasurer::new(18.0, 28.0);
    let engine = CenteredStripLayoutEngine::new(
        LayoutConfig {
            surface_width_px: 3840,
            surface_height_px: 1024,
            max_visible_finalized: 2,
            strip_max_width_px: 2750.0,
            horizontal_padding_px: 48.0,
            vertical_padding_px: 28.0,
            line_height_px: 156.0,
            inter_strip_gap_px: 36.0,
        },
        measurer,
    );
    let strip = StripNode {
        id: "peer:1".into(),
        channel: CaptionChannel::PeerChannel,
        text: "this line must wrap before it crosses the strip width".repeat(4),
        phase: StripPhase::Steady,
        order_key: 0,
        alpha: 1.0,
        offset_y: 0.0,
        current_bounds: None,
        previous_bounds: None,
    };

    let frame = engine.layout(vec![strip]);

    assert!(frame.strips[0].lines.len() > 1);
    assert!(frame.strips[0].lines.iter().all(|line| line.width_px <= frame.strips[0].content_width_px));
}

#[test]
fn layout_centers_each_line_inside_the_strip_box() {
    let measurer = TestTextMeasurer::new(20.0, 30.0);
    let engine = CenteredStripLayoutEngine::default_for_test(measurer);
    let frame = engine.layout(vec![StripNode {
        id: "self:1".into(),
        channel: CaptionChannel::SelfChannel,
        text: "center me".into(),
        phase: StripPhase::Steady,
        order_key: 0,
        alpha: 1.0,
        offset_y: 0.0,
        current_bounds: None,
        previous_bounds: None,
    }]);

    let strip = &frame.strips[0];
    let line = &strip.lines[0];
    assert!((line.origin_x_px - ((strip.bounds.left + strip.bounds.right - line.width_px) / 2.0)).abs() < 0.5);
}

#[test]
fn layout_clamps_latest_only_strip_when_text_is_taller_than_safe_region() {
    let measurer = TestTextMeasurer::new(16.0, 24.0);
    let engine = CenteredStripLayoutEngine::default_for_test(measurer);
    let frame = engine.layout(vec![StripNode {
        id: "peer:1".into(),
        channel: CaptionChannel::PeerChannel,
        text: "overflow ".repeat(300),
        phase: StripPhase::Steady,
        order_key: 0,
        alpha: 1.0,
        offset_y: 0.0,
        current_bounds: None,
        previous_bounds: None,
    }]);

    assert!(frame.strips[0].truncated);
    assert!(frame.strips[0].bounds.bottom <= frame.safe_bottom_px);
}
```

- [ ] **Step 2: Run the renderer tests to verify they fail**

Run:

```bash
cargo test --test renderer
```

Expected:

- compile failure because `CenteredStripLayoutEngine`, `LayoutConfig`, and `TestTextMeasurer` do not exist

- [ ] **Step 3: Implement measured layout instead of character-count wrapping**

```rust
// native/overlay/src/renderer/layout.rs
pub trait TextMeasurer {
    fn measure_text_width(&self, text: &str, channel: CaptionChannel) -> f32;
}

#[derive(Debug, Clone, PartialEq)]
pub struct LayoutConfig {
    pub surface_width_px: u32,
    pub surface_height_px: u32,
    pub max_visible_finalized: usize,
    pub strip_max_width_px: f32,
    pub horizontal_padding_px: f32,
    pub vertical_padding_px: f32,
    pub line_height_px: f32,
    pub inter_strip_gap_px: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LaidOutLine {
    pub text: String,
    pub width_px: f32,
    pub origin_x_px: f32,
    pub baseline_y_px: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LaidOutStrip {
    pub id: String,
    pub channel: CaptionChannel,
    pub lines: Vec<LaidOutLine>,
    pub bounds: StripBounds,
    pub content_width_px: f32,
    pub truncated: bool,
    pub alpha: f32,
}

pub struct CenteredStripLayoutEngine<M> {
    config: LayoutConfig,
    measurer: M,
}

impl<M: TextMeasurer> CenteredStripLayoutEngine<M> {
    pub fn layout(&self, strips: Vec<StripNode>) -> LayoutFrame {
        let safe_left = (self.config.surface_width_px as f32 - self.config.strip_max_width_px) / 2.0;
        let safe_right = safe_left + self.config.strip_max_width_px;
        let safe_top = 64.0;
        let safe_bottom = self.config.surface_height_px as f32 - 64.0;
        let mut cursor_bottom = safe_bottom;
        let mut visible = Vec::new();

        for strip in strips.into_iter().rev() {
            let content_width = self.config.strip_max_width_px - self.config.horizontal_padding_px * 2.0;
            let wrapped = wrap_paragraphs(&self.measurer, &strip.text, strip.channel, content_width);
            let strip_height = wrapped.len().max(1) as f32 * self.config.line_height_px
                + self.config.vertical_padding_px * 2.0;
            let top = (cursor_bottom - strip_height).max(safe_top);
            let truncated = top <= safe_top && strip_height > (safe_bottom - safe_top);
            let bounds = StripBounds {
                left: safe_left,
                right: safe_right,
                top,
                bottom: cursor_bottom,
            };

            let lines = wrapped
                .into_iter()
                .enumerate()
                .map(|(index, text)| {
                    let width = self.measurer.measure_text_width(&text, strip.channel);
                    let origin_x = (bounds.left + bounds.right - width) / 2.0;
                    LaidOutLine {
                        text,
                        width_px: width,
                        origin_x_px: origin_x,
                        baseline_y_px: bounds.top + self.config.vertical_padding_px
                            + (index as f32 + 1.0) * self.config.line_height_px,
                    }
                })
                .collect();

            visible.push(LaidOutStrip {
                id: strip.id,
                channel: strip.channel,
                lines,
                bounds,
                content_width_px: content_width,
                truncated,
                alpha: strip.alpha,
            });

            cursor_bottom = top - self.config.inter_strip_gap_px;
            if cursor_bottom <= safe_top {
                break;
            }
        }

        visible.reverse();
        LayoutFrame {
            strips: visible,
            safe_bottom_px: safe_bottom,
        }
    }
}
```

Implementation notes:

- Use paragraph-preserving wrap logic instead of `wrap_text()` character chunks
- Replace `DrawText`-with-left/right box assumptions with per-line centered origins
- Keep the off-Windows test path purely geometry-based so the new layout remains unit-testable in WSL

- [ ] **Step 4: Re-run renderer tests**

Run:

```bash
cargo test --test renderer
```

Expected:

- wrapping tests PASS
- centered layout tests PASS
- no-overflow/truncation tests PASS

### Task 4: Adaptive 36/45 Animation Tick And HMD Refresh Detection

**Files:**
- Create: `native/overlay/src/renderer/animation.rs`
- Modify: `native/overlay/src/openvr.rs`
- Modify: `native/overlay/src/runtime.rs`
- Modify: `native/overlay/tests/runtime.rs`

- [ ] **Step 1: Write failing runtime tests for adaptive animation cadence**

```rust
#[derive(Default)]
struct HintOnlySubmitter {
    fps_hint: u32,
}

impl OverlayFrameSubmitter for HintOnlySubmitter {
    fn submit_frame(&mut self, _frame: &RenderedFrame) -> Result<(), OpenVrError> {
        Ok(())
    }

    fn animation_frame_rate_hint(&self) -> u32 {
        self.fps_hint
    }
}

#[test]
fn runtime_uses_36fps_for_72hz_hint() {
    let submitter = HintOnlySubmitter { fps_hint: 36 };
    assert_eq!(submitter.animation_frame_rate_hint(), 36);
}

#[test]
fn runtime_uses_45fps_for_90hz_hint() {
    let submitter = HintOnlySubmitter { fps_hint: 45 };
    assert_eq!(submitter.animation_frame_rate_hint(), 45);
}

#[tokio::test]
async fn runtime_keeps_animation_ticker_idle_without_active_motion() {
    let runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    assert!(runtime.animation_deadline_for_test().is_none());
}
```

- [ ] **Step 2: Run runtime tests to verify they fail**

Run:

```bash
cargo test --test runtime
```

Expected:

- compile failure because `animation_frame_rate_hint()` and `animation_deadline_for_test()` do not exist

- [ ] **Step 3: Add half-refresh animation cadence and refresh-rate hinting**

```rust
// native/overlay/src/openvr.rs
pub trait OverlayFrameSubmitter {
    fn submit_frame(&mut self, frame: &RenderedFrame) -> Result<(), OpenVrError>;

    fn apply_calibration(&mut self, _calibration: &OverlayCalibration) -> Result<(), OpenVrError> {
        Ok(())
    }

    fn set_overlay_visible(&mut self, _visible: bool) -> Result<(), OpenVrError> {
        Ok(())
    }

    fn animation_frame_rate_hint(&self) -> u32 {
        45
    }
}

#[cfg(windows)]
impl WindowsOpenVrOverlay {
    fn query_display_refresh_hz(&self) -> Option<f32> {
        let system = initialize_system_api().ok()?;
        let getter = unsafe { (*system).GetFloatTrackedDeviceProperty }?;
        let mut error = openvr_sys::ETrackedPropertyError_TrackedProp_Success;
        let hz = unsafe {
            getter(
                openvr_sys::k_unTrackedDeviceIndex_Hmd,
                openvr_sys::ETrackedDeviceProperty_Prop_DisplayFrequency_Float,
                &mut error,
            )
        };
        if error == openvr_sys::ETrackedPropertyError_TrackedProp_Success {
            Some(hz)
        } else {
            None
        }
    }
}

#[cfg(windows)]
impl OverlayFrameSubmitter for WindowsOpenVrOverlay {
    fn animation_frame_rate_hint(&self) -> u32 {
        match self.query_display_refresh_hz() {
            Some(hz) if (hz - 72.0).abs() < 0.5 => 36,
            Some(hz) if (hz - 90.0).abs() < 0.5 => 45,
            _ => 45,
        }
    }
}

// native/overlay/src/renderer/animation.rs
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AnimationCadence {
    pub fps: u32,
}

impl AnimationCadence {
    pub fn frame_interval(&self) -> Duration {
        Duration::from_secs_f32(1.0 / self.fps as f32)
    }
}
```

Implementation notes:

- Keep cadence calculation conservative: only special-case 72 and 90; default to 45
- Expose one test hook on `OverlayRuntime` for the scheduled animation deadline so idle behavior stays testable

- [ ] **Step 4: Re-run the runtime tests**

Run:

```bash
cargo test --test runtime
```

Expected:

- new cadence tests PASS
- existing startup/visibility tests still PASS

### Task 5: Damage-Band Redraw And Flush Removal

**Files:**
- Create: `native/overlay/src/renderer/damage.rs`
- Modify: `native/overlay/src/renderer/backend.rs`
- Create: `native/overlay/tests/damage.rs`
- Modify: `native/overlay/tests/renderer.rs`

- [ ] **Step 1: Write failing damage-band tests**

```rust
use puripuly_heart_overlay::renderer::damage::{compute_damage_band, DamageBand};
use puripuly_heart_overlay::renderer::types::StripBounds;

#[test]
fn damage_band_covers_previous_and_current_bounds_for_moved_strip() {
    let band = compute_damage_band(
        &[StripBounds {
            left: 100.0,
            right: 500.0,
            top: 200.0,
            bottom: 320.0,
        }],
        &[StripBounds {
            left: 100.0,
            right: 500.0,
            top: 150.0,
            bottom: 270.0,
        }],
        1024,
    )
    .unwrap();

    assert_eq!(band.top_px, 150);
    assert_eq!(band.bottom_px, 320);
}

#[test]
fn damage_band_is_none_when_no_bounds_change() {
    assert_eq!(compute_damage_band(&[], &[], 1024), None);
}
```

- [ ] **Step 2: Run the damage tests to verify they fail**

Run:

```bash
cargo test --test damage
```

Expected:

- compile failure because `renderer::damage` does not exist

- [ ] **Step 3: Implement damage-band redraw and remove unconditional `Flush()`**

```rust
// native/overlay/src/renderer/damage.rs
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DamageBand {
    pub top_px: u32,
    pub bottom_px: u32,
}

pub fn compute_damage_band(
    previous: &[StripBounds],
    current: &[StripBounds],
    surface_height_px: u32,
) -> Option<DamageBand> {
    let mut top = surface_height_px as f32;
    let mut bottom = 0.0_f32;

    for bounds in previous.iter().chain(current.iter()) {
        top = top.min(bounds.top);
        bottom = bottom.max(bounds.bottom);
    }

    if bottom <= top {
        return None;
    }

    Some(DamageBand {
        top_px: top.floor().max(0.0) as u32,
        bottom_px: bottom.ceil().min(surface_height_px as f32) as u32,
    })
}

// native/overlay/src/renderer/backend.rs
if let Some(damage) = frame.damage_band {
    self.d2d_context.PushAxisAlignedClip(
        &D2D_RECT_F {
            left: 0.0,
            top: damage.top_px as f32,
            right: frame.surface_width_px as f32,
            bottom: damage.bottom_px as f32,
        },
        D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
    );
    self.d2d_context.Clear(Some(&D2D1_COLOR_F {
        r: 0.0,
        g: 0.0,
        b: 0.0,
        a: presentation.background_alpha,
    }));
    self.draw_visible_strips(frame, Some(damage))?;
    self.d2d_context.PopAxisAlignedClip();
} else {
    self.d2d_context.Clear(Some(&D2D1_COLOR_F {
        r: 0.0,
        g: 0.0,
        b: 0.0,
        a: presentation.background_alpha,
    }));
    self.draw_visible_strips(frame, None)?;
}

self.d2d_context
    .EndDraw(None, None)
    .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
```

Implementation notes:

- Do not call `self.d3d_context.Flush()` in the default redraw path
- Keep a narrow fallback helper if Windows validation proves `Flush()` is still needed for a specific failure mode
- In the off-Windows test backend, store the computed `DamageBand` inside the frame so tests can assert it directly

- [ ] **Step 4: Re-run damage and renderer tests**

Run:

```bash
cargo test --test damage --test renderer
```

Expected:

- damage tests PASS
- renderer tests PASS without bringing back full-surface redraw assumptions

### Task 6: Runtime Integration, Full Regression, And Plan Exit Criteria

**Files:**
- Modify: `native/overlay/src/runtime.rs`
- Modify: `native/overlay/src/lib.rs`
- Modify: `native/overlay/tests/runtime.rs`
- Modify: `tests/core/test_overlay_presenter.py`

- [ ] **Step 1: Write failing end-to-end runtime tests for strip lifecycle integration**

```rust
#[tokio::test]
async fn runtime_updates_existing_strip_text_without_restarting_enter_motion() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("stream-update-no-reenter").await;
    let (mut bridge, server) = connect_test_bridge().await;
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:1", "peer", "hello")],
    });
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:1", "peer", "hello world")],
    });

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();

    assert!(renderer.debug_scene_for_test().strips()[0].phase.is_steady());
    let _ = bridge.close().await;
    let _ = server.await;
}

#[tokio::test]
async fn runtime_keeps_redraws_idle_when_no_snapshot_or_animation_is_pending() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("idle-no-redraw").await;
    let (mut bridge, server) = connect_test_bridge().await;
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();
    let calls_after_first_submit = submitter.calls;

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();

    assert_eq!(submitter.calls, calls_after_first_submit);
    let _ = bridge.close().await;
    let _ = server.await;
}
```

- [ ] **Step 2: Run the full targeted regression suite and verify the new tests fail first**

Run:

```bash
cargo test --test renderer --test runtime --test state --test scene --test damage
UV_PROJECT_ENVIRONMENT=/mnt/c/Users/salee/Documents/dev/puripuly_heart/.venv-wsl \
uv run pytest tests/core/test_overlay_presenter.py tests/app/test_overlay_process_manager.py tests/ui/test_controller_branch_paths.py -q
```

Expected:

- at least one new runtime integration test FAILS before the final wiring is in place

- [ ] **Step 3: Wire runtime to the retained renderer and remove obsolete caption-block plumbing**

```rust
// native/overlay/src/runtime.rs
pub struct OverlayRuntime {
    ready: bool,
    first_texture_submitted: bool,
    overlay_visible: bool,
    stopped: bool,
    state: OverlayState,
    redraw_requested: bool,
    hide_deadline: Option<Instant>,
    animation_deadline: Option<Instant>,
}

pub async fn submit_frame_if_needed<S: OverlayFrameSubmitter>(
    &mut self,
    renderer: &CaptionRenderer,
    openvr: &mut S,
    bridge: &mut BridgeClient,
    logger: &OverlayLogger,
) -> Result<(), RuntimeFailure> {
    if self.first_texture_submitted
        && !self.redraw_requested
        && self.animation_deadline.is_none()
    {
        return Ok(());
    }

    renderer.set_presentation(CaptionPresentation {
        background_alpha: self.state.calibration().background_alpha,
    });
    renderer.sync_snapshot(self.state.snapshot());
    renderer.sample_animations(openvr.animation_frame_rate_hint());

    let frame = renderer
        .render_frame()
        .map_err(|error| RuntimeFailure::Render(error.to_string()))?;

    openvr
        .submit_frame(&frame)
        .map_err(|error| RuntimeFailure::OpenVr(error.to_string()))?;

    let has_drawable_text = frame.has_drawable_text();
    if has_drawable_text {
        self.hide_deadline = None;
    } else if self.first_texture_submitted && self.overlay_visible && self.hide_deadline.is_none() {
        self.hide_deadline = Some(Instant::now() + EMPTY_OVERLAY_HIDE_DELAY);
    }
    if has_drawable_text && !self.overlay_visible {
        openvr
            .set_overlay_visible(true)
            .map_err(|error| RuntimeFailure::OpenVr(error.to_string()))?;
        self.overlay_visible = true;
    }

    self.animation_deadline = renderer.next_animation_deadline();
    self.redraw_requested = false;
    if !self.first_texture_submitted {
        self.first_texture_submitted = true;
        self.emit_ready(bridge, logger).await?;
    }
    Ok(())
}

pub async fn run_event_loop<S: OverlayFrameSubmitter>(
    &mut self,
    bridge: &mut BridgeClient,
    renderer: &CaptionRenderer,
    openvr: &mut S,
    logger: &OverlayLogger,
) -> Result<(), RuntimeFailure> {
    loop {
        let next_deadline = match (self.hide_deadline, self.animation_deadline) {
            (Some(hide), Some(anim)) => Some(hide.min(anim)),
            (Some(hide), None) => Some(hide),
            (None, Some(anim)) => Some(anim),
            (None, None) => None,
        };

        if let Some(deadline) = next_deadline {
            tokio::select! {
                _ = sleep_until(deadline) => {
                    if self.animation_deadline == Some(deadline) {
                        self.redraw_requested = true;
                        self.submit_frame_if_needed(renderer, openvr, bridge, logger).await?;
                    } else {
                        self.handle_hide_deadline(openvr).await?;
                    }
                }
                message = bridge.next_message() => {
                    if !self.handle_bridge_message(message, renderer, openvr, bridge, logger).await? {
                        return Ok(());
                    }
                }
            }
        } else if !self
            .handle_bridge_message(bridge.next_message().await, renderer, openvr, bridge, logger)
            .await?
        {
            return Ok(());
        }
    }
}
```

Implementation notes:

- Remove or deprecate `OverlayRuntime::caption_blocks()` once the renderer consumes snapshots directly
- Keep `OverlayState` as the snapshot holder; do not expand the Python-to-Rust protocol in this pass
- Preserve existing overlay visibility semantics when the scene becomes empty

- [ ] **Step 4: Run the full regression suite and record success criteria**

Run:

```bash
cargo test --test renderer --test runtime --test state --test scene --test damage
UV_PROJECT_ENVIRONMENT=/mnt/c/Users/salee/Documents/dev/puripuly_heart/.venv-wsl \
uv run pytest tests/core/test_overlay_presenter.py tests/app/test_overlay_process_manager.py tests/ui/test_controller_branch_paths.py -q
```

Expected:

- all targeted Rust tests PASS
- all targeted Python tests PASS
- no renderer path relies on unconditional `ID3D11DeviceContext::Flush()`
- runtime redraws stop when there is no snapshot change and no active animation

## Self-Review Checklist

- Spec coverage:
  - Hybrid strips: Tasks 2, 3, 5, 6
  - Centered text: Task 3
  - Width-based wrapping / no overflow: Task 3
  - TTL 5 seconds: Task 1
  - 36/45 adaptive animation: Task 4
  - Flush removal and targeted redraw: Task 5
  - Worktree-isolated execution: plan header + baseline section
- Placeholder scan:
  - No `TODO`/`TBD`
  - Commands and test names are concrete
  - Code symbols referenced later are introduced in earlier tasks
- Type consistency:
  - `RetainedStripScene`, `StripNode`, `StripPhase`, `CenteredStripLayoutEngine`, and `animation_frame_rate_hint()` are used consistently across tasks
