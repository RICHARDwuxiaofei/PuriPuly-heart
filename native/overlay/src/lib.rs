pub mod bridge;
pub mod logging;
pub mod manifest;
pub mod renderer;
pub mod runtime;
pub mod state;

pub use bridge::{BridgeClient, BridgeError, BridgeIncoming, OverlayBridgeEvent};
pub use manifest::{load_manifest, validate_manifest, OverlayManifest, EXPECTED_CONTRACT_VERSION};
pub use renderer::{
    CaptionBlock, CaptionLayoutPolicy, CaptionLayoutResult, CaptionRenderError,
    CaptionRenderer, RenderedFrame, VisibleCaptionBlock,
};
pub use runtime::{run_cli, run_with_manifest, OverlayRuntime, RuntimeFailure, StartupError};
pub use state::{
    Event, OverlayRow, OverlayState, OverlayStateSnapshot, RowEvent, ShutdownEvent,
    UtteranceClosedEvent,
};
