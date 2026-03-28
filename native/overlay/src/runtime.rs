use std::path::Path;

use serde_json::json;
use thiserror::Error;
use tokio::io::{self, AsyncWriteExt};

use crate::bridge::{BridgeClient, BridgeError, BridgeIncoming, OverlayBridgeEvent};
use crate::logging::OverlayLogger;
use crate::manifest::{load_manifest, validate_manifest, OverlayManifest};
use crate::state::{OverlayState, OverlayStateSnapshot};

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum StartupError {
    #[error("manifest invalid: {0}")]
    Manifest(String),
    #[error("contract mismatch: {0}")]
    ContractMismatch(String),
    #[error("bridge auth failed: {0}")]
    BridgeAuth(String),
    #[error("openvr init failed: {0}")]
    OpenVrInit(String),
    #[error("renderer init failed: {0}")]
    RendererInit(String),
    #[error("startup failed: {0}")]
    Other(String),
}

impl StartupError {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::ContractMismatch(_) => 10,
            Self::BridgeAuth(_) => 12,
            Self::OpenVrInit(_) => 20,
            Self::RendererInit(_) => 21,
            Self::Manifest(_) | Self::Other(_) => 1,
        }
    }

    pub fn failure_reason(&self) -> &'static str {
        match self {
            Self::Manifest(_) => "manifest_invalid",
            Self::ContractMismatch(_) => "contract_mismatch",
            Self::BridgeAuth(_) => "bridge_auth_failed",
            Self::OpenVrInit(_) => "openvr_init_failed",
            Self::RendererInit(_) => "renderer_init_failed",
            Self::Other(_) => "unknown",
        }
    }
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum RuntimeFailure {
    #[error("runtime disconnected")]
    RuntimeDisconnected,
    #[error("runtime stopped")]
    Stopped,
    #[error("runtime bridge error: {0}")]
    Bridge(String),
}

