use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

use puripuly_heart_overlay::{
    run_with_manifest, submit_texture, validate_manifest, BridgeClient, CaptionBlock,
    CaptionChannel, CaptionRenderer, Event, FakeOpenVr, OpenVrError, OverlayBridgeEvent,
    OverlayCalibrationUpdateEvent, OverlayFrameSubmitter, OverlayManifest, OverlayRuntime,
    OverlayStateSnapshot, RenderedFrame, RowEvent, RuntimeFailure, StartupError,
    EXPECTED_CONTRACT_VERSION,
};
use puripuly_heart_overlay::logging::OverlayLogger;

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

fn test_row(channel: &str, utterance_id: &str, text: &str) -> RowEvent {
    RowEvent {
        event_id: format!("evt-{channel}-{utterance_id}"),
        seq: 1,
        utterance_id: utterance_id.to_string(),
        channel: channel.to_string(),
        text: text.to_string(),
        source_language: "en".to_string(),
        target_language: "ko".to_string(),
        created_at: 123.0,
        is_final: true,
        speaker_label: None,
        peer_epoch: None,
        applied_context_mode: None,
    }
}

fn test_snapshot() -> OverlayStateSnapshot {
    OverlayStateSnapshot {
        events: vec![Event::PeerTranscriptFinal(test_row("peer", "peer-1", "hello"))],
    }
}

fn test_peer_final_event() -> OverlayBridgeEvent {
    OverlayBridgeEvent::Live(Event::TranslationFinal(test_row(
        "peer",
        "peer-1",
        "hello there",
    )))
}

#[derive(Default)]
struct RecordingSubmitter {
    calls: usize,
    fail: bool,
}

impl RecordingSubmitter {
    fn failing() -> Self {
        Self {
            calls: 0,
            fail: true,
        }
    }
}

impl OverlayFrameSubmitter for RecordingSubmitter {
    fn submit_frame(&mut self, frame: &RenderedFrame) -> Result<(), OpenVrError> {
        self.calls += 1;
        if self.fail {
            return Err(OpenVrError::Submit("submit failed".into()));
        }
        assert_eq!(frame.width(), 3840);
        assert_eq!(frame.height(), 1024);
        Ok(())
    }
}

async fn connect_test_bridge() -> (BridgeClient, tokio::task::JoinHandle<Vec<serde_json::Value>>) {
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
            json!({"type": "snapshot", "payload": {"events": []}})
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
    assert!(snapshot.events.is_empty());
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
    assert_eq!(StartupError::OpenVrInit("steamvr missing".into()).exit_code(), 20);
    assert_eq!(StartupError::RendererInit("d3d init failed".into()).exit_code(), 21);
}

#[tokio::test]
async fn runtime_stops_cleanly_on_shutdown_event() {
    let mut runtime = OverlayRuntime::new(OverlayStateSnapshot::default());

    runtime
        .handle_event(OverlayBridgeEvent::Shutdown)
        .await
        .unwrap();

    assert!(runtime.is_stopped());
}

#[tokio::test]
async fn runtime_reports_bridge_loss_as_runtime_disconnect_after_ready() {
    let mut runtime = OverlayRuntime::new(OverlayStateSnapshot::default());
    runtime.mark_ready_for_test();

    let err = runtime.handle_bridge_loss_for_test().await.unwrap_err();

    assert_eq!(err.failure_reason(), "runtime_disconnected");
}

#[tokio::test]
async fn runtime_applies_overlay_calibration_updates_to_state() {
    let mut runtime = OverlayRuntime::new(OverlayStateSnapshot::default());

    runtime
        .handle_event(OverlayBridgeEvent::Live(Event::OverlayCalibrationUpdate(
            OverlayCalibrationUpdateEvent {
                event_id: "evt-calibration".into(),
                seq: 1,
                created_at: 100.0,
                anchor: "head_locked".into(),
                offset_x: 0.15,
                offset_y: -0.2,
                distance: 1.2,
                text_scale: 1.1,
                background_alpha: 0.4,
            },
        )))
        .await
        .unwrap();

    assert_eq!(runtime.state().calibration().distance, 1.2);
    assert_eq!(runtime.state().calibration().background_alpha, 0.4);
}

#[tokio::test]
async fn runtime_emits_overlay_ready_only_after_first_texture_submit() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("ready-gating-success").await;
    let (mut bridge, server) = connect_test_bridge().await;
    let mut runtime = OverlayRuntime::new(OverlayStateSnapshot::default());
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
    assert!(messages.iter().any(|message| message["type"] == "overlay_ready"));
}

