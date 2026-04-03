use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::renderer::CaptionLayoutPolicy;

const ENTER_DURATION_SECONDS: f32 = 0.18;
const EXIT_DURATION_SECONDS: f32 = 0.12;
const REFLOW_DURATION_SECONDS: f32 = 0.12;
const BLOCK_SPACING_PX: f32 = 36.0;

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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum OverlayPresentationBlockVariant {
    ActiveSelf,
    #[default]
    Finalized,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OverlayPresentationBlock {
    pub id: String,
    pub channel: String,
    pub block_variant: OverlayPresentationBlockVariant,
    pub primary_text: String,
    #[serde(default)]
    pub secondary_text: String,
    #[serde(default = "default_secondary_enabled")]
    pub secondary_enabled: bool,
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
    pub block_variant: OverlayPresentationBlockVariant,
    pub primary_text: String,
    pub secondary_text: String,
    pub secondary_enabled: bool,
    pub order: usize,
    pub previous_order: usize,
    pub lifecycle: OverlayStripLifecycle,
    pub enter_progress: f32,
    pub exit_progress: f32,
    pub reflow_progress: f32,
    pub previous_top_px: f32,
    pub current_top_px: f32,
    pub previous_height_px: f32,
    pub current_height_px: f32,
}

impl OverlayStrip {
    fn new(block: &OverlayPresentationBlock, order: usize, text_scale: f32) -> Self {
        let height_px = measured_block_height_px(block, text_scale);
        Self {
            id: block.id.clone(),
            channel: block.channel.clone(),
            block_variant: block.block_variant,
            primary_text: block.primary_text.clone(),
            secondary_text: block.secondary_text.clone(),
            secondary_enabled: block.secondary_enabled,
            order,
            previous_order: order,
            lifecycle: OverlayStripLifecycle::Entering,
            enter_progress: 0.0,
            exit_progress: 0.0,
            reflow_progress: 1.0,
            previous_top_px: 0.0,
            current_top_px: 0.0,
            previous_height_px: height_px,
            current_height_px: height_px,
        }
    }

    pub fn is_animating(&self) -> bool {
        self.lifecycle != OverlayStripLifecycle::Stable || self.reflow_progress < 1.0
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct OverlayScene {
    stable_finalized: Vec<OverlayStrip>,
    exiting_finalized: Vec<OverlayStrip>,
    active_self: Option<OverlayStrip>,
    strips: Vec<OverlayStrip>,
}

impl OverlayScene {
    pub fn strips(&self) -> &[OverlayStrip] {
        &self.strips
    }

    fn apply_snapshot(&mut self, blocks: &[OverlayPresentationBlock], text_scale: f32) -> bool {
        let previous = self.strips.clone();
        let mut existing_by_id: HashMap<String, OverlayStrip> = previous
            .iter()
            .cloned()
            .map(|strip| (strip.id.clone(), strip))
            .collect();
        let finalized_blocks = select_stable_finalized_blocks(blocks);
        let mut next_stable = finalized_blocks
            .iter()
            .enumerate()
            .map(|(order, block)| build_live_strip(&mut existing_by_id, block, order, text_scale))
            .collect::<Vec<_>>();
        let mut next_top_px = 0.0;
        for (order, strip) in next_stable.iter_mut().enumerate() {
            assign_live_layout(strip, order, next_top_px);
            next_top_px += strip.current_height_px + BLOCK_SPACING_PX;
        }

        let next_active = blocks
            .iter()
            .rev()
            .find(|block| block.block_variant == OverlayPresentationBlockVariant::ActiveSelf)
            .map(|block| {
                let order = next_stable.len();
                let mut strip = build_live_strip(&mut existing_by_id, block, order, text_scale);
                assign_live_layout(&mut strip, order, next_top_px);
                strip
            });
        if let Some(active_strip) = &next_active {
            next_top_px += active_strip.current_height_px + BLOCK_SPACING_PX;
        }

        let live_ids = next_stable
            .iter()
            .map(|strip| strip.id.as_str())
            .chain(next_active.iter().map(|strip| strip.id.as_str()))
            .collect::<Vec<_>>();
        let removed_stable = self
            .stable_finalized
            .iter()
            .filter(|strip| !live_ids.contains(&strip.id.as_str()))
            .cloned()
            .max_by_key(|strip| strip.order);
        let exiting_candidate = removed_stable.or_else(|| {
            self.exiting_finalized
                .iter()
                .find(|strip| strip.exit_progress < 1.0 && !live_ids.contains(&strip.id.as_str()))
                .cloned()
        });
        let next_exiting = exiting_candidate.map(|strip| {
            let order = next_stable.len() + usize::from(next_active.is_some());
            build_exiting_strip(strip, order, next_top_px)
        });

        self.stable_finalized = next_stable;
        self.active_self = next_active;
        self.exiting_finalized = next_exiting.into_iter().collect();
        self.rebuild_strips();

        let changed = previous != self.strips;
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
                    strip.previous_top_px = strip.current_top_px;
                    strip.previous_height_px = strip.current_height_px;
                }
            }
        }

        self.strips.retain(|strip| {
            !(strip.lifecycle == OverlayStripLifecycle::Exiting && strip.exit_progress >= 1.0)
        });
        self.stable_finalized = self
            .strips
            .iter()
            .filter(|strip| {
                strip.block_variant == OverlayPresentationBlockVariant::Finalized
                    && strip.lifecycle != OverlayStripLifecycle::Exiting
            })
            .cloned()
            .collect();
        self.active_self = self
            .strips
            .iter()
            .find(|strip| strip.block_variant == OverlayPresentationBlockVariant::ActiveSelf)
            .cloned();
        self.exiting_finalized = self
            .strips
            .iter()
            .filter(|strip| strip.lifecycle == OverlayStripLifecycle::Exiting)
            .cloned()
            .collect();

        previous != self.strips
    }

    pub fn has_active_animation(&self) -> bool {
        self.strips.iter().any(OverlayStrip::is_animating)
    }

    fn rebuild_strips(&mut self) {
        self.strips = self
            .stable_finalized
            .iter()
            .cloned()
            .chain(self.active_self.iter().cloned())
            .chain(self.exiting_finalized.iter().cloned())
            .collect();
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

        let scene_changed = self
            .scene
            .apply_snapshot(&snapshot.blocks, snapshot.calibration.text_scale);
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

fn default_secondary_enabled() -> bool {
    true
}

fn select_stable_finalized_blocks(
    blocks: &[OverlayPresentationBlock],
) -> Vec<&OverlayPresentationBlock> {
    let finalized = blocks
        .iter()
        .filter(|block| block.block_variant == OverlayPresentationBlockVariant::Finalized)
        .collect::<Vec<_>>();
    let keep_start = finalized.len().saturating_sub(2);
    finalized.into_iter().skip(keep_start).collect()
}

fn build_live_strip(
    existing_by_id: &mut HashMap<String, OverlayStrip>,
    block: &OverlayPresentationBlock,
    order: usize,
    text_scale: f32,
) -> OverlayStrip {
    let height_px = measured_block_height_px(block, text_scale);
    let existing = existing_by_id.remove(&block.id);
    let is_new = existing.is_none();
    let mut strip = existing.unwrap_or_else(|| OverlayStrip::new(block, order, text_scale));
    let previous_order = strip.order;
    let previous_top_px = strip.current_top_px;
    let previous_height_px = strip.current_height_px;
    let was_entering =
        strip.lifecycle == OverlayStripLifecycle::Entering && strip.enter_progress < 1.0;

    strip.id = block.id.clone();
    strip.channel = block.channel.clone();
    strip.block_variant = block.block_variant;
    strip.primary_text = block.primary_text.clone();
    strip.secondary_text = block.secondary_text.clone();
    strip.secondary_enabled = block.secondary_enabled;
    strip.order = order;
    strip.previous_order = previous_order;
    strip.previous_top_px = previous_top_px;
    strip.current_top_px = previous_top_px;
    strip.previous_height_px = previous_height_px;
    strip.current_height_px = height_px;
    strip.exit_progress = 0.0;
    if is_new {
        strip.lifecycle = OverlayStripLifecycle::Entering;
        strip.enter_progress = 0.0;
        strip.previous_order = order;
        strip.previous_top_px = 0.0;
        strip.previous_height_px = height_px;
    } else if !was_entering {
        strip.lifecycle = OverlayStripLifecycle::Stable;
    }
    strip
}

fn assign_live_layout(strip: &mut OverlayStrip, order: usize, top_px: f32) {
    let top_changed = (strip.previous_top_px - top_px).abs() > f32::EPSILON;
    let height_changed = (strip.previous_height_px - strip.current_height_px).abs() > f32::EPSILON;

    strip.order = order;
    strip.current_top_px = top_px;
    if strip.lifecycle == OverlayStripLifecycle::Entering && strip.enter_progress == 0.0 {
        strip.previous_order = order;
        strip.previous_top_px = top_px;
        strip.previous_height_px = strip.current_height_px;
        strip.reflow_progress = 1.0;
        return;
    }

    if top_changed || height_changed || strip.previous_order != order {
        strip.reflow_progress = 0.0;
        return;
    }

    strip.previous_order = order;
    strip.previous_top_px = top_px;
    strip.previous_height_px = strip.current_height_px;
    strip.reflow_progress = 1.0;
}

fn build_exiting_strip(mut strip: OverlayStrip, order: usize, top_px: f32) -> OverlayStrip {
    if strip.lifecycle != OverlayStripLifecycle::Exiting {
        strip.lifecycle = OverlayStripLifecycle::Exiting;
        strip.exit_progress = 0.0;
    }
    strip.order = order;
    strip.previous_order = order;
    strip.current_top_px = top_px;
    strip.previous_height_px = strip.current_height_px;
    strip.reflow_progress = 1.0;
    strip
}

fn measured_block_height_px(block: &OverlayPresentationBlock, text_scale: f32) -> f32 {
    CaptionLayoutPolicy::default().measured_block_height_px(
        block.secondary_enabled,
        text_scale,
        1.0,
    )
}

#[cfg(test)]
mod tests {
    use super::{
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

    #[test]
    fn apply_snapshot_replaces_render_state() {
        let mut state = OverlayState::default();

        assert!(state.apply_snapshot(&OverlayPresentationSnapshot {
            revision: 1,
            calibration: OverlayPresentationCalibration::default(),
            blocks: vec![block("self:1", "self", "hello", "안녕", true)],
        }));

        assert_eq!(state.snapshot().revision, 1);
        assert_eq!(state.blocks().len(), 1);
        assert_eq!(state.blocks()[0].primary_text, "hello");
        assert_eq!(state.blocks()[0].secondary_text, "안녕");
        assert_eq!(
            state.scene().strips()[0].lifecycle,
            OverlayStripLifecycle::Entering
        );
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
