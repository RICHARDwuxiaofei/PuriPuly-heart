pub mod bridge;
pub mod logging;
pub mod manifest;
pub mod openvr;
pub mod renderer;
pub mod runtime;
pub mod state;

pub use bridge::{BridgeClient, BridgeError, BridgeIncoming, OverlayBridgeEvent};
pub use manifest::{load_manifest, validate_manifest, OverlayManifest, EXPECTED_CONTRACT_VERSION};
pub use openvr::{
    submit_texture, FakeOpenVr, OpenVrError, OpenVrOverlay, OverlayFrameSubmitter,
    OverlayPlacementPolicy,
};
pub use renderer::{
    CaptionBlock, CaptionChannel, CaptionLayoutPolicy, CaptionLayoutResult,
    CaptionPresentation, CaptionRenderError, CaptionRenderer, RenderedFrame,
    VisibleCaptionBlock,
};
pub use runtime::{run_cli, run_with_manifest, OverlayRuntime, RuntimeFailure, StartupError};
pub use state::{
    Event, OverlayCalibration, OverlayCalibrationUpdateEvent, OverlayRow, OverlayState,
    OverlayStateSnapshot, RowEvent, ShutdownEvent, UtteranceClosedEvent,
};
