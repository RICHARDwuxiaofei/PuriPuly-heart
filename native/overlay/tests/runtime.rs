use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

use puripuly_heart_overlay::logging::OverlayLogger;
use puripuly_heart_overlay::{
    run_with_manifest, submit_texture, validate_manifest, BridgeClient, CaptionBlock,
    CaptionChannel, CaptionLayoutPolicy, CaptionRenderer, FakeOpenVr, OpenVrError,
    OverlayBridgeEvent, OverlayFrameSubmitter, OverlayManifest, OverlayPresentationBlock,
    OverlayPresentationBlockVariant, OverlayPresentationCalibration, OverlayPresentationSnapshot,
    OverlayRuntime, RenderedFrame, RuntimeFailure, StartupError, EXPECTED_CONTRACT_VERSION,
};

fn test_manifest() -> OverlayManifest {
    OverlayManifest {
        contract_version: EXPECTED_CONTRACT_VERSION,
        app_version: env!("CARGO_PKG_VERSION").into(),
        overlay_instance_id: "overlay-test".into(),
        bridge_url: "ws://127.0.0.1:1".into(),
        session_token: "expected-token".into(),
        parent_pid: 1,
        startup_deadline_ms: 3000,
        log_dir: std::env::temp_dir()
            .join("puripuly-heart-overlay-tests")
            .display()
            .to_string(),
        log_level: "INFO".into(),
        locale: "en".into(),
        diagnostics_enabled: false,
    }
}

fn unique_log_dir(name: &str) -> String {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_nanos();
    std::env::temp_dir()
        .join(format!("puripuly-heart-overlay-tests-{name}-{nonce}"))
        .display()
        .to_string()
}

fn unique_temp_file(name: &str, extension: &str) -> std::path::PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_nanos();
    std::env::temp_dir().join(format!("puripuly-heart-overlay-{name}-{nonce}.{extension}"))
}

fn overlay_binary() -> &'static str {
    env!("CARGO_BIN_EXE_PuriPulyHeartOverlay")
}

fn parse_event_payloads(stderr: &[u8]) -> Vec<serde_json::Value> {
    String::from_utf8_lossy(stderr)
        .lines()
        .filter_map(|line| line.strip_prefix("EVENT "))
        .filter_map(|payload| serde_json::from_str(payload).ok())
        .collect()
}

fn block(
    id: &str,
    channel: &str,
    primary_text: &str,
    secondary_text: &str,
    secondary_enabled: bool,
) -> OverlayPresentationBlock {
    OverlayPresentationBlock {
        id: id.to_string(),
        channel: channel.to_string(),
        block_variant: OverlayPresentationBlockVariant::Finalized,
        primary_text: primary_text.to_string(),
        secondary_text: secondary_text.to_string(),
        secondary_enabled,
    }
}

fn active_self_block(id: &str, primary_text: &str) -> OverlayPresentationBlock {
    OverlayPresentationBlock {
        id: id.to_string(),
        channel: "self".to_string(),
        block_variant: OverlayPresentationBlockVariant::ActiveSelf,
        primary_text: primary_text.to_string(),
        secondary_text: String::new(),
        secondary_enabled: true,
    }
}

#[derive(Default)]
struct RecordingSubmitter {
    calls: usize,
    fail: bool,
    operations: Vec<&'static str>,
    visibility_changes: Vec<bool>,
    last_visible: Option<bool>,
}

impl RecordingSubmitter {
    fn failing() -> Self {
        Self {
            calls: 0,
            fail: true,
            operations: Vec::new(),
            visibility_changes: Vec::new(),
            last_visible: None,
        }
    }
}

impl OverlayFrameSubmitter for RecordingSubmitter {
    fn submit_frame(&mut self, frame: &RenderedFrame) -> Result<(), OpenVrError> {
        self.calls += 1;
        let operation = if frame.layout().visible_blocks.is_empty() {
            "submit:empty"
        } else {
            "submit:text"
        };
        self.operations.push(operation);
        if self.fail {
            return Err(OpenVrError::Submit("submit failed".into()));
        }
        assert_eq!(frame.width(), 3840);
        assert_eq!(frame.height(), 1024);
        Ok(())
    }

