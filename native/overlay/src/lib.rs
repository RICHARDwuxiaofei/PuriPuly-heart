pub mod bridge;
pub mod logging;
pub mod manifest;
pub mod openvr;
pub mod presentation;
pub mod renderer;
pub mod runtime;
pub mod state;

pub use bridge::{
    BridgeClient, BridgeError, BridgeIncoming, OverlayBridgeEvent, OverlayRuntimeControl,
};
pub use logging::{OverlayLogger, OverlayLoggingMode};
pub use manifest::{load_manifest, validate_manifest, OverlayManifest, EXPECTED_CONTRACT_VERSION};
pub use openvr::{
    submit_texture, FakeOpenVr, OpenVrError, OpenVrOutputAdapter, OpenVrOverlay,
    OverlayFrameSubmitter, OverlayPlacementPolicy,
};
pub use presentation::{
    AdapterIdentity, AdapterMatch, CompositorAttribution, PendingPresentationDiagnostics,
    PhysicalHmdVisibility, PresentationBackend, PresentationCause, PresentationCauseChannel,
    PresentationCauseKind, PresentationCauses, PresentationCorrelation,
    PresentationDiagnosticRecord, PresentationDiagnostics, PresentationOutcome, PresentationStage,
    PresentationStrategy, ReadinessCancellation, ReadinessOutcome,
};
#[cfg(windows)]
pub use renderer::WindowsBundledFontCollection;
pub use renderer::{
    bundled_font_path_from_exe_dir, runtime_bundled_font_path, BlockBounds, BundledFaceId,
    CaptionBlock, CaptionBlockVariant, CaptionChannel, CaptionDebugOverlay, CaptionLayoutPolicy,
    CaptionLayoutResult, CaptionLineLayout, CaptionPresentation, CaptionRenderError,
    CaptionRenderer, DamageBand, FontFallbackReason, FontLanguageBucket, FontResolver, FontSource,
    FontWeight, RenderedFrame, ResolvedFontStyle, StyleBucketSourceCount, TextFamilyKey,
    TextLocaleKey, TextStyleDescriptor, TextStyleKey, VisibleCaptionBlock,
};
pub use runtime::{
    run_cli, run_with_manifest, NativePresentationOwner, OverlayRuntime, RuntimeFailure,
    StartupError, NATIVE_FRESH_RETRY_CADENCE, NATIVE_FRESH_RETRY_DEADLINE,
    NATIVE_FRESH_RETRY_MAX_COMPLETED,
};
pub use state::{
    NativeFreshRenderGenerations, OverlayCalibration, OverlayPresentationBlock,
    OverlayPresentationBlockVariant, OverlayPresentationCalibration, OverlayPresentationSnapshot,
    OverlayScene, OverlaySlot, OverlayState, OverlayStateSnapshot,
};
