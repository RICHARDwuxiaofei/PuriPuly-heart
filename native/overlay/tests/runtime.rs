use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

use puripuly_heart_overlay::{
    run_with_manifest, validate_manifest, BridgeClient, Event, OverlayBridgeEvent,
    OverlayManifest, OverlayRuntime, OverlayStateSnapshot, RowEvent, RuntimeFailure,
    StartupError, EXPECTED_CONTRACT_VERSION,
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
async fn runtime_ignores_duplicate_live_events_for_redraw() {
    let mut runtime = OverlayRuntime::new(OverlayStateSnapshot::default());
    let event = test_peer_final_event();

    runtime.handle_event(event.clone()).await.unwrap();
    assert!(runtime.redraw_requested());

    runtime.clear_redraw_flag();
    runtime.handle_event(event).await.unwrap();

    assert!(!runtime.redraw_requested());
}
