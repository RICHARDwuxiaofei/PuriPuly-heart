use std::path::Path;

use serde_json::json;
use thiserror::Error;
use tokio::io::{self, AsyncWriteExt};

use crate::bridge::{BridgeClient, BridgeError, BridgeIncoming, OverlayBridgeEvent};
use crate::logging::OverlayLogger;
use crate::manifest::{load_manifest, validate_manifest, OverlayManifest, EXPECTED_CONTRACT_VERSION};
use crate::openvr::{
    perform_startup_preflight, OpenVrOverlay, OpenVrStartupPreflightError, OverlayFrameSubmitter,
};
#[cfg(test)]
use crate::openvr::OpenVrError;
use crate::renderer::{
    CaptionBlock, CaptionChannel, CaptionPresentation, CaptionRenderer,
};
use crate::state::{OverlayPresentationSnapshot, OverlayState};

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum StartupError {
    #[error("manifest invalid: {0}")]
    Manifest(String),
    #[error("contract mismatch: {0}")]
    ContractMismatch(String),
    #[error("bridge auth failed: {0}")]
    BridgeAuth(String),
    #[error("SteamVR/OpenVR runtime is not installed")]
    SteamVrNotInstalled,
    #[error("SteamVR is not running")]
    SteamVrNotRunning,
    #[error("VR headset not found")]
    HmdNotFound,
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
            Self::SteamVrNotInstalled | Self::SteamVrNotRunning | Self::HmdNotFound => 20,
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
            Self::SteamVrNotInstalled => "steamvr_not_installed",
            Self::SteamVrNotRunning => "steamvr_not_running",
            Self::HmdNotFound => "hmd_not_found",
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
    #[error("renderer draw failed: {0}")]
    Render(String),
    #[error("openvr submit failed: {0}")]
    OpenVr(String),
}

