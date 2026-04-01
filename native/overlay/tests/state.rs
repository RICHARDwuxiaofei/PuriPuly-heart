use puripuly_heart_overlay::{
    OverlayPresentationBlock, OverlayPresentationCalibration, OverlayPresentationSnapshot,
    OverlayState, OverlayStripLifecycle,
};

fn block(id: &str, channel: &str, text: &str) -> OverlayPresentationBlock {
    OverlayPresentationBlock {
        id: id.to_string(),
        channel: channel.to_string(),
        text: text.to_string(),
    }
}

#[test]
fn overlay_state_keeps_snapshot_blocks_in_order() {
    let mut state = OverlayState::default();

    state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello"), block("peer:2", "peer", "there")],
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
        blocks: vec![block("self:1", "self", "hello")],
    });

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:2", "peer", "there")],
    }));

    assert_eq!(state.blocks().len(), 1);
    assert_eq!(state.blocks()[0].id, "peer:2");
    assert_eq!(state.blocks()[0].text, "there");
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
        blocks: vec![block("self:3", "self", "latest")],
    }));

    assert!(!state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:2", "peer", "stale")],
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
        blocks: vec![block("self:4", "self", "keep")],
    }));

    assert!(!state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 4,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("peer:4", "peer", "ignore")],
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
        blocks: vec![block("self:1", "self", "hello")],
    }));

    assert!(state.sample_animations(1.0, 45));
    assert_eq!(state.scene().strips()[0].lifecycle, OverlayStripLifecycle::Stable);

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![],
    }));

    assert_eq!(state.scene().strips().len(), 1);
    assert_eq!(state.scene().strips()[0].id, "self:1");
    assert_eq!(state.scene().strips()[0].lifecycle, OverlayStripLifecycle::Exiting);

    assert!(state.sample_animations(1.0, 45));
    assert!(state.scene().strips().is_empty());
}

#[test]
fn overlay_state_updates_existing_strip_text_without_replaying_enter_animation() {
    let mut state = OverlayState::default();

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 1,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello")],
    }));

    assert!(state.sample_animations(1.0, 45));
    let enter_progress = state.scene().strips()[0].enter_progress;

    assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
        revision: 2,
        calibration: OverlayPresentationCalibration::default(),
        blocks: vec![block("self:1", "self", "hello again")],
    }));

    assert_eq!(state.scene().strips().len(), 1);
    assert_eq!(state.scene().strips()[0].text, "hello again");
    assert_eq!(state.scene().strips()[0].lifecycle, OverlayStripLifecycle::Stable);
    assert_eq!(state.scene().strips()[0].enter_progress, enter_progress);
}
