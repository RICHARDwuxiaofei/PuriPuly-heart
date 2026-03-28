use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct OverlayRowKey {
    channel: String,
    utterance_id: String,
}

impl OverlayRowKey {
    fn new(channel: &str, utterance_id: &str) -> Self {
        Self {
            channel: channel.to_string(),
            utterance_id: utterance_id.to_string(),
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
#[serde(tag = "type")]
pub enum Event {
    #[serde(rename = "self_transcript_final")]
    SelfTranscriptFinal(RowEvent),
    #[serde(rename = "peer_transcript_final")]
    PeerTranscriptFinal(RowEvent),
    #[serde(rename = "translation_stream_update")]
    TranslationStreamUpdate(RowEvent),
    #[serde(rename = "translation_final")]
    TranslationFinal(RowEvent),
    #[serde(rename = "utterance_closed")]
    UtteranceClosed(UtteranceClosedEvent),
    #[serde(rename = "shutdown")]
    Shutdown(ShutdownEvent),
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

impl From<&RowEvent> for OverlayRow {
    fn from(event: &RowEvent) -> Self {
        Self {
            event_id: event.event_id.clone(),
            seq: event.seq,
            utterance_id: event.utterance_id.clone(),
            channel: event.channel.clone(),
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

#[derive(Debug, Clone, PartialEq, Default)]
pub struct OverlayState {
    rows: BTreeMap<OverlayRowKey, OverlayRow>,
}

impl OverlayState {
    pub fn apply_snapshot(&mut self, snapshot: &OverlayStateSnapshot) -> bool {
        let mut changed = false;
        for event in &snapshot.events {
            changed |= self.apply(event.clone());
        }
        changed
    }

    pub fn apply(&mut self, event: Event) -> bool {
        match event {
            Event::SelfTranscriptFinal(row_event)
            | Event::PeerTranscriptFinal(row_event)
            | Event::TranslationStreamUpdate(row_event)
            | Event::TranslationFinal(row_event) => self.upsert_row(OverlayRow::from(&row_event)),
            Event::UtteranceClosed(event) => self.close_row(event),
            Event::Shutdown(_) => false,
        }
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

    fn upsert_row(&mut self, row: OverlayRow) -> bool {
        let key = OverlayRowKey::new(&row.channel, &row.utterance_id);
        match self.rows.get(&key) {
            Some(existing) if existing == &row => false,
            _ => {
                self.rows.insert(key, row);
                true
            }
        }
    }

    fn close_row(&mut self, event: UtteranceClosedEvent) -> bool {
        let key = OverlayRowKey::new(&event.channel, &event.utterance_id);
        let Some(row) = self.rows.get_mut(&key) else {
            return false;
        };

        let was_closed = row.closed;
        let prior_final = row.is_final;
        row.closed = true;
        row.is_final = event.is_final;
        !was_closed || prior_final != event.is_final
    }
}

fn default_true() -> bool {
    true
}
