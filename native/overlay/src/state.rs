use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OverlayPresentationCalibration {
    #[serde(default = "default_anchor")]
    pub anchor: String,
    #[serde(default)]
    pub offset_x: f32,
    #[serde(default)]
    pub offset_y: f32,
    #[serde(default = "default_distance")]
    pub distance: f32,
    #[serde(default = "default_text_scale")]
    pub text_scale: f32,
    #[serde(default = "default_background_alpha")]
    pub background_alpha: f32,
}

impl Default for OverlayPresentationCalibration {
    fn default() -> Self {
        Self {
            anchor: default_anchor(),
            offset_x: 0.0,
            offset_y: 0.0,
            distance: default_distance(),
            text_scale: default_text_scale(),
            background_alpha: default_background_alpha(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OverlayPresentationBlock {
    pub id: String,
    pub channel: String,
    #[serde(default)]
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct OverlayPresentationSnapshot {
    #[serde(default)]
    pub revision: u64,
    #[serde(default)]
    pub calibration: OverlayPresentationCalibration,
    #[serde(default)]
    pub blocks: Vec<OverlayPresentationBlock>,
}

pub type OverlayCalibration = OverlayPresentationCalibration;
pub type OverlayStateSnapshot = OverlayPresentationSnapshot;

#[derive(Debug, Clone, PartialEq, Default)]
pub struct OverlayState {
    snapshot: OverlayPresentationSnapshot,
}

impl OverlayState {
    pub fn apply_snapshot(&mut self, snapshot: &OverlayPresentationSnapshot) -> bool {
        if snapshot.revision < self.snapshot.revision {
            return false;
        }
        if snapshot.revision == self.snapshot.revision {
            return false;
        }

        let visual_changed = self.snapshot.calibration != snapshot.calibration
            || self.snapshot.blocks != snapshot.blocks;
        self.snapshot = snapshot.clone();
        visual_changed
    }

    pub fn snapshot(&self) -> &OverlayPresentationSnapshot {
        &self.snapshot
    }

    pub fn calibration(&self) -> &OverlayPresentationCalibration {
        &self.snapshot.calibration
    }

    pub fn blocks(&self) -> &[OverlayPresentationBlock] {
        &self.snapshot.blocks
    }
}

fn default_anchor() -> String {
    "head_locked".to_string()
}

fn default_distance() -> f32 {
    1.1
}

fn default_text_scale() -> f32 {
    1.0
}

fn default_background_alpha() -> f32 {
    0.24
}

#[cfg(test)]
mod tests {
    use super::{
        OverlayPresentationBlock, OverlayPresentationCalibration, OverlayPresentationSnapshot,
        OverlayState,
    };

    #[test]
    fn apply_snapshot_replaces_render_state() {
        let mut state = OverlayState::default();

        assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
            revision: 1,
            calibration: OverlayPresentationCalibration::default(),
            blocks: vec![OverlayPresentationBlock {
                id: "self:1".to_string(),
                channel: "self".to_string(),
                text: "hello".to_string(),
            }],
        }));

        assert_eq!(state.snapshot().revision, 1);
        assert_eq!(state.blocks().len(), 1);
        assert_eq!(state.blocks()[0].text, "hello");
    }

    #[test]
    fn apply_snapshot_is_noop_for_identical_state() {
        let snapshot = OverlayPresentationSnapshot {
            revision: 2,
            calibration: OverlayPresentationCalibration::default(),
            blocks: vec![],
        };
        let mut state = OverlayState::default();

        assert!(!state.apply_snapshot(&snapshot));
        assert_eq!(state.snapshot().revision, 2);
        assert!(!state.apply_snapshot(&snapshot));
    }
}