    fn set_overlay_visible(&mut self, visible: bool) -> Result<(), OpenVrError> {
        self.operations.push(if visible { "show" } else { "hide" });
        self.last_visible = Some(visible);
        self.visibility_changes.push(visible);
        Ok(())
    }
}

async fn connect_test_bridge() -> (
    BridgeClient,
    tokio::task::JoinHandle<Vec<serde_json::Value>>,
) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let auth = ws.next().await.unwrap().unwrap();
        let Message::Text(auth_text) = auth else {
            panic!("expected auth text frame");
        };
        let auth_payload: serde_json::Value = serde_json::from_str(&auth_text).unwrap();
        assert_eq!(auth_payload["type"], "auth");
        assert_eq!(auth_payload["session_token"], "expected-token");

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 0,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [],
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let mut messages = Vec::new();
        while let Some(message) = ws.next().await {
            let Ok(Message::Text(text)) = message else {
                break;
            };
            messages.push(serde_json::from_str(&text).unwrap());
        }

        messages
    });

    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (client, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    assert!(snapshot.blocks.is_empty());
    (client, server)
}

async fn test_logger(name: &str) -> OverlayLogger {
    OverlayLogger::open(unique_log_dir(name)).await.unwrap()
}

#[test]
fn runtime_accepts_app_version_mismatch_when_contract_version_matches() {
    let manifest = OverlayManifest {
        contract_version: EXPECTED_CONTRACT_VERSION,
        app_version: "0.0.1-test".into(),
        ..test_manifest()
    };

    let result = validate_manifest(&manifest);

    assert!(result.is_ok());
}

#[test]
fn runtime_returns_standardized_startup_failure_codes_before_ready() {
    assert_eq!(StartupError::ContractMismatch("bad".into()).exit_code(), 10);
    assert_eq!(StartupError::BridgeAuth("bad token".into()).exit_code(), 12);
    assert_eq!(StartupError::SteamVrNotInstalled.exit_code(), 20);
    assert_eq!(StartupError::SteamVrNotRunning.exit_code(), 20);
    assert_eq!(StartupError::HmdNotFound.exit_code(), 20);
    assert_eq!(
        StartupError::OpenVrInit("steamvr missing".into()).exit_code(),
        20
    );
    assert_eq!(
        StartupError::RendererInit("d3d init failed".into()).exit_code(),
        21
    );
}

#[test]
fn runtime_exposes_specific_preflight_failure_reasons() {
    assert_eq!(
        StartupError::SteamVrNotInstalled.failure_reason(),
        "steamvr_not_installed"
    );
    assert_eq!(
        StartupError::SteamVrNotRunning.failure_reason(),
        "steamvr_not_running"
    );
    assert_eq!(StartupError::HmdNotFound.failure_reason(), "hmd_not_found");
}

#[tokio::test]
async fn runtime_stops_cleanly_on_shutdown_event() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());

    runtime
        .handle_event(OverlayBridgeEvent::Shutdown)
        .await
        .unwrap();

    assert!(runtime.is_stopped());
}

#[tokio::test]
async fn runtime_reports_bridge_loss_as_runtime_disconnect_after_ready() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    runtime.mark_ready_for_test();

    let err = runtime.handle_bridge_loss_for_test().await.unwrap_err();

    assert_eq!(err.failure_reason(), "runtime_disconnected");
}

#[tokio::test]
async fn runtime_applies_new_snapshot_calibration_to_state() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration {
            anchor: "head_locked".into(),
            offset_x: 0.15,
            offset_y: -0.2,
            distance: 1.2,
            text_scale: 1.1,
            background_alpha: 0.4,
        },
        blocks: vec![],
    });

    assert_eq!(runtime.state().calibration().distance, 1.2);
    assert_eq!(runtime.state().calibration().background_alpha, 0.4);
}

#[tokio::test]
async fn runtime_emits_overlay_ready_only_after_first_texture_submit() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("ready-gating-success").await;
    let (mut bridge, server) = connect_test_bridge().await;
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    let mut submitter = RecordingSubmitter::default();

    assert!(!runtime.ready_sent());

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();

    drop(bridge);
    let messages = server.await.unwrap();

    assert_eq!(submitter.calls, 1);
    assert!(runtime.ready_sent());
    assert!(messages
        .iter()
        .any(|message| message["type"] == "overlay_ready"));
}

