use puripuly_heart_overlay::{
    OverlayPresentationBlock, OverlayPresentationBlockVariant, OverlayPresentationCalibration,
    OverlayPresentationSnapshot, OverlayState, OverlayStripLifecycle,
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

#[test]
fn overlay_state_keeps_snapshot_blocks_in_order() {
    let mut state = OverlayState::default();

    state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "hello", "안녕", true),
            block("peer:2", "peer", "there", "원문", true),
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
    assert_eq!(state.blocks()[0].primary_text, "there");
    assert_eq!(state.blocks()[0].secondary_text, "원문");
    assert!(!state.blocks()[0].secondary_enabled);
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

    assert_eq!(state.snapshot().revision, 4);
    assert_eq!(state.blocks()[0].id, "self:4");
}

#[test]
fn overlay_state_marks_missing_snapshot_blocks_as_exiting_before_removal() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello", "", true)],
    }));

    assert!(state.sample_animations(1.0, 45));
    assert_eq!(
        state.scene().strips()[0].lifecycle,
        OverlayStripLifecycle::Stable
    );

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![],
    }));

    assert_eq!(state.scene().strips().len(), 1);
    assert_eq!(state.scene().strips()[0].id, "self:1");
    assert_eq!(
        state.scene().strips()[0].lifecycle,
        OverlayStripLifecycle::Exiting
    );

    assert!(state.sample_animations(1.0, 45));
    assert!(state.scene().strips().is_empty());
}

#[test]
fn overlay_state_updates_existing_strip_text_without_replaying_enter_animation() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello", "", true)],
    }));

    assert!(state.sample_animations(1.0, 45));
    let enter_progress = state.scene().strips()[0].enter_progress;

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello again", "second line", true)],
    }));

    assert_eq!(state.scene().strips().len(), 1);
    assert_eq!(state.scene().strips()[0].primary_text, "hello again");
    assert_eq!(state.scene().strips()[0].secondary_text, "second line");
    assert_eq!(
        state.scene().strips()[0].lifecycle,
        OverlayStripLifecycle::Stable
    );
    assert_eq!(state.scene().strips()[0].enter_progress, enter_progress);
}

#[test]
fn overlay_state_keeps_two_finalized_rows_when_active_self_is_present() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "first", "", true),
            block("peer:2", "peer", "second", "", true),
            active_self_block("self:active", "speaking now"),
        ],
    }));

    assert_eq!(
        state
            .scene()
            .strips()
            .iter()
            .map(|strip| strip.id.as_str())
            .collect::<Vec<_>>(),
        vec!["self:1", "peer:2", "self:active"]
    );
}

#[test]
fn overlay_state_uses_calibration_text_scale_for_strip_height() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello", "", true)],
    }));
    let default_height = state.scene().strips()[0].current_height_px;

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration {
            text_scale: 1.25,
            ..OverlayPresentationCalibration::default()
        },
        blocks: vec![block("self:1", "self", "hello", "", true)],
    }));
    let scaled_height = state.scene().strips()[0].current_height_px;

    assert!(scaled_height > default_height);
}

#[test]
fn overlay_state_keeps_only_the_most_recent_displaced_finalized_row_as_exiting() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:1", "self", "first", "", true),
            block("peer:2", "peer", "second", "", true),
        ],
    }));
    assert!(state.sample_animations(1.0, 45));

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![
            block("self:3", "self", "third", "", true),
            block("peer:4", "peer", "fourth", "", true),
        ],
    }));

    assert_eq!(
        state
            .scene()
            .strips()
            .iter()
            .map(|strip| (strip.id.as_str(), strip.lifecycle))
            .collect::<Vec<_>>(),
        vec![
            ("self:3", OverlayStripLifecycle::Entering),
            ("peer:4", OverlayStripLifecycle::Entering),
            ("peer:2", OverlayStripLifecycle::Exiting),
        ]
    );
}

#[test]
fn overlay_state_does_not_keep_exiting_duplicate_when_same_id_reappears() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello", "", true)],
    }));
    assert!(state.sample_animations(1.0, 45));

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![],
    }));
    assert_eq!(state.scene().strips().len(), 1);
    assert_eq!(
        state.scene().strips()[0].lifecycle,
        OverlayStripLifecycle::Exiting
    );

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 3,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello again", "", true)],
    }));

    assert_eq!(
        state
            .scene()
            .strips()
            .iter()
            .map(|strip| (strip.id.as_str(), strip.lifecycle))
            .collect::<Vec<_>>(),
        vec![("self:1", OverlayStripLifecycle::Stable)]
    );
}