impl RuntimeFailure {
    pub fn failure_reason(&self) -> &'static str {
        match self {
            Self::RuntimeDisconnected => "runtime_disconnected",
            Self::Stopped => "stopped",
            Self::Bridge(_) | Self::Render(_) | Self::OpenVr(_) => "unknown",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct OverlayRuntime {
    ready: bool,
    first_texture_submitted: bool,
    stopped: bool,
    state: OverlayState,
    redraw_requested: bool,
}

impl OverlayRuntime {
    pub fn new(snapshot: OverlayPresentationSnapshot) -> Self {
        let mut runtime = Self {
            ready: false,
            first_texture_submitted: false,
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

    pub fn ready_sent(&self) -> bool {
        self.ready
    }

    pub async fn submit_first_texture_for_test(&mut self) -> Result<(), RuntimeFailure> {
        self.first_texture_submitted = true;
        self.ready = true;
        Ok(())
    }

    pub fn apply_snapshot(&mut self, snapshot: OverlayPresentationSnapshot) {
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

    pub async fn submit_frame_if_needed<S: OverlayFrameSubmitter>(
        &mut self,
        renderer: &CaptionRenderer,
        openvr: &mut S,
        bridge: &mut BridgeClient,
        logger: &OverlayLogger,
    ) -> Result<(), RuntimeFailure> {
        if self.first_texture_submitted && !self.redraw_requested {
            return Ok(());
        }

        renderer.set_presentation(CaptionPresentation {
            background_alpha: self.state.calibration().background_alpha,
        });
        openvr
            .apply_calibration(self.state.calibration())
            .map_err(|error| RuntimeFailure::OpenVr(error.to_string()))?;
        let blocks = self.caption_blocks();
        let frame = if blocks.is_empty() {
            renderer
                .render_empty_frame()
                .map_err(|error| RuntimeFailure::Render(error.to_string()))?
        } else {
            renderer
                .render_blocks(blocks)
                .map_err(|error| RuntimeFailure::Render(error.to_string()))?
        };
        openvr
            .submit_frame(&frame)
            .map_err(|error| RuntimeFailure::OpenVr(error.to_string()))?;
        self.redraw_requested = false;

        if !self.first_texture_submitted {
            logger
                .info("first_texture_submitted")
                .await
                .map_err(|error| RuntimeFailure::Bridge(error.to_string()))?;
            self.first_texture_submitted = true;
            self.emit_ready(bridge, logger).await?;
        }

        Ok(())
    }

    pub async fn run_event_loop<S: OverlayFrameSubmitter>(
        &mut self,
        bridge: &mut BridgeClient,
        renderer: &CaptionRenderer,
        openvr: &mut S,
        logger: &OverlayLogger,
    ) -> Result<(), RuntimeFailure> {
        loop {
            match bridge.next_message().await {
                Ok(BridgeIncoming::Heartbeat) => continue,
                Ok(BridgeIncoming::Snapshot(snapshot)) => {
                    self.apply_snapshot(snapshot);
                    self.submit_frame_if_needed(renderer, openvr, bridge, logger)
                        .await?;
                }
                Ok(BridgeIncoming::Event(event)) => {
                    self.handle_event(event).await?;
                    if self.stopped {
                        return Ok(());
                    }
                    self.submit_frame_if_needed(renderer, openvr, bridge, logger)
                        .await?;
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

fn startup_error_from_preflight(error: OpenVrStartupPreflightError) -> StartupError {
    match error {
        OpenVrStartupPreflightError::SteamVrNotInstalled => StartupError::SteamVrNotInstalled,
        OpenVrStartupPreflightError::SteamVrNotRunning => StartupError::SteamVrNotRunning,
        OpenVrStartupPreflightError::HmdNotFound => StartupError::HmdNotFound,
        OpenVrStartupPreflightError::Init(message) => StartupError::OpenVrInit(message),
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

    if let Err(error) = perform_startup_preflight() {
        let startup_error = startup_error_from_preflight(error);
        emit_startup_failure(&logger, &startup_error).await;
        return startup_error.exit_code();
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

    let (renderer, mut openvr) = match initialize_runtime_resources(&manifest, &logger).await {
        Ok(resources) => resources,
        Err(error) => {
            let _ = bridge.close().await;
            emit_startup_failure(&logger, &error).await;
            return error.exit_code();
        }
    };

    let mut runtime = OverlayRuntime::new(snapshot);
    if let Err(error) = runtime
        .submit_frame_if_needed(&renderer, &mut openvr, &mut bridge, &logger)
        .await
    {
        let startup_error = startup_error_from_runtime_failure(error);
        let _ = bridge.close().await;
        emit_startup_failure(&logger, &startup_error).await;
        return startup_error.exit_code();
    }

    let runtime_result = runtime
        .run_event_loop(&mut bridge, &renderer, &mut openvr, &logger)
        .await;
    let _ = bridge.close().await;

    match runtime_result {
        Ok(()) => 0,
        Err(RuntimeFailure::RuntimeDisconnected) => 1,
        Err(error) => {
            let _ = logger.error(&error.to_string()).await;
            let _ = logger
                .emit_stdout_event(&json!({
                    "type": "runtime_error",
                    "failure_reason": error.failure_reason(),
                }))
                .await;
            1
        }
    }
}

pub async fn run_cli(args: &[String]) -> i32 {
    if args.len() == 2 && args[1] == "--version" {
        println!("{}", env!("CARGO_PKG_VERSION"));
        return 0;
    }

    if args.len() == 2 && args[1] == "--check-startup-contract" {
        println!(
            "{}",
            json!({
                "contract_version": EXPECTED_CONTRACT_VERSION,
                "app_version": env!("CARGO_PKG_VERSION"),
            })
        );
        return 0;
    }

    if args.len() != 3 || args[1] != "--config" {
        eprintln!(
            "usage: PuriPulyHeartOverlay --config <manifest.json> | --check-startup-contract | --version"
        );
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

fn startup_error_from_runtime_failure(error: RuntimeFailure) -> StartupError {
    match error {
        RuntimeFailure::Render(message) => StartupError::RendererInit(message),
        RuntimeFailure::OpenVr(message) => StartupError::OpenVrInit(message),
        RuntimeFailure::Bridge(message) => StartupError::Other(message),
        RuntimeFailure::RuntimeDisconnected => {
            StartupError::Other("runtime disconnected before ready".into())
        }
        RuntimeFailure::Stopped => StartupError::Other("runtime stopped before ready".into()),
    }
}

#[cfg(test)]
fn prepare_openvr_runtime<T, P, F>(
    overlay_instance_id: &str,
    preflight: P,
    overlay_factory: F,
) -> Result<T, StartupError>
where
    P: FnOnce() -> Result<(), OpenVrStartupPreflightError>,
    F: FnOnce(&str) -> Result<T, OpenVrError>,
{
    preflight().map_err(startup_error_from_preflight)?;
    overlay_factory(overlay_instance_id).map_err(|error| StartupError::OpenVrInit(error.to_string()))
}

async fn initialize_runtime_resources(
    manifest: &OverlayManifest,
    logger: &OverlayLogger,
) -> Result<(CaptionRenderer, OpenVrOverlay), StartupError> {
    let openvr = OpenVrOverlay::new(&manifest.overlay_instance_id)
        .map_err(|error| StartupError::OpenVrInit(error.to_string()))?;
    logger
        .info("openvr_ready")
        .await
        .map_err(|error| StartupError::Other(error.to_string()))?;
    let renderer = create_runtime_renderer()
        .map_err(|error| StartupError::RendererInit(error.to_string()))?;
    logger
        .info("renderer_resources_ready")
        .await
        .map_err(|error| StartupError::Other(error.to_string()))?;
    Ok((renderer, openvr))
}

fn create_runtime_renderer() -> Result<CaptionRenderer, crate::renderer::CaptionRenderError> {
    #[cfg(windows)]
    {
        CaptionRenderer::new()
    }

    #[cfg(not(windows))]
    {
        CaptionRenderer::new_for_test()
    }
}

impl OverlayRuntime {
    pub fn caption_blocks(&self) -> Vec<CaptionBlock> {
        self.state
            .blocks()
            .iter()
            .map(|block| {
                let channel = if block.channel == "peer" {
                    CaptionChannel::PeerChannel
                } else {
                    CaptionChannel::SelfChannel
                };
                CaptionBlock::new(block.id.clone(), block.text.clone()).with_channel(channel)
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::{prepare_openvr_runtime, OverlayRuntime, StartupError};
    use crate::openvr::{OpenVrError, OpenVrStartupPreflightError};
    use crate::state::{
        OverlayPresentationBlock, OverlayPresentationCalibration, OverlayPresentationSnapshot,
    };
    use std::cell::Cell;

    fn block(id: &str, channel: &str, text: &str) -> OverlayPresentationBlock {
        OverlayPresentationBlock {
            id: id.to_string(),
            channel: channel.to_string(),
            text: text.to_string(),
        }
    }

    #[test]
    fn caption_blocks_follow_snapshot_order_exactly() {
        let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
            revision: 3,
            calibration: OverlayPresentationCalibration::default(),
            blocks: vec![
                block("peer:1", "peer", "peer one"),
                block("self:2", "self", "self two (translated)"),
            ],
        });

        let blocks = runtime.caption_blocks();

        assert_eq!(
            blocks
                .iter()
                .map(|block| (block.id.as_str(), block.text.as_str()))
                .collect::<Vec<_>>(),
            vec![
                ("peer:1", "peer one"),
                ("self:2", "self two (translated)"),
            ]
        );
    }

    #[test]
    fn apply_snapshot_replaces_existing_blocks_and_calibration() {
        let mut runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
            revision: 1,
            calibration: OverlayPresentationCalibration::default(),
            blocks: vec![block("self:1", "self", "self one")],
        });

        runtime.apply_snapshot(OverlayPresentationSnapshot {
            revision: 2,
            calibration: OverlayPresentationCalibration {
                distance: 1.5,
                ..OverlayPresentationCalibration::default()
            },
            blocks: vec![block("peer:2", "peer", "peer two")],
        });

        let blocks = runtime.caption_blocks();

        assert_eq!(
            blocks
                .iter()
                .map(|block| (block.id.as_str(), block.text.as_str()))
                .collect::<Vec<_>>(),
            vec![("peer:2", "peer two")]
        );
        assert_eq!(runtime.state().snapshot().revision, 2);
        assert_eq!(runtime.state().snapshot().calibration.distance, 1.5);
    }

    #[test]
    fn runtime_uses_snapshot_blocks_without_internal_reordering() {
        let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
            revision: 4,
            calibration: OverlayPresentationCalibration::default(),
            blocks: vec![
                block("self:older", "self", "older"),
                block("peer:newer", "peer", "newer"),
            ],
        });

        let blocks = runtime.caption_blocks();

        assert_eq!(
            blocks
                .iter()
                .map(|block| (block.id.as_str(), block.text.as_str()))
                .collect::<Vec<_>>(),
            vec![
                ("self:older", "older"),
                ("peer:newer", "newer"),
            ]
        );
    }

    #[test]
    fn runtime_starts_empty_when_snapshot_has_no_blocks() {
        let runtime = OverlayRuntime::new(OverlayPresentationSnapshot {
            revision: 0,
            calibration: OverlayPresentationCalibration::default(),
            blocks: vec![],
        });

        assert!(runtime.caption_blocks().is_empty());
        assert_eq!(runtime.state().snapshot().revision, 0);
        assert_eq!(
            runtime.state().snapshot().calibration,
            OverlayPresentationCalibration::default()
        );
    }

    #[test]
    fn prepare_openvr_runtime_stops_before_overlay_factory_when_preflight_fails() {
        let overlay_factory_calls = Cell::new(0);

        let result = prepare_openvr_runtime(
            "overlay-test",
            || Err(OpenVrStartupPreflightError::SteamVrNotRunning),
            |_| {
                overlay_factory_calls.set(overlay_factory_calls.get() + 1);
                Ok(())
            },
        );

        assert_eq!(result, Err(StartupError::SteamVrNotRunning));
        assert_eq!(overlay_factory_calls.get(), 0);
    }

    #[test]
    fn prepare_openvr_runtime_initializes_overlay_after_successful_preflight() {
        let overlay_factory_calls = Cell::new(0);

        let result = prepare_openvr_runtime("overlay-test", || Ok(()), |_| {
            overlay_factory_calls.set(overlay_factory_calls.get() + 1);
            Ok::<_, OpenVrError>("overlay-ready")
        });

        assert_eq!(result, Ok("overlay-ready"));
        assert_eq!(overlay_factory_calls.get(), 1);
    }
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
