use std::collections::HashMap;

use serde::{Deserialize, Serialize};

const ENTER_DURATION_SECONDS: f32 = 0.18;
const EXIT_DURATION_SECONDS: f32 = 0.12;
const REFLOW_DURATION_SECONDS: f32 = 0.12;

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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OverlayStripLifecycle {
    Entering,
    Stable,
    Exiting,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OverlayStrip {
    pub id: String,
    pub channel: String,
    pub text: String,
    pub order: usize,
    pub previous_order: usize,
    pub lifecycle: OverlayStripLifecycle,
    pub enter_progress: f32,
    pub exit_progress: f32,
    pub reflow_progress: f32,
}

impl OverlayStrip {
    fn new(block: &OverlayPresentationBlock, order: usize) -> Self {
        Self {
            id: block.id.clone(),
            channel: block.channel.clone(),
            text: block.text.clone(),
            order,
            previous_order: order,
            lifecycle: OverlayStripLifecycle::Entering,
            enter_progress: 0.0,
            exit_progress: 0.0,
            reflow_progress: 1.0,
        }
    }

    pub fn is_animating(&self) -> bool {
        self.lifecycle != OverlayStripLifecycle::Stable || self.reflow_progress < 1.0
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct OverlayScene {
    strips: Vec<OverlayStrip>,
}

impl OverlayScene {
    pub fn strips(&self) -> &[OverlayStrip] {
        &self.strips
    }

    fn apply_snapshot(&mut self, blocks: &[OverlayPresentationBlock]) -> bool {
        let previous = self.strips.clone();
        let mut existing_by_id: HashMap<String, OverlayStrip> = self
            .strips
            .drain(..)
            .map(|strip| (strip.id.clone(), strip))
            .collect();
        let mut next_strips = Vec::with_capacity(previous.len().max(blocks.len()));

        for (order, block) in blocks.iter().enumerate() {
            let mut strip = existing_by_id
                .remove(&block.id)
                .unwrap_or_else(|| OverlayStrip::new(block, order));
            let previous_order = strip.order;
            let was_entering = strip.lifecycle == OverlayStripLifecycle::Entering
                && strip.enter_progress < 1.0;

            strip.id = block.id.clone();
            strip.channel = block.channel.clone();
            strip.text = block.text.clone();
            strip.order = order;
            if previous_order != order {
                strip.previous_order = previous_order;
                strip.reflow_progress = 0.0;
            } else if strip.reflow_progress >= 1.0 {
                strip.previous_order = order;
                strip.reflow_progress = 1.0;
            }
            strip.exit_progress = 0.0;
            strip.lifecycle = if was_entering {
                OverlayStripLifecycle::Entering
            } else {
                OverlayStripLifecycle::Stable
            };
            next_strips.push(strip);
        }

        let mut exiting: Vec<OverlayStrip> = existing_by_id
            .into_values()
            .map(|mut strip| {
                if strip.lifecycle != OverlayStripLifecycle::Exiting {
                    strip.lifecycle = OverlayStripLifecycle::Exiting;
                    strip.exit_progress = 0.0;
                }
                strip
            })
            .collect();
        exiting.sort_by_key(|strip| strip.order);
        next_strips.extend(exiting);

        let changed = previous != next_strips;
        self.strips = next_strips;
        changed
    }

    fn sample_animations(&mut self, delta_seconds: f32, _sample_rate_hz: u32) -> bool {
        let previous = self.strips.clone();

        for strip in &mut self.strips {
            match strip.lifecycle {
                OverlayStripLifecycle::Entering => {
                    strip.enter_progress =
                        (strip.enter_progress + delta_seconds / ENTER_DURATION_SECONDS).min(1.0);
                    if strip.enter_progress >= 1.0 {
                        strip.lifecycle = OverlayStripLifecycle::Stable;
                    }
                }
                OverlayStripLifecycle::Stable => {}
                OverlayStripLifecycle::Exiting => {
                    strip.exit_progress =
                        (strip.exit_progress + delta_seconds / EXIT_DURATION_SECONDS).min(1.0);
                }
            }

            if strip.reflow_progress < 1.0 {
                strip.reflow_progress =
                    (strip.reflow_progress + delta_seconds / REFLOW_DURATION_SECONDS).min(1.0);
                if strip.reflow_progress >= 1.0 {
                    strip.previous_order = strip.order;
                }
            }
        }

        self.strips.retain(|strip| {
            !(strip.lifecycle == OverlayStripLifecycle::Exiting && strip.exit_progress >= 1.0)
        });

        previous != self.strips
    }

    pub fn has_active_animation(&self) -> bool {
        self.strips.iter().any(OverlayStrip::is_animating)
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct OverlayState {
    snapshot: OverlayPresentationSnapshot,
    scene: OverlayScene,
}

impl OverlayState {
    pub fn apply_snapshot(&mut self, snapshot: &OverlayPresentationSnapshot) -> bool {
        if snapshot.revision < self.snapshot.revision {
            return false;
        }
        if snapshot.revision == self.snapshot.revision {
            return false;
        }

        let scene_changed = self.scene.apply_snapshot(&snapshot.blocks);
        let visual_changed = self.snapshot.calibration != snapshot.calibration || scene_changed;
        self.snapshot = snapshot.clone();
        visual_changed
    }

    pub fn sample_animations(&mut self, delta_seconds: f32, sample_rate_hz: u32) -> bool {
        self.scene.sample_animations(delta_seconds, sample_rate_hz)
    }

    pub fn has_active_animation(&self) -> bool {
        self.scene.has_active_animation()
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

    pub fn scene(&self) -> &OverlayScene {
        &self.scene
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
        OverlayState, OverlayStripLifecycle,
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
        assert_eq!(state.scene().strips()[0].lifecycle, OverlayStripLifecycle::Entering);
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
