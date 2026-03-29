use puripuly_heart_overlay::{
    Event, OverlayCalibrationUpdateEvent, OverlayState, OverlayStateSnapshot, RowEvent,
    ShutdownEvent,
};

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

#[test]
fn overlay_state_keeps_self_and_peer_rows_separate() {
    let mut state = OverlayState::default();

    state.apply(Event::SelfTranscriptFinal(test_row("self", "self-1", "hello")));
    state.apply(Event::PeerTranscriptFinal(test_row("peer", "peer-1", "there")));

    assert_eq!(state.rows_for("self").len(), 1);
    assert_eq!(state.rows_for("peer").len(), 1);
}

#[test]
fn overlay_state_keeps_original_and_translation_rows_for_same_utterance() {
    let mut state = OverlayState::default();

    state.apply(Event::PeerTranscriptFinal(test_row("peer", "peer-1", "hello")));
    state.apply(Event::TranslationFinal(test_row(
        "peer",
        "peer-1",
        "hello there",
    )));

    assert_eq!(state.rows_for("peer").len(), 2);
    assert_eq!(state.rows_for("peer")[0].text, "hello");
    assert_eq!(state.rows_for("peer")[1].text, "hello there");
}

#[test]
fn utterance_closed_marks_original_and_translation_rows_closed_together() {
    let mut state = OverlayState::default();

    state.apply(Event::PeerTranscriptFinal(test_row("peer", "peer-1", "hello")));
    state.apply(Event::TranslationFinal(test_row(
        "peer",
        "peer-1",
        "hello there",
    )));
    state.apply(Event::UtteranceClosed(puripuly_heart_overlay::UtteranceClosedEvent {
        event_id: "evt-close".to_string(),
        seq: 2,
        utterance_id: "peer-1".to_string(),
        channel: "peer".to_string(),
        created_at: 124.0,
        is_final: true,
    }));

    assert!(state.rows_for("peer").iter().all(|row| row.closed));
}

#[test]
fn overlay_state_ignores_shutdown_events_from_snapshots() {
    let mut state = OverlayState::default();
    let snapshot = OverlayStateSnapshot {
        events: vec![Event::Shutdown(ShutdownEvent {
            event_id: "evt-shutdown".to_string(),
            seq: 99,
            utterance_id: None,
            channel: None,
            created_at: 999.0,
        })],
    };

    assert!(!state.apply_snapshot(&snapshot));
    assert!(state.rows_for("self").is_empty());
    assert!(state.rows_for("peer").is_empty());
}

#[test]
fn overlay_state_snapshot_replaces_stale_rows() {
    let mut state = OverlayState::default();

    state.apply(Event::SelfTranscriptFinal(test_row("self", "self-1", "hello")));
    assert_eq!(state.rows_for("self").len(), 1);

    let snapshot = OverlayStateSnapshot {
        events: vec![Event::PeerTranscriptFinal(test_row("peer", "peer-1", "there"))],
    };

    assert!(state.apply_snapshot(&snapshot));
    assert!(state.rows_for("self").is_empty());
    assert_eq!(state.rows_for("peer").len(), 1);
    assert_eq!(state.rows_for("peer")[0].text, "there");
}

#[test]
fn overlay_state_tracks_latest_overlay_calibration_update() {
    let mut state = OverlayState::default();

    assert_eq!(state.calibration().distance, 1.0);

    state.apply(Event::OverlayCalibrationUpdate(OverlayCalibrationUpdateEvent {
        event_id: "evt-calibration".to_string(),
        seq: 3,
        created_at: 200.0,
        anchor: "head_locked".to_string(),
        offset_x: 0.15,
        offset_y: -0.2,
        distance: 1.2,
        text_scale: 1.1,
        background_alpha: 0.4,
    }));

    assert_eq!(state.calibration().distance, 1.2);
    assert_eq!(state.calibration().background_alpha, 0.4);
}
