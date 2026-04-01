use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum OverlayContentKind {
    Original,
    Translation,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct OverlayRowKey {
    channel: String,
    utterance_id: String,
    content_kind: OverlayContentKind,
}

impl OverlayRowKey {
    fn new(channel: &str, utterance_id: &str, content_kind: OverlayContentKind) -> Self {
        Self {
            channel: channel.to_string(),
            utterance_id: utterance_id.to_string(),
            content_kind,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RowEvent {
    pub event_id: String,
    pub seq: u64,
    pub utterance_id: String,
    pub channel: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub source_language: String,
    #[serde(default)]
    pub target_language: String,
    pub created_at: f64,
    #[serde(default = "default_true")]
    pub is_final: bool,
    #[serde(default)]
    pub speaker_label: Option<String>,
    #[serde(default)]
    pub peer_epoch: Option<i64>,
    #[serde(default)]
    pub applied_context_mode: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UtteranceClosedEvent {
    pub event_id: String,
    pub seq: u64,
    pub utterance_id: String,
    pub channel: String,
    pub created_at: f64,
    #[serde(default = "default_true")]
    pub is_final: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ShutdownEvent {
    #[serde(default)]
    pub event_id: String,
    #[serde(default)]
    pub seq: u64,
    #[serde(default)]
    pub utterance_id: Option<String>,
    #[serde(default)]
    pub channel: Option<String>,
    #[serde(default)]
    pub created_at: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SelfActiveUpdateEvent {
    pub event_id: String,
    pub seq: u64,
    #[serde(default)]
    pub utterance_id: Option<String>,
    #[serde(default)]
    pub channel: Option<String>,
    #[serde(default)]
    pub text: String,
    pub created_at: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SelfActiveClearEvent {
    pub event_id: String,
    pub seq: u64,
    #[serde(default)]
    pub utterance_id: Option<String>,
    #[serde(default)]
    pub channel: Option<String>,
    pub created_at: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OverlayCalibration {
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

impl Default for OverlayCalibration {
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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OverlayCalibrationUpdateEvent {
    pub event_id: String,
    pub seq: u64,
    pub created_at: f64,
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

impl OverlayCalibrationUpdateEvent {
    fn calibration(&self) -> OverlayCalibration {
        OverlayCalibration {
            anchor: self.anchor.clone(),
            offset_x: self.offset_x,
            offset_y: self.offset_y,
            distance: self.distance,
            text_scale: self.text_scale,
            background_alpha: self.background_alpha,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum Event {
    #[serde(rename = "self_transcript_final")]
    SelfTranscriptFinal(RowEvent),
    #[serde(rename = "peer_transcript_final")]
    PeerTranscriptFinal(RowEvent),
    #[serde(rename = "self_active_update")]
    SelfActiveUpdate(SelfActiveUpdateEvent),
    #[serde(rename = "self_active_clear")]
    SelfActiveClear(SelfActiveClearEvent),
    #[serde(rename = "translation_stream_update")]
    TranslationStreamUpdate(RowEvent),
    #[serde(rename = "translation_final")]
    TranslationFinal(RowEvent),
    #[serde(rename = "utterance_closed")]
    UtteranceClosed(UtteranceClosedEvent),
    #[serde(rename = "shutdown")]
    Shutdown(ShutdownEvent),
    #[serde(rename = "overlay_calibration_update")]
    OverlayCalibrationUpdate(OverlayCalibrationUpdateEvent),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct OverlayStateSnapshot {
    pub events: Vec<Event>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OverlayRow {
    pub event_id: String,
    pub seq: u64,
    pub utterance_id: String,
    pub channel: String,
    pub content_kind: OverlayContentKind,
    pub text: String,
    pub source_language: String,
    pub target_language: String,
    pub created_at: f64,
    pub is_final: bool,
    pub speaker_label: Option<String>,
    pub peer_epoch: Option<i64>,
    pub applied_context_mode: Option<String>,
    pub closed: bool,
}

impl OverlayRow {
    fn from_row_event(event: &RowEvent, content_kind: OverlayContentKind) -> Self {
        Self {
            event_id: event.event_id.clone(),
            seq: event.seq,
            utterance_id: event.utterance_id.clone(),
            channel: event.channel.clone(),
            content_kind,
            text: event.text.clone(),
            source_language: event.source_language.clone(),
            target_language: event.target_language.clone(),
            created_at: event.created_at,
            is_final: event.is_final,
            speaker_label: event.speaker_label.clone(),
            peer_epoch: event.peer_epoch,
            applied_context_mode: event.applied_context_mode.clone(),
            closed: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
struct OverlayActiveSelf {
    seq: u64,
    text: String,
    created_at: f64,
}

impl OverlayActiveSelf {
    fn from_event(event: &SelfActiveUpdateEvent) -> Self {
        Self {
            seq: event.seq,
            text: event.text.clone(),
            created_at: event.created_at,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct OverlayState {
    rows: BTreeMap<OverlayRowKey, OverlayRow>,
    calibration: OverlayCalibration,
    active_self: Option<OverlayActiveSelf>,
}

impl OverlayState {
    pub fn apply_snapshot(&mut self, snapshot: &OverlayStateSnapshot) -> bool {
        let mut next_state = OverlayState::default();
        for event in &snapshot.events {
            apply_event_to_state(&mut next_state, event.clone());
        }
        if self == &next_state {
            return false;
        }
        *self = next_state;
        true
    }

    pub fn apply(&mut self, event: Event) -> bool {
        apply_event_to_state(self, event)
    }

    pub fn rows_for(&self, channel: &str) -> Vec<&OverlayRow> {
        let mut rows: Vec<&OverlayRow> = self
            .rows
            .values()
            .filter(|row| row.channel == channel)
            .collect();
        rows.sort_by(|left, right| left.seq.cmp(&right.seq));
        rows
    }

    pub fn calibration(&self) -> &OverlayCalibration {
        &self.calibration
    }

    pub fn active_self_text(&self) -> Option<&str> {
        self.active_self.as_ref().map(|active| active.text.as_str())
    }

    pub fn active_self_seq(&self) -> Option<u64> {
        self.active_self.as_ref().map(|active| active.seq)
    }
}

fn default_true() -> bool {
    true
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

fn apply_event_to_state(state: &mut OverlayState, event: Event) -> bool {
    match event {
        Event::SelfTranscriptFinal(row_event) | Event::PeerTranscriptFinal(row_event) => {
            upsert_row(
                &mut state.rows,
                OverlayRow::from_row_event(&row_event, OverlayContentKind::Original),
            )
        }
        Event::SelfActiveUpdate(event) => upsert_active_self(&mut state.active_self, event),
        Event::SelfActiveClear(_) => clear_active_self(&mut state.active_self),
        Event::TranslationStreamUpdate(row_event) | Event::TranslationFinal(row_event) => {
            upsert_row(
                &mut state.rows,
                OverlayRow::from_row_event(&row_event, OverlayContentKind::Translation),
            )
        }
        Event::UtteranceClosed(event) => close_row(&mut state.rows, event),
        Event::Shutdown(_) => false,
        Event::OverlayCalibrationUpdate(event) => {
            let next_calibration = event.calibration();
            if state.calibration == next_calibration {
                return false;
            }
            state.calibration = next_calibration;
            true
        }
    }
}

fn upsert_row(rows: &mut BTreeMap<OverlayRowKey, OverlayRow>, row: OverlayRow) -> bool {
    let key = OverlayRowKey::new(&row.channel, &row.utterance_id, row.content_kind.clone());
    match rows.get(&key) {
        Some(existing) if existing == &row => false,
        _ => {
            rows.insert(key, row);
            true
        }
    }
}

fn close_row(rows: &mut BTreeMap<OverlayRowKey, OverlayRow>, event: UtteranceClosedEvent) -> bool {
    let keys = [
        OverlayRowKey::new(
            &event.channel,
            &event.utterance_id,
            OverlayContentKind::Original,
        ),
        OverlayRowKey::new(
            &event.channel,
            &event.utterance_id,
            OverlayContentKind::Translation,
        ),
    ];

    let mut changed = false;
    for key in keys {
        let Some(row) = rows.get_mut(&key) else {
            continue;
        };
        let was_closed = row.closed;
        let prior_final = row.is_final;
        row.closed = true;
        row.is_final = event.is_final;
        changed |= !was_closed || prior_final != event.is_final;
    }

    changed
}

fn upsert_active_self(
    active_self: &mut Option<OverlayActiveSelf>,
    event: SelfActiveUpdateEvent,
) -> bool {
    let next_active = OverlayActiveSelf::from_event(&event);
    match active_self {
        Some(existing) if existing == &next_active => false,
        _ => {
            *active_self = Some(next_active);
            true
        }
    }
}

fn clear_active_self(active_self: &mut Option<OverlayActiveSelf>) -> bool {
    active_self.take().is_some()
}

#[cfg(test)]
mod tests {
    use super::{
        Event, OverlayState, SelfActiveClearEvent, SelfActiveUpdateEvent,
    };

    #[test]
    fn self_active_update_and_clear_change_overlay_state() {
        let mut state = OverlayState::default();

        assert!(state.apply(Event::SelfActiveUpdate(SelfActiveUpdateEvent {
            event_id: "evt-active-1".to_string(),
            seq: 1,
            utterance_id: None,
            channel: Some("self".to_string()),
            text: "speaking now".to_string(),
            created_at: 1.0,
        })));
        assert_eq!(state.active_self_text(), Some("speaking now"));

        assert!(state.apply(Event::SelfActiveClear(SelfActiveClearEvent {
            event_id: "evt-active-2".to_string(),
            seq: 2,
            utterance_id: None,
            channel: Some("self".to_string()),
            created_at: 2.0,
        })));
        assert_eq!(state.active_self_text(), None);
    }
}
