use std::collections::VecDeque;

use serde::Serialize;

const MAX_PRESENTATION_DIAGNOSTIC_RECORDS: usize = 128;
const MAX_PENDING_PRESENTATION_DIAGNOSTIC_RECORDS: usize = 8;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresentationStage {
    LogicalRevisionAccepted,
    RenderReturned,
    ReadinessObserved,
    SubmissionAttempted,
    SubmissionReturned,
    VisibilityObserved,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresentationOutcome {
    Accepted,
    Success,
    Failure,
    LegacyNotObserved,
    Attempted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresentationStrategy {
    LegacyDirectTextureSubmit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PresentationBackend {
    D3d11Hardware,
    D3d11Warp,
    Test,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AdapterIdentity {
    NotObservedStageOne,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PhysicalHmdVisibility {
    NotObservable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PresentationDiagnosticRecord {
    pub schema_version: u8,
    pub sequence: u64,
    pub logical_revision: u64,
    pub render_generation: Option<u64>,
    pub submission_attempt: Option<u64>,
    pub stage: PresentationStage,
    pub outcome: PresentationOutcome,
    pub strategy: PresentationStrategy,
    pub backend: PresentationBackend,
    pub adapter_identity: AdapterIdentity,
    pub desired_visible: Option<bool>,
    pub observed_runtime_visible: Option<bool>,
    pub physical_hmd_visibility: PhysicalHmdVisibility,
    pub dropped_unacknowledged_records: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PresentationCorrelation {
    pub logical_revision: u64,
    pub render_generation: u64,
    pub submission_attempt: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingPresentationDiagnostics {
    pub records: Vec<String>,
    pub through_sequence: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PresentationDiagnostics {
    records: VecDeque<PresentationDiagnosticRecord>,
    acknowledged_through_sequence: u64,
    next_sequence: u64,
    next_logical_revision: u64,
    next_render_generation: u64,
    next_submission_attempt: u64,
    active_logical_revision: u64,
    stopped: bool,
    dropped_unacknowledged_records: u64,
}

impl PresentationDiagnostics {
    pub fn new() -> Self {
        Self {
            records: VecDeque::with_capacity(MAX_PRESENTATION_DIAGNOSTIC_RECORDS),
            acknowledged_through_sequence: 0,
            next_sequence: 1,
            next_logical_revision: 1,
            next_render_generation: 1,
            next_submission_attempt: 1,
            active_logical_revision: 0,
            stopped: false,
            dropped_unacknowledged_records: 0,
        }
    }

    pub fn accept_logical_revision(&mut self, backend: PresentationBackend) {
        if self.stopped {
            return;
        }
        self.active_logical_revision = self.next_logical_revision;
        self.next_logical_revision = self.next_logical_revision.saturating_add(1);
        self.push(
            PresentationStage::LogicalRevisionAccepted,
            PresentationOutcome::Accepted,
            backend,
            None,
            None,
            None,
            None,
        );
    }

    pub fn begin_presentation(&mut self) -> Option<PresentationCorrelation> {
        if self.stopped {
            return None;
        }
        let correlation = PresentationCorrelation {
            logical_revision: self.active_logical_revision,
            render_generation: self.next_render_generation,
            submission_attempt: self.next_submission_attempt,
        };
        self.next_render_generation = self.next_render_generation.saturating_add(1);
        self.next_submission_attempt = self.next_submission_attempt.saturating_add(1);
        Some(correlation)
    }

    pub fn record_render_return(
        &mut self,
        correlation: PresentationCorrelation,
        backend: PresentationBackend,
        succeeded: bool,
    ) {
        self.push_for_correlation(
            correlation,
            PresentationStage::RenderReturned,
            if succeeded {
                PresentationOutcome::Success
            } else {
                PresentationOutcome::Failure
            },
            backend,
            None,
            None,
        );
    }

    pub fn record_legacy_readiness(
        &mut self,
        correlation: PresentationCorrelation,
        backend: PresentationBackend,
    ) {
        self.push_for_correlation(
            correlation,
            PresentationStage::ReadinessObserved,
            PresentationOutcome::LegacyNotObserved,
            backend,
            None,
            None,
        );
    }

    pub fn record_submission_attempt(
        &mut self,
        correlation: PresentationCorrelation,
        backend: PresentationBackend,
    ) {
        self.push_for_correlation(
            correlation,
            PresentationStage::SubmissionAttempted,
            PresentationOutcome::Attempted,
            backend,
            None,
            None,
        );
    }

    pub fn record_submission_return(
        &mut self,
        correlation: PresentationCorrelation,
        backend: PresentationBackend,
        succeeded: bool,
    ) {
        self.push_for_correlation(
            correlation,
            PresentationStage::SubmissionReturned,
            if succeeded {
                PresentationOutcome::Success
            } else {
                PresentationOutcome::Failure
            },
            backend,
            None,
            None,
        );
    }

    pub fn record_visibility(
        &mut self,
        correlation: PresentationCorrelation,
        backend: PresentationBackend,
        desired_visible: bool,
        observed_runtime_visible: bool,
        succeeded: bool,
    ) {
        self.push_for_correlation(
            correlation,
            PresentationStage::VisibilityObserved,
            if succeeded {
                PresentationOutcome::Success
            } else {
                PresentationOutcome::Failure
            },
            backend,
            Some(desired_visible),
            Some(observed_runtime_visible),
        );
    }

    pub fn pending_json(&self) -> Vec<String> {
        self.pending_batch().records
    }

    pub fn pending_batch(&self) -> PendingPresentationDiagnostics {
        if self.stopped {
            return PendingPresentationDiagnostics {
                records: Vec::new(),
                through_sequence: None,
            };
        }
        let pending_records = self
            .records
            .iter()
            .filter(|record| record.sequence > self.acknowledged_through_sequence)
            .take(MAX_PENDING_PRESENTATION_DIAGNOSTIC_RECORDS)
            .collect::<Vec<_>>();
        let through_sequence = pending_records.last().map(|record| record.sequence);
        let records = pending_records
            .into_iter()
            .filter_map(|record| serde_json::to_string(record).ok())
            .collect();
        PendingPresentationDiagnostics {
            records,
            through_sequence,
        }
    }

    pub fn acknowledge_through(&mut self, sequence: u64) {
        self.acknowledged_through_sequence = self
            .acknowledged_through_sequence
            .max(sequence.min(self.next_sequence.saturating_sub(1)));
    }

    pub fn records(&self) -> &VecDeque<PresentationDiagnosticRecord> {
        &self.records
    }

    pub fn shutdown(&mut self) {
        self.records.clear();
        self.stopped = true;
    }

    fn push_for_correlation(
        &mut self,
        correlation: PresentationCorrelation,
        stage: PresentationStage,
        outcome: PresentationOutcome,
        backend: PresentationBackend,
        desired_visible: Option<bool>,
        observed_runtime_visible: Option<bool>,
    ) {
        self.push(
            stage,
            outcome,
            backend,
            Some(correlation.render_generation),
            Some(correlation.submission_attempt),
            desired_visible,
            observed_runtime_visible,
        );
    }

    fn push(
        &mut self,
        stage: PresentationStage,
        outcome: PresentationOutcome,
        backend: PresentationBackend,
        render_generation: Option<u64>,
        submission_attempt: Option<u64>,
        desired_visible: Option<bool>,
        observed_runtime_visible: Option<bool>,
    ) {
        if self.stopped {
            return;
        }
        if self.records.len() == MAX_PRESENTATION_DIAGNOSTIC_RECORDS {
            if self
                .records
                .front()
                .is_some_and(|record| record.sequence > self.acknowledged_through_sequence)
            {
                self.dropped_unacknowledged_records =
                    self.dropped_unacknowledged_records.saturating_add(1);
            }
            self.records.pop_front();
        }
        self.records.push_back(PresentationDiagnosticRecord {
            schema_version: 1,
            sequence: self.next_sequence,
            logical_revision: self.active_logical_revision,
            render_generation,
            submission_attempt,
            stage,
            outcome,
            strategy: PresentationStrategy::LegacyDirectTextureSubmit,
            backend,
            adapter_identity: AdapterIdentity::NotObservedStageOne,
            desired_visible,
            observed_runtime_visible,
            physical_hmd_visibility: PhysicalHmdVisibility::NotObservable,
            dropped_unacknowledged_records: self.dropped_unacknowledged_records,
        });
        self.next_sequence = self.next_sequence.saturating_add(1);
    }
}

impl Default for PresentationDiagnostics {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_are_bounded_and_shutdown_removes_owned_work() {
        let mut diagnostics = PresentationDiagnostics::new();
        for _ in 0..200 {
            diagnostics.accept_logical_revision(PresentationBackend::Test);
        }

        assert_eq!(
            diagnostics.records().len(),
            MAX_PRESENTATION_DIAGNOSTIC_RECORDS
        );
        assert_eq!(
            diagnostics.pending_json().len(),
            MAX_PENDING_PRESENTATION_DIAGNOSTIC_RECORDS
        );

        diagnostics.shutdown();
        diagnostics.accept_logical_revision(PresentationBackend::Test);

        assert!(diagnostics.records().is_empty());
        assert!(diagnostics.pending_json().is_empty());
    }

    #[test]
    fn serialized_records_are_structurally_allowlisted() {
        let mut diagnostics = PresentationDiagnostics::new();
        diagnostics.accept_logical_revision(PresentationBackend::Test);
        let correlation = diagnostics.begin_presentation().unwrap();
        diagnostics.record_render_return(correlation, PresentationBackend::Test, true);
        diagnostics.record_legacy_readiness(correlation, PresentationBackend::Test);
        diagnostics.record_submission_attempt(correlation, PresentationBackend::Test);
        diagnostics.record_submission_return(correlation, PresentationBackend::Test, true);
        diagnostics.record_visibility(correlation, PresentationBackend::Test, true, true, true);

        let allowed = [
            "schema_version",
            "sequence",
            "logical_revision",
            "render_generation",
            "submission_attempt",
            "stage",
            "outcome",
            "strategy",
            "backend",
            "adapter_identity",
            "desired_visible",
            "observed_runtime_visible",
            "physical_hmd_visibility",
            "dropped_unacknowledged_records",
        ];
        for line in diagnostics.pending_json() {
            let value: serde_json::Value = serde_json::from_str(&line).unwrap();
            let object = value.as_object().unwrap();
            assert!(object.keys().all(|key| allowed.contains(&key.as_str())));
            assert_eq!(object["physical_hmd_visibility"], "not_observable");
            assert!(!line.contains("caption"));
            assert!(!line.contains("exception"));
            assert!(!line.contains("stack"));
        }
    }

    #[test]
    fn pending_records_require_explicit_acknowledgement() {
        let mut diagnostics = PresentationDiagnostics::new();
        diagnostics.accept_logical_revision(PresentationBackend::Test);

        let first = diagnostics.pending_json();
        let repeated = diagnostics.pending_json();
        assert_eq!(first, repeated);

        diagnostics.acknowledge_through(1);

        assert!(diagnostics.pending_json().is_empty());
    }

    #[test]
    fn bounded_overflow_accounts_for_unacknowledged_records() {
        let mut diagnostics = PresentationDiagnostics::new();
        for _ in 0..=MAX_PRESENTATION_DIAGNOSTIC_RECORDS {
            diagnostics.accept_logical_revision(PresentationBackend::Test);
        }

        assert_eq!(
            diagnostics
                .records()
                .back()
                .unwrap()
                .dropped_unacknowledged_records,
            1
        );
    }
}