#[tokio::test]
async fn runtime_caption_blocks_keep_channel_metadata_for_color_only_rendering() {
    let runtime = OverlayRuntime::new(OverlayStateSnapshot {
        events: vec![
            Event::SelfTranscriptFinal(test_row("self", "self-1", "hello")),
            Event::TranslationFinal(test_row("self", "self-1", "안녕")),
            Event::PeerTranscriptFinal(test_row("peer", "peer-1", "world")),
            Event::TranslationFinal(test_row("peer", "peer-1", "세상")),
        ],
    });

    let blocks = runtime.caption_blocks();
    let channels = blocks
        .iter()
        .map(|block| (block.id.as_str(), block.channel))
        .collect::<std::collections::BTreeMap<_, _>>();

    assert_eq!(channels.get("self:self-1"), Some(&Some(CaptionChannel::SelfChannel)));
    assert_eq!(channels.get("peer:peer-1"), Some(&Some(CaptionChannel::PeerChannel)));
}

#[tokio::test]
async fn runtime_does_not_emit_overlay_ready_when_first_texture_submit_fails() {
    let renderer = CaptionRenderer::new_for_test().unwrap();
    let logger = test_logger("ready-gating-failure").await;
    let (mut bridge, server) = connect_test_bridge().await;
    let mut runtime = OverlayRuntime::new(OverlayStateSnapshot::default());
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
    assert!(!messages.iter().any(|message| message["type"] == "overlay_ready"));
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
            json!({"type": "snapshot", "payload": {"events": []}})
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
    assert!(snapshot.events.is_empty());
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

#[tokio::test]
async fn runtime_startup_connect_failure_is_not_reported_as_bridge_auth() {
    let mut manifest = test_manifest();
    manifest.bridge_url = "ws://127.0.0.1:9".into();
    manifest.log_dir = unique_log_dir("connect-failure");

    let exit_code = run_with_manifest(manifest.clone()).await;
    let log_path = std::path::Path::new(&manifest.log_dir).join("puripuly_heart_overlay.log");
    let log_contents = std::fs::read_to_string(log_path).unwrap();

    assert_eq!(exit_code, 1);
    assert!(log_contents.contains("bridge startup failed"));
    assert!(log_contents.contains("Connection refused"));
    assert!(!log_contents.contains("bridge auth failed"));
}

#[test]
fn binary_emits_structured_manifest_invalid_startup_error() {
    let manifest_path = unique_temp_file("invalid-manifest", "json");
    std::fs::write(&manifest_path, "{ invalid json").unwrap();

    let output = Command::new(overlay_binary())
        .args(["--config", manifest_path.to_str().unwrap()])
        .output()
        .unwrap();

    let events = parse_event_payloads(&output.stderr);

    assert!(!output.status.success());
    assert!(events.iter().any(|event| {
        event["type"] == "startup_error" && event["failure_reason"] == "manifest_invalid"
    }));
}

#[test]
fn binary_reports_startup_contract_for_smoke_checks() {
    let output = Command::new(overlay_binary())
        .arg("--check-startup-contract")
        .output()
        .unwrap();

    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();

    assert!(output.status.success());
    assert_eq!(payload["contract_version"], EXPECTED_CONTRACT_VERSION);
    assert_eq!(payload["app_version"], env!("CARGO_PKG_VERSION"));
}

#[tokio::test]
async fn binary_reports_non_auth_bridge_startup_failure_as_unknown() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    drop(listener);

    let manifest = OverlayManifest {
        bridge_url: format!("ws://{}", address),
        log_dir: unique_log_dir("connect-failure-binary"),
        ..test_manifest()
    };
    let manifest_path = unique_temp_file("connect-failure", "json");
    std::fs::write(
        &manifest_path,
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();

    let output = Command::new(overlay_binary())
        .args(["--config", manifest_path.to_str().unwrap()])
        .output()
        .unwrap();
    let events = parse_event_payloads(&output.stderr);

    assert!(!output.status.success());
    assert!(events.iter().any(|event| {
        event["type"] == "startup_error" && event["failure_reason"] == "unknown"
    }));
    assert!(!events.iter().any(|event| {
        event["type"] == "startup_error" && event["failure_reason"] == "bridge_auth_failed"
    }));
}

#[tokio::test]
async fn runtime_schedules_redraws_from_snapshot_and_live_events() {
    let mut runtime = OverlayRuntime::new(OverlayStateSnapshot::default());

    runtime.apply_snapshot(test_snapshot());
    assert!(runtime.redraw_requested());

    runtime.clear_redraw_flag();
    runtime.handle_event(test_peer_final_event()).await.unwrap();

    assert!(runtime.redraw_requested());
}

#[tokio::test]
async fn runtime_ignores_duplicate_snapshot_for_redraw() {
    let snapshot = test_snapshot();
    let mut runtime = OverlayRuntime::new(snapshot.clone());

    runtime.clear_redraw_flag();
    runtime.apply_snapshot(snapshot);

    assert!(!runtime.redraw_requested());
}

#[tokio::test]
async fn runtime_ignores_duplicate_live_events_for_redraw() {
    let mut runtime = OverlayRuntime::new(OverlayStateSnapshot::default());
    let event = test_peer_final_event();

    runtime.handle_event(event.clone()).await.unwrap();
    assert!(runtime.redraw_requested());

    runtime.clear_redraw_flag();
    runtime.handle_event(event).await.unwrap();

    assert!(!runtime.redraw_requested());
}