#[tokio::test]
async fn bridge_client_close_sends_close_frame() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let auth = ws.next().await.unwrap().unwrap();
        let Message::Text(auth_text) = auth else {
            panic!("expected auth text frame");
        };
        let auth_payload: serde_json::Value = serde_json::from_str(&auth_text).unwrap();
        assert_eq!(auth_payload["type"], "auth");

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 0,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [],
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        while let Some(message) = ws.next().await {
            match message.unwrap() {
                Message::Close(_) => return true,
                Message::Text(_) | Message::Binary(_) | Message::Ping(_) | Message::Pong(_) => {}
                Message::Frame(_) => {}
            }
        }

        false
    });

    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut client, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    assert!(snapshot.blocks.is_empty());

    client.close().await.unwrap();

    assert!(server.await.unwrap());
}

#[tokio::test]
async fn runtime_caption_blocks_keep_channel_metadata_for_color_only_rendering() {
    let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 3,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "hello", "안녕", true),
            block("peer:2", "peer", "세상", "world", false),
        ],
    });

    let blocks = runtime.caption_blocks();
    let channels = blocks
        .iter()
        .map(|block| {
            (
                block.id.as_str(),
                (
                    block.channel,
                    block.primary_text.as_str(),
                    block.secondary_enabled,
                ),
            )
        })
        .collect::<std::collections::BTreeMap<_, _>>();

    assert_eq!(
        channels.get("self:1"),
        Some(&(Some(CaptionChannel::SelfChannel), "hello", true))
    );
    assert_eq!(
        channels.get("peer:2"),
        Some(&(Some(CaptionChannel::PeerChannel), "세상", false))
    );
}

#[test]
fn runtime_uses_half_refresh_animation_sampling() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());

    runtime.set_refresh_rate_for_test(Some(72.0));
    assert_eq!(
        runtime.animation_interval_for_test(),
        Duration::from_secs_f32(1.0 / 36.0)
    );

    runtime.set_refresh_rate_for_test(Some(90.0));
    assert_eq!(
        runtime.animation_interval_for_test(),
        Duration::from_secs_f32(1.0 / 45.0)
    );

    runtime.set_refresh_rate_for_test(None);
    assert_eq!(
        runtime.animation_interval_for_test(),
        Duration::from_secs_f32(1.0 / 45.0)
    );
}

#[test]
fn runtime_keeps_missing_snapshot_blocks_visible_until_exit_animation_finishes() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello", "", true)],
    });

    runtime.advance_animation_for_test(Duration::from_secs_f32(1.0));
    runtime.clear_redraw_flag();
    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![],
    });

    assert_eq!(runtime.caption_blocks().len(), 1);
    assert_eq!(runtime.caption_blocks()[0].id, "self:1");

    runtime.advance_animation_for_test(Duration::from_secs_f32(1.0));

    assert!(runtime.caption_blocks().is_empty());
}

#[test]
fn runtime_keeps_two_finalized_rows_visible_when_active_self_is_present() {
    let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "first", "", true),
            block("peer:2", "peer", "second", "", true),
            active_self_block("self:active", "speaking"),
        ],
    });

    assert_eq!(
        runtime
            .caption_blocks()
            .iter()
            .map(|block| block.id.as_str())
            .collect::<Vec<_>>(),
        vec!["self:1", "peer:2", "self:active"]
    );
}

