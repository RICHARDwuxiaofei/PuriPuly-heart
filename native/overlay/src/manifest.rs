use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::runtime::StartupError;

pub const EXPECTED_CONTRACT_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OverlayManifest {
    pub contract_version: u32,
    pub app_version: String,
    pub overlay_instance_id: String,
    pub bridge_url: String,
    pub session_token: String,
    pub parent_pid: u32,
    pub startup_deadline_ms: u32,
    pub log_dir: String,
    pub log_level: String,
    pub locale: String,
    pub diagnostics_enabled: bool,
}

pub fn load_manifest(path: impl AsRef<Path>) -> Result<OverlayManifest, StartupError> {
    let content = std::fs::read_to_string(path).map_err(|error| StartupError::Manifest(error.to_string()))?;
    serde_json::from_str(&content).map_err(|error| StartupError::Manifest(error.to_string()))
}

pub fn validate_manifest(manifest: &OverlayManifest) -> Result<(), StartupError> {
    if manifest.contract_version != EXPECTED_CONTRACT_VERSION {
        return Err(StartupError::ContractMismatch(format!(
            "expected contract_version={} but received {}",
            EXPECTED_CONTRACT_VERSION, manifest.contract_version
        )));
    }
    Ok(())
}
