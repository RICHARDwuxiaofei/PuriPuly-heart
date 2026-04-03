use std::collections::HashMap;

#[cfg(windows)]
use windows::Win32::Graphics::Direct2D::ID2D1CommandList;
#[cfg(windows)]
use windows::Win32::Graphics::DirectWrite::IDWriteTextFormat;

use super::types::{BlockBounds, LayoutCacheKey, LineRole, VisualBounds};
#[cfg(windows)]
use super::types::{BlockCacheKey, LineCacheKey};

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct CachedLineLayoutTemplate {
    pub text: String,
    pub role: LineRole,
    pub width_px: f32,
    pub origin_x: f32,
    pub origin_y: f32,
    pub font_size_px: f32,
    pub visual_bounds: VisualBounds,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct CachedBlockLayoutTemplate {
    pub primary_lines: Vec<CachedLineLayoutTemplate>,
    pub secondary_line: Option<CachedLineLayoutTemplate>,
    pub secondary_reserved: bool,
    pub bounds: BlockBounds,
    pub visual_bounds: VisualBounds,
    pub content_width_px: f32,
    pub truncated_primary: bool,
    pub truncated_secondary: bool,
}

#[allow(dead_code)]
#[derive(Debug, Default)]
pub(crate) struct LayoutCache {
    entries: HashMap<LayoutCacheKey, CachedBlockLayoutTemplate>,
}

#[allow(dead_code)]
impl LayoutCache {
    pub(crate) fn get(&self, key: &LayoutCacheKey) -> Option<&CachedBlockLayoutTemplate> {
        self.entries.get(key)
    }

    pub(crate) fn insert(&mut self, key: LayoutCacheKey, value: CachedBlockLayoutTemplate) {
        self.entries.insert(key, value);
    }
}

#[cfg(windows)]
#[derive(Debug, Clone)]
pub(crate) struct CachedLineVisual {
    pub command_list: ID2D1CommandList,
    pub visual_bounds: VisualBounds,
}

#[cfg(windows)]
#[derive(Debug, Clone)]
pub(crate) struct CachedBlockVisual {
    pub command_list: ID2D1CommandList,
    #[allow(dead_code)]
    pub visual_bounds: VisualBounds,
}

#[cfg(windows)]
#[derive(Debug, Default)]
pub(crate) struct WindowsRendererCaches {
    pub text_format_cache: HashMap<(super::types::TextScriptBucket, u32), IDWriteTextFormat>,
    #[allow(dead_code)]
    pub layout_cache: LayoutCache,
    pub line_cache: HashMap<LineCacheKey, CachedLineVisual>,
    pub block_cache: HashMap<BlockCacheKey, CachedBlockVisual>,
}