#[test]
fn runtime_reflow_tracks_actual_height_delta_when_secondary_slot_changes() {
    let policy = CaptionLayoutPolicy::default();
    let expected_before = policy.layout_blocks(
        vec![
            CaptionBlock::new("self:1", "hello").with_secondary_text("", false),
            CaptionBlock::new("peer:2", "second").with_secondary_text("", false),
        ],
        3840,
        4096,
    );
    let expected_after = policy.layout_blocks(
        vec![
            CaptionBlock::new("self:1", "hello").with_secondary_text("translated", true),
            CaptionBlock::new("peer:2", "second").with_secondary_text("", false),
        ],
        3840,
        4096,
    );
    let expected_shift = expected_after.visible_blocks[1].bounds.top_px
        - expected_before.visible_blocks[1].bounds.top_px;

    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "hello", "", false),
            block("peer:2", "peer", "second", "", false),
        ],
    });
    runtime.advance_animation_for_test(Duration::from_secs_f32(1.0));

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "hello", "translated", true),
            block("peer:2", "peer", "second", "", false),
        ],
    });

    let second = runtime
        .caption_blocks()
        .into_iter()
        .find(|block| block.id == "peer:2")
        .expect("peer block should remain visible");
    let first = runtime
        .caption_blocks()
        .into_iter()
        .find(|block| block.id == "self:1")
        .expect("self block should remain visible");

    assert_eq!(second.offset_y_px, -expected_shift);
    assert!(first.height_scale > 1.0);
}

#[test]
fn runtime_reflow_animation_still_renders_shifted_bounds_from_visual_state_only() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let initial_layout = CaptionLayoutPolicy::default().layout_blocks(
        vec![
            CaptionBlock::new("self:1", "hello")
                .with_channel(CaptionChannel::SelfChannel)
                .with_secondary_text("", false),
            CaptionBlock::new("peer:2", "second")
                .with_channel(CaptionChannel::PeerChannel)
                .with_secondary_text("", false),
        ],
        3840,
        4096,
    );
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "hello", "", false),
            block("peer:2", "peer", "second", "", false),
        ],
    });
    runtime.advance_animation_for_test(Duration::from_secs_f32(1.0));
    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "hello", "translated", true),
            block("peer:2", "peer", "second", "", false),
        ],
    });

    let animated = renderer.render_blocks(runtime.caption_blocks()).unwrap();
    let final_layout = CaptionLayoutPolicy::default().layout_blocks(
        vec![
            CaptionBlock::new("self:1", "hello")
                .with_channel(CaptionChannel::SelfChannel)
                .with_secondary_text("translated", true),
            CaptionBlock::new("peer:2", "second")
                .with_channel(CaptionChannel::PeerChannel)
                .with_secondary_text("", false),
        ],
        3840,
        4096,
    );

    let animated_self = animated
        .layout()
        .visible_blocks
        .iter()
        .find(|block| block.id == "self:1")
        .expect("animated self block should render");
    let animated_peer = animated
        .layout()
        .visible_blocks
        .iter()
        .find(|block| block.id == "peer:2")
        .expect("animated peer block should render");
    let initial_peer = initial_layout
        .visible_blocks
        .iter()
        .find(|block| block.id == "peer:2")
        .expect("initial peer block should render");
    let final_self = final_layout
        .visible_blocks
        .iter()
        .find(|block| block.id == "self:1")
        .expect("final self block should render");
    let final_peer = final_layout
        .visible_blocks
        .iter()
        .find(|block| block.id == "peer:2")
        .expect("final peer block should render");

    assert_eq!(animated_peer.bounds.top_px, initial_peer.bounds.top_px);
    assert_ne!(animated_peer.bounds.top_px, final_peer.bounds.top_px);
    assert!(
        animated_self.bounds.bottom_px - animated_self.bounds.top_px
            > final_self.bounds.bottom_px - final_self.bounds.top_px,
        "height_scale should still stretch the updated row during reflow"
    );
}

#[cfg(windows)]
#[test]
fn runtime_active_self_frames_do_not_hit_finalized_block_cache() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![active_self_block("self:active", "speaking now")],
    });

    renderer.render_blocks(runtime.caption_blocks()).unwrap();
    let second = renderer.render_blocks(runtime.caption_blocks()).unwrap();

    assert_eq!(second.diagnostics().block_cache_hits, 0);
    assert!(second.diagnostics().line_cache_hits >= 1);
}

#[test]
fn runtime_does_not_render_duplicate_row_when_same_id_reappears_during_exit() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello", "", true)],
    });
    runtime.advance_animation_for_test(Duration::from_secs_f32(1.0));

    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![],
    });
    runtime.apply_snapshot(OverlayPresentationSnapshot {
        revision: 3,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello again", "", true)],
    });

    assert_eq!(
        runtime
            .caption_blocks()
            .iter()
            .map(|block| block.id.as_str())
            .collect::<Vec<_>>(),
        vec!["self:1"]
    );
}

