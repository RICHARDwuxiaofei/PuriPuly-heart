use puripuly_heart_overlay::{
    OverlayPresentationBlock, OverlayPresentationBlockVariant, OverlayPresentationCalibration,
    OverlayPresentationSnapshot, OverlayState,
};

fn block(
    id: &str,
    channel: &str,
    primary_text: &str,
    secondary_text: &str,
    secondary_enabled: bool,
) -> OverlayPresentationBlock {
    OverlayPresentationBlock {
        id: id.to_string(),
        occupant_key: id.to_string(),
        appearance_seq: 1,
        channel: channel.to_string(),
        block_variant: OverlayPresentationBlockVariant::Finalized,
        primary_text: primary_text.to_string(),
        secondary_text: secondary_text.to_string(),
        secondary_enabled,
    }
}

fn slot_block(
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
fn overlay_state_keeps_snapshot_blocks_in_order() {
    let mut state = OverlayState::default();

    state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "hello", "안녕", true),
            slot_block("peer:2", "peer:2", 2, "peer", "there", "원문", true),
        ],
    });

    assert_eq!(state.blocks().len(), 2);
    assert_eq!(state.blocks()[0].id, "self:1");
    assert_eq!(state.blocks()[1].id, "peer:2");
}

#[test]
fn overlay_state_snapshot_replaces_stale_blocks() {
    let mut state = OverlayState::default();

    state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello", "", true)],
    });

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:2", "peer", "there", "원문", false)],
    }));

    assert_eq!(state.blocks().len(), 1);
    assert_eq!(state.blocks()[0].id, "peer:2");
}

#[test]
fn overlay_state_tracks_latest_snapshot_calibration() {
    let mut state = OverlayState::default();

    assert_eq!(state.calibration().distance, 1.1);

    state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 3,
        calibration: OverlayPresentationCalibration {
            anchor: "head_locked".to_string(),
            offset_x: 0.15,
            offset_y: -0.2,
            distance: 1.2,
            text_scale: 1.1,
            background_alpha: 0.4,
        },
        blocks: vec![],
    });

    assert_eq!(state.calibration().distance, 1.2);
    assert_eq!(state.calibration().background_alpha, 0.4);
}

#[test]
fn overlay_state_ignores_lower_revision_snapshots() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 3,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:3", "self", "latest", "", true)],
    }));

    assert!(!state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:2", "peer", "stale", "", true)],
    }));

    assert_eq!(state.snapshot().revision, 3);
    assert_eq!(state.blocks()[0].id, "self:3");
}

#[test]
fn overlay_state_treats_equal_revision_snapshots_as_noop() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 4,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:4", "self", "keep", "", true)],
    }));

    assert!(!state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 4,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:4", "peer", "ignore", "", true)],
    }));
}

#[test]
fn overlay_state_keeps_slot_two_anchor_when_slot_one_disappears() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "one", "", true),
            slot_block("peer:2", "peer:2", 2, "peer", "two", "", true),
        ],
    }));

    let second_top = state.scene().slots()[1].as_ref().unwrap().anchor_top_px;

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![slot_block("peer:2", "peer:2", 2, "peer", "two", "", true)],
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
    assert!(state.sample_animations(0.06, 45));
    let started = state.scene().slots()[0].as_ref().unwrap().accent_started_at_s;
    let progress = state.scene().slots()[0].as_ref().unwrap().accent_progress;
    assert!(progress > 0.0);
    assert!(progress < 1.0);

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![slot_block(
            "self:merge-1",
            "self:merge-1",
            1,
            "self",
            "hello live",
            "",
            true,
        )],
    }));

    let slot = state.scene().slots()[0].as_ref().unwrap();
    assert_eq!(slot.occupant_key, "self:merge-1");
    assert_eq!(slot.accent_started_at_s, started);
    assert_eq!(slot.accent_progress, progress);
}

#[test]
fn overlay_state_fills_first_empty_slot_before_replacing_again() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:1", "self:1", 1, "self", "one", "", true),
            slot_block("peer:2", "peer:2", 2, "peer", "two", "", true),
        ],
    }));

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![slot_block("peer:2", "peer:2", 2, "peer", "two", "", true)],
    }));

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 3,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            slot_block("self:3", "self:3", 3, "self", "three", "", true),
            slot_block("peer:2", "peer:2", 2, "peer", "two", "", true),
        ],
    }));

    assert_eq!(
        state
            .scene()
            .slots()
            .iter()
            .map(|slot| slot.as_ref().map(|slot| slot.id.clone()))
            .collect::<Vec<_>>(),
        vec![Some("self:3".to_string()), Some("peer:2".to_string())]
    );
}