impl RuntimeFailure {
    pub fn failure_reason(&self) -> &'static str {
        match self {
            Self::RuntimeDisconnected => "runtime_disconnected",
            Self::Stopped => "stopped",
            Self::Bridge(_) => "unknown",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct OverlayRuntime {
    ready: bool,
    stopped: bool,
    state: OverlayState,
    redraw_requested: bool,
}

impl OverlayRuntime {
    pub fn new(snapshot: OverlayStateSnapshot) -> Self {
        let mut runtime = Self {
            ready: false,
            stopped: false,
            state: OverlayState::default(),
            redraw_requested: false,
        };
        runtime.apply_snapshot(snapshot);
        runtime
    }

    pub fn state(&self) -> &OverlayState {
        &self.state
    }

    pub fn is_stopped(&self) -> bool {
        self.stopped
    }

    pub fn mark_ready_for_test(&mut self) {
        self.ready = true;
    }

    pub fn apply_snapshot(&mut self, snapshot: OverlayStateSnapshot) {
        if self.state.apply_snapshot(&snapshot) {
            self.redraw_requested = true;
        }
    }

    pub fn redraw_requested(&self) -> bool {
        self.redraw_requested
    }

    pub fn clear_redraw_flag(&mut self) {
        self.redraw_requested = false;
    }

    pub async fn handle_event(&mut self, event: OverlayBridgeEvent) -> Result<(), RuntimeFailure> {
        match event {
            OverlayBridgeEvent::Shutdown => {
                self.stopped = true;
                Ok(())
            }
            OverlayBridgeEvent::Live(event) => {
                if self.state.apply(event) {
                    self.redraw_requested = true;
                }
                Ok(())
            }
        }
    }

    pub async fn handle_bridge_loss_for_test(&mut self) -> Result<(), RuntimeFailure> {
        self.stopped = true;
        if self.ready {
            Err(RuntimeFailure::RuntimeDisconnected)
        } else {
            Ok(())
        }
    }

    pub async fn emit_ready(
        &mut self,
        bridge: &mut BridgeClient,
        logger: &OverlayLogger,
    ) -> Result<(), RuntimeFailure> {
        bridge
            .send_json(json!({"type": "overlay_ready"}))
            .await
            .map_err(|error| RuntimeFailure::Bridge(error.to_string()))?;
        logger
            .emit_stdout_event(&json!({"type": "overlay_ready"}))
            .await
            .map_err(|error| RuntimeFailure::Bridge(error.to_string()))?;
        logger
            .info("overlay_ready_sent")
            .await
            .map_err(|error| RuntimeFailure::Bridge(error.to_string()))?;
        self.ready = true;
        Ok(())
    }

    pub async fn run_event_loop(
        &mut self,
        bridge: &mut BridgeClient,
        logger: &OverlayLogger,
    ) -> Result<(), RuntimeFailure> {
        loop {
            match bridge.next_message().await {
                Ok(BridgeIncoming::Heartbeat) => continue,
                Ok(BridgeIncoming::Snapshot(snapshot)) => {
                    self.apply_snapshot(snapshot);
                }
                Ok(BridgeIncoming::Event(event)) => {
                    self.handle_event(event).await?;
                    if self.stopped {
                        return Ok(());
                    }
                }
                Err(BridgeError::Disconnected) => {
                    logger
                        .error("runtime_disconnected")
                        .await
                        .map_err(|error| RuntimeFailure::Bridge(error.to_string()))?;
                    self.handle_bridge_loss_for_test().await?;
                    logger
                        .emit_stdout_event(&json!({
                            "type": "runtime_error",
                            "failure_reason": "runtime_disconnected"
                        }))
                        .await
                        .map_err(|error| RuntimeFailure::Bridge(error.to_string()))?;
                    return Err(RuntimeFailure::RuntimeDisconnected);
                }
                Err(error) => return Err(RuntimeFailure::Bridge(error.to_string())),
            }
        }
    }
}

pub fn startup_error_from_bridge_error(error: BridgeError) -> StartupError {
    match error {
        BridgeError::Auth(message) => StartupError::BridgeAuth(message),
        BridgeError::Connect(message) | BridgeError::Protocol(message) => {
            StartupError::Other(format!("bridge startup failed: {message}"))
        }
        BridgeError::Disconnected => {
            StartupError::Other("bridge disconnected during startup".into())
        }
    }
}

pub async fn run_with_manifest(manifest: OverlayManifest) -> i32 {
    let logger = match OverlayLogger::open(&manifest.log_dir).await {
        Ok(logger) => logger,
        Err(error) => {
            eprintln!("[overlay][ERROR] failed to initialize logging: {error}");
            return 1;
        }
    };

    let _ = logger.info("manifest_loaded").await;
    if let Err(error) = validate_manifest(&manifest) {
        emit_startup_failure(&logger, &error).await;
        return error.exit_code();
    }

    if manifest.app_version != env!("CARGO_PKG_VERSION") {
        let _ = logger.warn(&format!(
            "app_version mismatch accepted: manifest={} runtime={}",
            manifest.app_version,
            env!("CARGO_PKG_VERSION")
        )).await;
    }

    let (mut bridge, snapshot) = match BridgeClient::connect(&manifest).await {
        Ok(result) => result,
        Err(error) => {
            let startup_error = startup_error_from_bridge_error(error);
            emit_startup_failure(&logger, &startup_error).await;
            return startup_error.exit_code();
        }
    };
    let _ = logger.info("bridge_connected").await;
    let _ = logger.info("bridge_authenticated").await;

    if let Err(error) = initialize_runtime_stubs(&logger).await {
        emit_startup_failure(&logger, &error).await;
        return error.exit_code();
    }

    let mut runtime = OverlayRuntime::new(snapshot);
    if let Err(error) = runtime.emit_ready(&mut bridge, &logger).await {
        let _ = logger.error(&error.to_string()).await;
        return 1;
    }

    match runtime.run_event_loop(&mut bridge, &logger).await {
        Ok(()) => 0,
        Err(RuntimeFailure::RuntimeDisconnected) => 1,
        Err(error) => {
            let _ = logger.error(&error.to_string()).await;
            1
        }
    }
}

pub async fn run_cli(args: &[String]) -> i32 {
    if args.len() == 2 && args[1] == "--version" {
        println!("{}", env!("CARGO_PKG_VERSION"));
        return 0;
    }

    if args.len() != 3 || args[1] != "--config" {
        eprintln!("usage: PuriPulyHeartOverlay --config <manifest.json>");
        return 2;
    }

    let manifest = match load_manifest(Path::new(&args[2])) {
        Ok(manifest) => manifest,
        Err(error) => {
            eprintln!("[overlay][ERROR] {error}");
            emit_startup_failure_to_stderr(&error).await;
            return error.exit_code();
        }
    };

    run_with_manifest(manifest).await
}

async fn initialize_runtime_stubs(logger: &OverlayLogger) -> Result<(), StartupError> {
    logger
        .info("openvr_ready")
        .await
        .map_err(|error| StartupError::Other(error.to_string()))?;
    logger
        .info("renderer_resources_ready")
        .await
        .map_err(|error| StartupError::Other(error.to_string()))?;
    Ok(())
}

async fn emit_startup_failure(logger: &OverlayLogger, error: &StartupError) {
    let _ = logger.error(&error.to_string()).await;
    let _ = logger
        .emit_stderr_event(&json!({
            "type": "startup_error",
            "failure_reason": error.failure_reason(),
        }))
        .await;
}

async fn emit_startup_failure_to_stderr(error: &StartupError) {
    let mut stderr = io::stderr();
    let line = format!(
        "EVENT {}\n",
        json!({
            "type": "startup_error",
            "failure_reason": error.failure_reason(),
        })
    );
    let _ = stderr.write_all(line.as_bytes()).await;
    let _ = stderr.flush().await;
}