#[test]
fn runtime_requests_redraws_while_animation_is_active_and_stops_when_scene_is_idle() {
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello", "", true)],
    });

    assert!(runtime.redraw_requested());
    runtime.clear_redraw_flag();
    assert!(!runtime.redraw_requested());

    runtime.advance_animation_for_test(Duration::from_secs_f32(1.0 / 45.0));
    assert!(runtime.redraw_requested());

    runtime.clear_redraw_flag();
    runtime.advance_animation_for_test(Duration::from_secs_f32(1.0));
    assert!(runtime.redraw_requested());

    runtime.clear_redraw_flag();
    runtime.advance_animation_for_test(Duration::from_secs_f32(1.0));
    assert!(!runtime.redraw_requested());
}

#[tokio::test]
async fn runtime_does_not_emit_overlay_ready_when_first_texture_submit_fails() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("ready-gating-failure").await;
    let (mut bridge, server) = connect_test_bridge().await;
    let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot::default());
    let mut submitter = RecordingSubmitter::failing();

    let err = runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap_err();

    drop(bridge);
    let messages = server.await.unwrap();

    assert_eq!(submitter.calls, 1);
    assert!(matches!(err, RuntimeFailure::OpenVr(_)));
    assert!(!runtime.ready_sent());
    assert!(!messages
        .iter()
        .any(|message| message["type"] == "overlay_ready"));
}

