mod backend;
mod cache;
mod glyph_run;
mod layout;
mod types;

pub use backend::{CaptionRenderer, RenderedFrame};
pub use layout::CaptionLayoutPolicy;
#[allow(unused_imports)]
pub(crate) use types::{
    effective_background_alpha, fill_color_for_channel, outline_offsets_px, text_script_bucket,
    TextScriptBucket,
};
pub use types::{
    BlockBounds, BlockCacheKey, CaptionBlock, CaptionBlockVariant, CaptionChannel,
    CaptionLayoutResult, CaptionLineLayout, CaptionPresentation, CaptionRenderError, DamageBand,
    LayoutCacheKey, LineCacheKey, LineRole, RenderDiagnostics, ResolvedBlockLayout,
    ResolvedFrameLayout, ResolvedLineLayout, VisibleCaptionBlock, VisualBounds,
};
