use serde::{Deserialize, Serialize};
use std::net::Ipv4Addr;
use std::path::Path;

pub const CONTRACT_VERSION: u32 = 2;

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum WorkerMode {
    Discovery,
    Persistent,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct LaunchManifest {
    pub contract_version: u32,
    pub session_id: String,
    pub auth_token: String,
    pub connect_host: String,
    pub connect_port: u16,
    pub heartbeat_interval_ms: u64,
    pub mode: WorkerMode,
}

impl LaunchManifest {
    pub fn load(path: &Path) -> Result<Self, String> {
        let bytes = std::fs::read(path).map_err(|_| "manifest_read_failed".to_string())?;
        let manifest: Self =
            serde_json::from_slice(&bytes).map_err(|_| "manifest_invalid".to_string())?;
        manifest.validate()?;
        Ok(manifest)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.contract_version != CONTRACT_VERSION {
            return Err("contract_mismatch".to_string());
        }
        if self.session_id.len() < 16 || self.session_id.len() > 128 {
            return Err("session_id_invalid".to_string());
        }
        if self.auth_token.len() < 32 || self.auth_token.len() > 256 {
            return Err("auth_token_invalid".to_string());
        }
        if self.connect_host.parse::<Ipv4Addr>() != Ok(Ipv4Addr::LOCALHOST) {
            return Err("connect_host_not_loopback".to_string());
        }
        if self.connect_port == 0 {
            return Err("connect_port_invalid".to_string());
        }
        if !(100..=60_000).contains(&self.heartbeat_interval_ms) {
            return Err("heartbeat_interval_invalid".to_string());
        }
        Ok(())
    }
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Discover {
        contract_version: u32,
        session_id: String,
        request_id: String,
    },
    Activate {
        contract_version: u32,
        session_id: String,
        request_id: String,
        model_path: String,
        device_id: String,
    },
    Transcribe {
        contract_version: u32,
        session_id: String,
        request_id: String,
        channel: String,
        audio_path: String,
        language_hint: Option<String>,
    },
    Cancel {
        contract_version: u32,
        session_id: String,
        request_id: String,
        target_request_id: String,
    },
    Shutdown {
        contract_version: u32,
        session_id: String,
        request_id: String,
    },
}

impl Request {
    pub fn validate_session(&self, expected_session_id: &str) -> Result<(), String> {
        let (contract_version, session_id, request_id) = match self {
            Self::Discover {
                contract_version,
                session_id,
                request_id,
            }
            | Self::Activate {
                contract_version,
                session_id,
                request_id,
                ..
            }
            | Self::Transcribe {
                contract_version,
                session_id,
                request_id,
                ..
            }
            | Self::Cancel {
                contract_version,
                session_id,
                request_id,
                ..
            }
            | Self::Shutdown {
                contract_version,
                session_id,
                request_id,
            } => (contract_version, session_id, request_id),
        };
        if *contract_version != CONTRACT_VERSION {
            return Err("contract_mismatch".to_string());
        }
        if session_id != expected_session_id {
            return Err("session_mismatch".to_string());
        }
        if request_id.is_empty() || request_id.len() > 128 {
            return Err("request_id_invalid".to_string());
        }
        Ok(())
    }

    pub fn request_id(&self) -> &str {
        match self {
            Self::Discover { request_id, .. }
            | Self::Activate { request_id, .. }
            | Self::Transcribe { request_id, .. }
            | Self::Cancel { request_id, .. }
            | Self::Shutdown { request_id, .. } => request_id,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{LaunchManifest, Request, WorkerMode, CONTRACT_VERSION};

    #[test]
    fn manifest_accepts_only_ipv4_loopback_and_bounded_auth_fields() {
        let valid = LaunchManifest {
            contract_version: CONTRACT_VERSION,
            session_id: "session-123456789".to_string(),
            auth_token: "a".repeat(64),
            connect_host: "127.0.0.1".to_string(),
            connect_port: 12345,
            heartbeat_interval_ms: 500,
            mode: WorkerMode::Persistent,
        };
        assert_eq!(valid.validate(), Ok(()));

        let mut invalid = valid.clone();
        invalid.connect_host = "0.0.0.0".to_string();
        assert_eq!(
            invalid.validate(),
            Err("connect_host_not_loopback".to_string())
        );

        invalid = valid;
        invalid.auth_token = "short".to_string();
        assert_eq!(invalid.validate(), Err("auth_token_invalid".to_string()));
    }

    #[test]
    fn request_rejects_contract_and_session_mismatch() {
        let request = Request::Discover {
            contract_version: CONTRACT_VERSION,
            session_id: "expected-session".to_string(),
            request_id: "discover-1".to_string(),
        };
        assert_eq!(request.validate_session("expected-session"), Ok(()));
        assert_eq!(
            request.validate_session("different-session"),
            Err("session_mismatch".to_string())
        );
    }

    #[test]
    fn transcribe_request_preserves_optional_language_hint() {
        let request: Request = serde_json::from_value(serde_json::json!({
            "type": "transcribe",
            "contract_version": CONTRACT_VERSION,
            "session_id": "expected-session",
            "request_id": "transcribe-1",
            "channel": "peer",
            "audio_path": "sample.wav",
            "language_hint": "ja"
        }))
        .expect("transcribe request");

        match request {
            Request::Transcribe { language_hint, .. } => {
                assert_eq!(language_hint.as_deref(), Some("ja"));
            }
            _ => panic!("expected transcribe request"),
        }
    }
}