#[tokio::test]
async fn runtime_hides_overlay_after_empty_state_stays_idle_past_delay() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let _ = ws.next().await.unwrap().unwrap();
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 1,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:1", "self", "hello", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let _ = ws.next().await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 2,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": []
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(650)).await;

        ws.send(Message::Text(
            json!({"type": "shutdown"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let logger = test_logger("idle-hide").await;
    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut bridge, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let mut runtime = OverlayRuntime::new(snapshot);
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();
    runtime
        .run_event_loop(&mut bridge, &renderer, &mut submitter, &logger)
        .await
        .unwrap();

    server.await.unwrap();

    assert!(submitter.visibility_changes.contains(&false));
}

#[tokio::test]
async fn runtime_cancels_pending_idle_hide_when_new_text_arrives() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let _ = ws.next().await.unwrap().unwrap();
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 1,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:1", "self", "hello", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let _ = ws.next().await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 2,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": []
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(250)).await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 3,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:2", "self", "back again", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(650)).await;

        ws.send(Message::Text(
            json!({"type": "shutdown"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let logger = test_logger("idle-hide-cancel").await;
    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut bridge, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let mut runtime = OverlayRuntime::new(snapshot);
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();
    runtime
        .run_event_loop(&mut bridge, &renderer, &mut submitter, &logger)
        .await
        .unwrap();

    server.await.unwrap();

    assert!(!submitter.visibility_changes.contains(&false));
}

#[tokio::test]
async fn runtime_shows_overlay_again_when_text_returns_after_idle_hide() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let _ = ws.next().await.unwrap().unwrap();
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 1,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:1", "self", "hello", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let _ = ws.next().await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 2,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": []
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(650)).await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 3,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:2", "self", "visible again", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(50)).await;

        ws.send(Message::Text(
            json!({"type": "shutdown"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let logger = test_logger("idle-hide-restore").await;
    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut bridge, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let mut runtime = OverlayRuntime::new(snapshot);
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();
    runtime
        .run_event_loop(&mut bridge, &renderer, &mut submitter, &logger)
        .await
        .unwrap();

    server.await.unwrap();

    assert!(submitter
        .visibility_changes
        .windows(2)
        .any(|pair| pair == [false, true]));
}

#[tokio::test]
async fn runtime_submits_text_frame_before_revealing_overlay_after_idle_hide() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let _ = ws.next().await.unwrap().unwrap();
        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 1,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:1", "self", "hello", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        let _ = ws.next().await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 2,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": []
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(650)).await;

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 3,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [block("self:2", "self", "visible again", "", true)]
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();

        tokio::time::sleep(Duration::from_millis(50)).await;

        ws.send(Message::Text(
            json!({"type": "shutdown"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let logger = test_logger("reveal-order-after-hide").await;
    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (mut bridge, snapshot) = BridgeClient::connect(&manifest).await.unwrap();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let mut runtime = OverlayRuntime::new(snapshot);
    let mut submitter = RecordingSubmitter::default();

    runtime
        .submit_frame_if_needed(&renderer, &mut submitter, &mut bridge, &logger)
        .await
        .unwrap();
    runtime
        .run_event_loop(&mut bridge, &renderer, &mut submitter, &logger)
        .await
        .unwrap();

    server.await.unwrap();

    let hide_index = submitter
        .operations
        .iter()
        .rposition(|operation| *operation == "hide")
        .expect("expected idle hide before reveal");
    assert_eq!(
        &submitter.operations[hide_index + 1..hide_index + 3],
        &["submit:text", "show"]
    );
}

#[tokio::test]
async fn bridge_client_authenticates_and_receives_initial_snapshot() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();

        let auth = ws.next().await.unwrap().unwrap();
        let Message::Text(auth_text) = auth else {
            panic!("expected auth text frame");
        };
        let auth_payload: serde_json::Value = serde_json::from_str(&auth_text).unwrap();
        assert_eq!(auth_payload["type"], "auth");
        assert_eq!(auth_payload["session_token"], "expected-token");

        ws.send(Message::Text(
            json!({
                "type": "snapshot",
                "payload": {
                    "revision": 0,
                    "calibration": OverlayPresentationCalibration::default(),
                    "blocks": [],
                }
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    });

    let mut manifest = test_manifest();
    manifest.bridge_url = format!("ws://{}", address);

    let (_client, snapshot) = BridgeClient::connect(&manifest).await.unwrap();

    server.await.unwrap();
    assert!(snapshot.blocks.is_empty());
}

#[test]
fn runtime_disconnect_failure_reason_is_stable() {
    assert_eq!(
        RuntimeFailure::RuntimeDisconnected.failure_reason(),
        "runtime_disconnected"
    );
}

#[test]
fn openvr_submission_uses_set_overlay_texture_for_rendered_frames() {
    let openvr = FakeOpenVr::default();
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let frame = renderer
        .render_blocks(vec![CaptionBlock::new("peer-1", "hello")])
        .unwrap();

    submit_texture(&openvr, &frame).unwrap();

    assert_eq!(openvr.last_call().as_deref(), Some("SetOverlayTexture"));
}

#[test]
fn check_startup_contract_reports_current_contract_version() {
    let output = Command::new(overlay_binary())
        .arg("--check-startup-contract")
        .output()
        .unwrap();

    assert!(output.status.success());
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["contract_version"], 4);
}

#[test]
fn validate_manifest_rejects_contract_version_mismatch() {
    let manifest = OverlayManifest {
        contract_version: EXPECTED_CONTRACT_VERSION + 1,
        ..test_manifest()
    };

    let error = validate_manifest(&manifest).unwrap_err();

    assert!(matches!(error, StartupError::ContractMismatch(_)));
}

#[tokio::test]
async fn run_with_manifest_reports_bridge_auth_failures_as_startup_errors() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();
        let _ = ws.next().await;
        ws.send(Message::Text(
            json!({"type": "auth_error"}).to_string().into(),
        ))
        .await
        .unwrap();
    });

    let log_dir = unique_log_dir("bridge-auth-failure");
    let exit_code = run_with_manifest(OverlayManifest {
        bridge_url: format!("ws://{}", address),
        log_dir,
        ..test_manifest()
    })
    .await;

    server.await.unwrap();
    assert_eq!(exit_code, StartupError::BridgeAuth("x".into()).exit_code());
}

#[test]
fn cli_requires_config_argument_or_supported_flags() {
    let output = Command::new(overlay_binary()).output().unwrap();

    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("usage:"));
}

#[test]
fn cli_emits_startup_failure_event_when_manifest_is_missing() {
    let missing_path = unique_temp_file("missing-manifest", "json");
    let output = Command::new(overlay_binary())
        .arg("--config")
        .arg(&missing_path)
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    let stderr_events = parse_event_payloads(&output.stderr);
    assert!(stderr_events
        .iter()
        .any(|event| event["type"] == "startup_error"));
}
