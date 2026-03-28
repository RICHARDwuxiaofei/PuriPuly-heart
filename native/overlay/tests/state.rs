use puripuly_heart_overlay::{Event, OverlayState, OverlayStateSnapshot, RowEvent, ShutdownEvent};

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
fn overlay_state_replaces_existing_rows_by_utterance_and_channel() {
    let mut state = OverlayState::default();

    state.apply(Event::PeerTranscriptFinal(test_row("peer", "peer-1", "hello")));
    state.apply(Event::TranslationFinal(test_row(
        "peer",
        "peer-1",
        "hello there",
    )));

    assert_eq!(state.rows_for("peer").len(), 1);
    assert_eq!(state.rows_for("peer")[0].text, "hello there");
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
