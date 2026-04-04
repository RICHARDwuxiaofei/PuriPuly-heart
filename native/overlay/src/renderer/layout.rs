#[cfg(windows)]
use super::cache::LayoutCache;
use super::cache::{CachedBlockLayoutTemplate, CachedLineLayoutTemplate};
use super::types::{
    BlockBounds, CaptionBlock, CaptionLayoutResult, CaptionPresentation, LayoutCacheKey, LineRole,
    ResolvedBlockLayout, ResolvedFrameLayout, ResolvedLineLayout, VisualBounds,
    DEFAULT_AVERAGE_GLYPH_ADVANCE_PX, DEFAULT_BLOCK_SPACING_PX, DEFAULT_FONT_SIZE_PX,
    DEFAULT_HORIZONTAL_PADDING_PX, DEFAULT_PRIMARY_LINE_HEIGHT_PX,
    DEFAULT_SECONDARY_LINE_HEIGHT_PX, DEFAULT_STRIP_HORIZONTAL_PADDING_PX,
    DEFAULT_STRIP_VERTICAL_PADDING_PX, DEFAULT_SURFACE_HEIGHT_PX, DEFAULT_SURFACE_WIDTH_PX,
    DEFAULT_VERTICAL_PADDING_PX, SECONDARY_FONT_SCALE, SLOT_ACCENT_WIDTH_PX,
    TEXT_OUTLINE_OVERHANG_PX,
};
#[cfg(windows)]
use windows::core::PCWSTR;
#[cfg(windows)]
use windows::Win32::Graphics::DirectWrite::{
    DWriteCreateFactory, IDWriteFactory, IDWriteFactory2, IDWriteFontCollection,
    IDWriteFontFallback, IDWriteFontFamily, IDWriteInlineObject, IDWriteTextFormat,
    IDWriteTextFormat1, IDWriteTextLayout, IDWriteTextLayout2, DWRITE_FACTORY_TYPE_SHARED,
    DWRITE_FONT_STRETCH_NORMAL, DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_WEIGHT_MEDIUM,
    DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_WEIGHT_SEMI_BOLD, DWRITE_TEXT_ALIGNMENT_CENTER,
    DWRITE_TEXT_METRICS, DWRITE_TRIMMING, DWRITE_TRIMMING_GRANULARITY_CHARACTER,
    DWRITE_WORD_WRAPPING_NO_WRAP,
};
#[cfg(windows)]
use windows_core::Interface;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptionLayoutPolicy {
    preferred_weights: [&'static str; 3],
    latin_face_chain: [&'static str; 3],
    cjk_face_chain: [&'static str; 10],
    channel_uses_color_only: bool,
    show_speaker_labels_by_default: bool,
    visible_window_target_blocks: usize,
    horizontal_padding_px: u32,
    vertical_padding_px: u32,
    primary_line_height_px: u32,
    secondary_line_height_px: u32,
    block_spacing_px: u32,
    strip_horizontal_padding_px: u32,
    strip_vertical_padding_px: u32,
    average_glyph_advance_px: u32,
}

impl Default for CaptionLayoutPolicy {
    fn default() -> Self {
        Self {
            preferred_weights: ["Semibold", "Medium", "Regular"],
            latin_face_chain: ["Noto Sans", "Segoe UI", "DirectWrite system fallback"],
            cjk_face_chain: [
                "Noto Sans CJK KR",
                "Noto Sans CJK JP",
                "Noto Sans CJK SC",
                "Noto Sans CJK TC",
                "Malgun Gothic",
                "Yu Gothic UI",
                "Microsoft YaHei UI",
                "Microsoft JhengHei UI",
                "Segoe UI",
                "DirectWrite system fallback",
            ],
            channel_uses_color_only: true,
            show_speaker_labels_by_default: false,
            visible_window_target_blocks: 2,
            horizontal_padding_px: DEFAULT_HORIZONTAL_PADDING_PX,
            vertical_padding_px: DEFAULT_VERTICAL_PADDING_PX,
            primary_line_height_px: DEFAULT_PRIMARY_LINE_HEIGHT_PX,
            secondary_line_height_px: DEFAULT_SECONDARY_LINE_HEIGHT_PX,
            block_spacing_px: DEFAULT_BLOCK_SPACING_PX,
            strip_horizontal_padding_px: DEFAULT_STRIP_HORIZONTAL_PADDING_PX,
            strip_vertical_padding_px: DEFAULT_STRIP_VERTICAL_PADDING_PX,
            average_glyph_advance_px: DEFAULT_AVERAGE_GLYPH_ADVANCE_PX,
        }
    }
}

impl CaptionLayoutPolicy {
    pub fn preferred_weights(&self) -> Vec<&'static str> {
        self.preferred_weights.to_vec()
    }

    pub fn latin_face_chain(&self) -> &[&'static str] {
        &self.latin_face_chain
    }

    pub fn cjk_face_chain(&self) -> &[&'static str] {
        &self.cjk_face_chain
    }

    pub fn visible_window_target_blocks(&self) -> usize {
        self.visible_window_target_blocks
    }

    pub fn channel_uses_color_only(&self) -> bool {
        self.channel_uses_color_only
    }

    pub fn show_speaker_labels_by_default(&self) -> bool {
        self.show_speaker_labels_by_default
    }

    pub fn default_surface_size(&self) -> (u32, u32) {
        (DEFAULT_SURFACE_WIDTH_PX, DEFAULT_SURFACE_HEIGHT_PX)
    }

    #[cfg_attr(not(windows), allow(dead_code))]
    pub(crate) fn strip_horizontal_padding_px(&self) -> u32 {
        self.strip_horizontal_padding_px
    }

    pub fn layout_blocks(
        &self,
        blocks: Vec<CaptionBlock>,
        surface_width_px: u32,
        surface_height_px: u32,
    ) -> CaptionLayoutResult {
        self.layout_blocks_for_presentation(
            blocks,
            surface_width_px,
            surface_height_px,
            &CaptionPresentation::default(),
        )
    }

    pub fn layout_blocks_for_presentation(
        &self,
        blocks: Vec<CaptionBlock>,
        surface_width_px: u32,
        surface_height_px: u32,
        presentation: &CaptionPresentation,
    ) -> CaptionLayoutResult {
        self.resolve_blocks_for_presentation(
            blocks,
            surface_width_px,
            surface_height_px,
            presentation,
        )
        .into()
    }

    #[cfg_attr(not(windows), allow(dead_code))]
    pub(crate) fn layout_cache_key_for_block(
        &self,
        block: &CaptionBlock,
        surface_width_px: u32,
        presentation: &CaptionPresentation,
    ) -> LayoutCacheKey {
        layout_cache_key_for_block(
            block,
            self.content_width_px(surface_width_px),
            presentation.text_scale.max(0.1),
        )
    }

    pub fn resolve_blocks_for_presentation(
        &self,
        blocks: Vec<CaptionBlock>,
        surface_width_px: u32,
        surface_height_px: u32,
        presentation: &CaptionPresentation,
    ) -> ResolvedFrameLayout {
        #[cfg(windows)]
        if let Ok(layout) = self.resolve_blocks_for_presentation_windows_cached(
            blocks.clone(),
            surface_width_px,
            surface_height_px,
            presentation,
            None,
        ) {
            return layout;
        }

        self.resolve_blocks_for_presentation_fallback(
            blocks,
            surface_width_px,
            surface_height_px,
            presentation,
        )
    }

    fn resolve_blocks_for_presentation_fallback(
        &self,
        blocks: Vec<CaptionBlock>,
        surface_width_px: u32,
        surface_height_px: u32,
        presentation: &CaptionPresentation,
    ) -> ResolvedFrameLayout {
        let content_width_px = self.content_width_px(surface_width_px);
        let text_scale = presentation.text_scale.max(0.1);
        let strip_left_px = self.horizontal_padding_px as f32;
        let mut top_px = self.vertical_padding_px as f32;
        let mut resolved_blocks = Vec::with_capacity(blocks.len());

        for block in blocks {
            let layout_cache_key = layout_cache_key_for_block(&block, content_width_px, text_scale);
            let template = self.build_fallback_block_template(&block, content_width_px, text_scale);
            let stable_block_height_px = template.bounds.bottom_px - template.bounds.top_px;
            let block_top_px = if block.slot_assigned {
                block.slot_top_px
            } else {
                top_px
            };
            resolved_blocks.push(materialize_resolved_block_layout(
                &block,
                layout_cache_key,
                &template,
                strip_left_px,
                block_top_px,
            ));
            if !block.slot_assigned {
                top_px += stable_block_height_px + self.block_spacing_px as f32;
            }
        }

        ResolvedFrameLayout {
            visible_blocks: resolved_blocks,
            dropped_block_ids: Vec::new(),
            surface_width_px,
            surface_height_px,
            damage_band: None,
        }
    }

    pub fn measured_block_height_px(
        &self,
        secondary_enabled: bool,
        text_scale: f32,
        height_scale: f32,
    ) -> f32 {
        self.stable_block_height_px(secondary_enabled, text_scale) * height_scale
    }

    pub(crate) fn stable_block_height_px(&self, secondary_enabled: bool, text_scale: f32) -> f32 {
        let primary_lines = if secondary_enabled { 2 } else { 3 };
        let secondary_lines: u32 = if secondary_enabled { 1 } else { 0 };
        let base_height_px = self.strip_vertical_padding_px.saturating_mul(2)
            + primary_lines * self.primary_line_height_px
            + secondary_lines.saturating_mul(self.secondary_line_height_px);
        base_height_px as f32 * text_scale.max(0.1)
    }

    fn content_width_px(&self, surface_width_px: u32) -> f32 {
        surface_width_px
            .saturating_sub(self.horizontal_padding_px.saturating_mul(2))
            .saturating_sub(self.strip_horizontal_padding_px.saturating_mul(2))
            .max(self.average_glyph_advance_px) as f32
    }

    fn primary_line_budget(&self, block: &CaptionBlock) -> usize {
        if block.secondary_enabled {
            2
        } else {
            3
        }
    }

    fn build_fallback_block_template(
        &self,
        block: &CaptionBlock,
        content_width_px: f32,
        text_scale: f32,
    ) -> CachedBlockLayoutTemplate {
        let content_width_px = content_width_px.max(1.0);
        let primary_budget = self.primary_line_budget(block);
        let primary_font_size_px = DEFAULT_FONT_SIZE_PX * text_scale;
        let secondary_font_size_px = primary_font_size_px * SECONDARY_FONT_SCALE;
        let primary_line_height_px = self.primary_line_height_px as f32 * text_scale;
        let vertical_padding_px = self.strip_vertical_padding_px as f32 * text_scale;
        let strip_width_px = content_width_px + self.strip_horizontal_padding_px as f32 * 2.0;
        let block_height_px = self.stable_block_height_px(block.secondary_enabled, text_scale);
        let local_bounds = BlockBounds::new(0.0, 0.0, strip_width_px, block_height_px);
        let primary_advance_px = self.average_glyph_advance_px as f32 * text_scale;
        let wrapped_primary = wrap_text(&block.primary_text, content_width_px, primary_advance_px);
        let truncated_primary = wrapped_primary.len() > primary_budget;
        let primary_lines = wrapped_primary
            .into_iter()
            .take(primary_budget)
            .enumerate()
            .map(|(index, text)| {
                let width_px = measure_text_width(&text, primary_advance_px);
                let origin_x = self.strip_horizontal_padding_px as f32
                    + ((content_width_px - width_px).max(0.0) * 0.5);
                let origin_y = vertical_padding_px + index as f32 * primary_line_height_px;
                CachedLineLayoutTemplate {
                    visual_bounds: line_visual_bounds(0.0, 0.0, width_px, primary_font_size_px)
                        .translate(origin_x, origin_y),
                    text,
                    role: LineRole::Primary,
                    width_px,
                    origin_x,
                    origin_y,
                    font_size_px: primary_font_size_px,
                }
            })
            .collect::<Vec<_>>();
        let (secondary_text, truncated_secondary) = if block.secondary_enabled {
            ellipsize_text(
                &block.secondary_text,
                content_width_px,
                primary_advance_px * SECONDARY_FONT_SCALE,
            )
        } else {
            (None, false)
        };
        let secondary_line = secondary_text.map(|text| {
            let width_px = measure_text_width(&text, primary_advance_px * SECONDARY_FONT_SCALE);
            let origin_x = self.strip_horizontal_padding_px as f32
                + ((content_width_px - width_px).max(0.0) * 0.5);
            let origin_y = vertical_padding_px + primary_budget as f32 * primary_line_height_px;
            CachedLineLayoutTemplate {
                visual_bounds: line_visual_bounds(0.0, 0.0, width_px, secondary_font_size_px)
                    .translate(origin_x, origin_y),
                text,
                role: LineRole::Secondary,
                width_px,
                origin_x,
                origin_y,
                font_size_px: secondary_font_size_px,
            }
        });
        let local_visual_bounds = block_visual_bounds_from_templates(
            local_bounds,
            &primary_lines,
            secondary_line.as_ref(),
        );

        CachedBlockLayoutTemplate {
            primary_lines,
            secondary_line,
            secondary_reserved: block.secondary_enabled,
            bounds: local_bounds,
            visual_bounds: local_visual_bounds,
            content_width_px,
            truncated_primary,
            truncated_secondary,
        }
    }

    #[cfg(windows)]
    fn build_windows_block_template(
        &self,
        engine: &DirectWriteLayoutEngine,
        block: &CaptionBlock,
        content_width_px: f32,
        text_scale: f32,
    ) -> Result<CachedBlockLayoutTemplate, windows::core::Error> {
        let primary_budget = self.primary_line_budget(block);
        let primary_font_size_px = DEFAULT_FONT_SIZE_PX * text_scale;
        let secondary_font_size_px = primary_font_size_px * SECONDARY_FONT_SCALE;
        let primary_line_height_px = self.primary_line_height_px as f32 * text_scale;
        let vertical_padding_px = self.strip_vertical_padding_px as f32 * text_scale;
        let block_height_px = self.stable_block_height_px(block.secondary_enabled, text_scale);
        let local_bounds = BlockBounds::new(
            0.0,
            0.0,
            content_width_px + self.strip_horizontal_padding_px as f32 * 2.0,
            block_height_px,
        );

        let (primary_lines_text, truncated_primary) = engine.wrap_primary_text(
            self,
            &block.primary_text,
            content_width_px,
            primary_font_size_px,
            primary_budget,
        )?;
        let primary_lines = primary_lines_text
            .iter()
            .enumerate()
            .map(|(index, text)| {
                let measured = engine.measure_centered_line(
                    self,
                    text,
                    content_width_px,
                    primary_font_size_px,
                )?;
                let origin_x = self.strip_horizontal_padding_px as f32 + measured.origin_x_px;
                let origin_y = vertical_padding_px + index as f32 * primary_line_height_px;
                Ok::<CachedLineLayoutTemplate, windows::core::Error>(CachedLineLayoutTemplate {
                    text: text.clone(),
                    role: LineRole::Primary,
                    width_px: measured.width_px,
                    origin_x,
                    origin_y,
                    font_size_px: primary_font_size_px,
                    visual_bounds: measured.visual_bounds.translate(origin_x, origin_y),
                })
            })
            .collect::<Result<Vec<_>, windows::core::Error>>()?;

        let (secondary_line, truncated_secondary) = if block.secondary_enabled {
            let (text, truncated) = engine.ellipsize_secondary_text(
                self,
                &block.secondary_text,
                content_width_px,
                secondary_font_size_px,
            )?;
            let line = text
                .as_ref()
                .map(|text| {
                    let measured = engine.measure_centered_line(
                        self,
                        text,
                        content_width_px,
                        secondary_font_size_px,
                    )?;
                    let origin_x = self.strip_horizontal_padding_px as f32 + measured.origin_x_px;
                    let origin_y =
                        vertical_padding_px + primary_budget as f32 * primary_line_height_px;
                    Ok::<CachedLineLayoutTemplate, windows::core::Error>(CachedLineLayoutTemplate {
                        text: text.clone(),
                        role: LineRole::Secondary,
                        width_px: measured.width_px,
                        origin_x,
                        origin_y,
                        font_size_px: secondary_font_size_px,
                        visual_bounds: measured.visual_bounds.translate(origin_x, origin_y),
                    })
                })
                .transpose()?;
            (line, truncated)
        } else {
            (None, false)
        };
        let visual_bounds = block_visual_bounds_from_templates(
            local_bounds,
            &primary_lines,
            secondary_line.as_ref(),
        );

        Ok(CachedBlockLayoutTemplate {
            primary_lines,
            secondary_line,
            secondary_reserved: block.secondary_enabled,
            bounds: local_bounds,
            visual_bounds,
            content_width_px,
            truncated_primary,
            truncated_secondary,
        })
    }

    #[cfg(windows)]
    pub(crate) fn resolve_blocks_for_presentation_windows_cached(
        &self,
        blocks: Vec<CaptionBlock>,
        surface_width_px: u32,
        surface_height_px: u32,
        presentation: &CaptionPresentation,
        mut layout_cache: Option<&mut LayoutCache>,
    ) -> Result<ResolvedFrameLayout, windows::core::Error> {
        let engine = DirectWriteLayoutEngine::new()?;
        let content_width_px = self.content_width_px(surface_width_px);
        let text_scale = presentation.text_scale.max(0.1);
        let strip_left_px = self.horizontal_padding_px as f32;
        let mut top_px = self.vertical_padding_px as f32;
        let mut resolved_blocks = Vec::with_capacity(blocks.len());

        for block in blocks {
            let layout_cache_key = layout_cache_key_for_block(&block, content_width_px, text_scale);
            let template = if let Some(cache) = layout_cache.as_deref_mut() {
                if let Some(cached) = cache.get(&layout_cache_key) {
                    cached.clone()
                } else {
                    let template = self.build_windows_block_template(
                        &engine,
                        &block,
                        content_width_px,
                        text_scale,
                    )?;
                    cache.insert(layout_cache_key.clone(), template.clone());
                    template
                }
            } else {
                self.build_windows_block_template(&engine, &block, content_width_px, text_scale)?
            };
            let stable_block_height_px = template.bounds.bottom_px - template.bounds.top_px;
            let block_top_px = if block.slot_assigned {
                block.slot_top_px
            } else {
                top_px
            };
            resolved_blocks.push(materialize_resolved_block_layout(
                &block,
                layout_cache_key,
                &template,
                strip_left_px,
                block_top_px,
            ));
            if !block.slot_assigned {
                top_px += stable_block_height_px + self.block_spacing_px as f32;
            }
        }

        Ok(ResolvedFrameLayout {
            visible_blocks: resolved_blocks,
            dropped_block_ids: Vec::new(),
            surface_width_px,
            surface_height_px,
            damage_band: None,
        })
    }
}

#[cfg(windows)]
#[derive(Debug, Clone, Copy)]
struct MeasuredLine {
    width_px: f32,
    origin_x_px: f32,
    visual_bounds: VisualBounds,
}

#[cfg(windows)]
struct DirectWriteLayoutEngine {
    factory: IDWriteFactory,
    system_font_collection: IDWriteFontCollection,
    system_font_fallback: IDWriteFontFallback,
}

#[cfg(windows)]
impl DirectWriteLayoutEngine {
    fn new() -> Result<Self, windows::core::Error> {
        let factory: IDWriteFactory = unsafe { DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED)? };
        let factory2: IDWriteFactory2 = factory.cast()?;
        let mut collection = None;
        unsafe {
            factory.GetSystemFontCollection(&mut collection, false)?;
        }
        Ok(Self {
            factory,
            system_font_collection: collection.expect("system font collection"),
            system_font_fallback: unsafe { factory2.GetSystemFontFallback()? },
        })
    }

    fn wrap_primary_text(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        max_width_px: f32,
        font_size_px: f32,
        budget: usize,
    ) -> Result<(Vec<String>, bool), windows::core::Error> {
        let mut lines = Vec::new();
        let mut remaining = text.trim();
        if remaining.is_empty() {
            return Ok((vec![String::new()], false));
        }

        while !remaining.is_empty() && lines.len() < budget {
            let line =
                self.longest_fitting_prefix(policy, remaining, max_width_px, font_size_px)?;
            if line.is_empty() {
                break;
            }
            let trimmed_line = line.trim().to_string();
            lines.push(trimmed_line.clone());
            remaining = remaining[line.len()..].trim_start();
        }

        let truncated = !remaining.is_empty();
        if truncated {
            let prefix = lines
                .iter()
                .take(lines.len().saturating_sub(1))
                .map(String::as_str)
                .collect::<Vec<_>>();
            let remaining_text = if prefix.is_empty() {
                text.trim()
            } else {
                remaining
            };
            let ellipsized =
                self.ellipsize_text(policy, remaining_text, max_width_px, font_size_px)?;
            if let Some(last) = lines.last_mut() {
                *last = ellipsized;
            }
        }

        Ok((lines, truncated))
    }

    fn ellipsize_secondary_text(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        max_width_px: f32,
        font_size_px: f32,
    ) -> Result<(Option<String>, bool), windows::core::Error> {
        let trimmed = text.trim();
        if trimmed.is_empty() {
            return Ok((None, false));
        }
        if self
            .measure_centered_line(policy, trimmed, max_width_px, font_size_px)?
            .width_px
            <= max_width_px
        {
            return Ok((Some(trimmed.to_string()), false));
        }
        Ok((
            Some(self.ellipsize_text(policy, trimmed, max_width_px, font_size_px)?),
            true,
        ))
    }

    fn ellipsize_text(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        max_width_px: f32,
        font_size_px: f32,
    ) -> Result<String, windows::core::Error> {
        let trimmed = text.trim();
        let ellipsis = "...";
        let ellipsis_width = self
            .measure_centered_line(policy, ellipsis, max_width_px, font_size_px)?
            .width_px;
        if ellipsis_width >= max_width_px {
            return Ok(ellipsis.to_string());
        }

        let mut best = String::new();
        let chars = trimmed.char_indices().collect::<Vec<_>>();
        let mut low = 0usize;
        let mut high = chars.len();
        while low <= high {
            let mid = (low + high) / 2;
            let candidate = match chars.get(mid) {
                Some((index, _)) => format!("{}{}", &trimmed[..*index], ellipsis),
                None => format!("{trimmed}{ellipsis}"),
            };
            let fits = self
                .measure_centered_line(policy, &candidate, max_width_px, font_size_px)?
                .width_px
                <= max_width_px;
            if fits {
                best = candidate;
                low = mid.saturating_add(1);
            } else if mid == 0 {
                break;
            } else {
                high = mid - 1;
            }
        }

        Ok(if best.is_empty() {
            ellipsis.to_string()
        } else {
            best
        })
    }

    fn longest_fitting_prefix<'a>(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &'a str,
        max_width_px: f32,
        font_size_px: f32,
    ) -> Result<&'a str, windows::core::Error> {
        let trimmed = text.trim_start();
        if trimmed.is_empty() {
            return Ok("");
        }

        if self
            .measure_centered_line(policy, trimmed, max_width_px, font_size_px)?
            .width_px
            <= max_width_px
        {
            return Ok(trimmed);
        }

        let mut best_end = 0usize;
        for (index, ch) in trimmed.char_indices() {
            let end = index + ch.len_utf8();
            let candidate = trimmed[..end].trim_end();
            if candidate.is_empty() {
                continue;
            }
            if self
                .measure_centered_line(policy, candidate, max_width_px, font_size_px)?
                .width_px
                <= max_width_px
            {
                best_end = if ch.is_whitespace() { index } else { end };
                continue;
            }
            break;
        }

        if best_end == 0 {
            for (index, ch) in trimmed.char_indices() {
                let end = index + ch.len_utf8();
                let candidate = &trimmed[..end];
                if self
                    .measure_centered_line(policy, candidate, max_width_px, font_size_px)?
                    .width_px
                    > max_width_px
                {
                    return Ok(trimmed[..index].trim_end());
                }
                if end == trimmed.len() {
                    return Ok(candidate);
                }
            }
        }

        let prefix = trimmed[..best_end].trim_end();
        if prefix.is_empty() {
            Ok(trimmed
                .chars()
                .next()
                .map(|ch| &trimmed[..ch.len_utf8()])
                .unwrap_or(""))
        } else {
            Ok(prefix)
        }
    }

    fn measure_centered_line(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        content_width_px: f32,
        font_size_px: f32,
    ) -> Result<MeasuredLine, windows::core::Error> {
        let text_layout = self.create_text_layout(
            policy,
            text,
            font_size_px,
            content_width_px,
            font_size_px * 1.5,
            DWRITE_WORD_WRAPPING_NO_WRAP,
            None,
        )?;
        let mut metrics = DWRITE_TEXT_METRICS::default();
        unsafe {
            text_layout.GetMetrics(&mut metrics)?;
        }
        let overhang = unsafe { text_layout.GetOverhangMetrics()? };
        Ok(MeasuredLine {
            width_px: metrics.width,
            origin_x_px: metrics.left,
            visual_bounds: VisualBounds::new(
                metrics.left - overhang.left,
                -overhang.top,
                metrics.left + metrics.width + overhang.right,
                metrics.height + overhang.bottom,
            ),
        })
    }

    fn create_text_layout(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        font_size_px: f32,
        max_width_px: f32,
        max_height_px: f32,
        word_wrapping: windows::Win32::Graphics::DirectWrite::DWRITE_WORD_WRAPPING,
        trimming_sign: Option<&IDWriteInlineObject>,
    ) -> Result<IDWriteTextLayout, windows::core::Error> {
        let text_format =
            self.create_text_format(policy, text, font_size_px, word_wrapping, trimming_sign)?;
        let utf16: Vec<u16> = text.encode_utf16().collect();
        let text_layout = unsafe {
            self.factory
                .CreateTextLayout(&utf16, &text_format, max_width_px, max_height_px)?
        };
        if let Ok(text_layout_2) = text_layout.cast::<IDWriteTextLayout2>() {
            unsafe {
                text_layout_2.SetFontFallback(&self.system_font_fallback)?;
            }
        }
        Ok(text_layout)
    }

    fn create_text_format(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        font_size_px: f32,
        word_wrapping: windows::Win32::Graphics::DirectWrite::DWRITE_WORD_WRAPPING,
        trimming_sign: Option<&IDWriteInlineObject>,
    ) -> Result<IDWriteTextFormat, windows::core::Error> {
        let (family_name, weight) = self.resolve_text_style(policy, text)?;
        let locale = utf16_null("en-us");
        let face_name = utf16_null(&family_name);
        let text_format = unsafe {
            self.factory.CreateTextFormat(
                PCWSTR::from_raw(face_name.as_ptr()),
                None,
                weight,
                DWRITE_FONT_STYLE_NORMAL,
                DWRITE_FONT_STRETCH_NORMAL,
                font_size_px,
                PCWSTR::from_raw(locale.as_ptr()),
            )?
        };
        unsafe {
            text_format.SetWordWrapping(word_wrapping)?;
            text_format.SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER)?;
            if let Ok(text_format_1) = text_format.cast::<IDWriteTextFormat1>() {
                text_format_1.SetFontFallback(&self.system_font_fallback)?;
            }
            if let Some(trimming_sign) = trimming_sign {
                text_format.SetTrimming(
                    &DWRITE_TRIMMING {
                        granularity: DWRITE_TRIMMING_GRANULARITY_CHARACTER,
                        delimiter: 0,
                        delimiterCount: 0,
                    },
                    trimming_sign,
                )?;
            }
        }
        Ok(text_format)
    }

    fn resolve_text_style(
        &self,
        policy: &CaptionLayoutPolicy,
        text: &str,
    ) -> Result<
        (
            String,
            windows::Win32::Graphics::DirectWrite::DWRITE_FONT_WEIGHT,
        ),
        windows::core::Error,
    > {
        let bucket = super::types::text_script_bucket(text);
        for family_name in select_face_chain(policy, bucket)
            .iter()
            .copied()
            .filter(|candidate| *candidate != "DirectWrite system fallback")
        {
            let family = match self.find_font_family(family_name)? {
                Some(family) => family,
                None => continue,
            };
            let Some(weight) = resolve_family_weight(&family, policy)? else {
                continue;
            };
            return Ok((family_name.to_string(), weight));
        }
        Ok(("Segoe UI".to_string(), DWRITE_FONT_WEIGHT_NORMAL))
    }

    fn find_font_family(
        &self,
        family_name: &str,
    ) -> Result<Option<IDWriteFontFamily>, windows::core::Error> {
        let family_name = utf16_null(family_name);
        let mut index = 0;
        let mut exists = false.into();
        unsafe {
            self.system_font_collection.FindFamilyName(
                PCWSTR::from_raw(family_name.as_ptr()),
                &mut index,
                &mut exists,
            )?;
            if !exists.as_bool() {
                return Ok(None);
            }
            self.system_font_collection.GetFontFamily(index).map(Some)
        }
    }
}

pub(crate) fn resolved_layout_has_drawable_text(layout: &ResolvedFrameLayout) -> bool {
    layout.visible_blocks.iter().any(|block| {
        block
            .primary_lines
            .iter()
            .any(|line| !line.text.trim().is_empty())
            || block
                .secondary_line
                .as_ref()
                .is_some_and(|line| !line.text.trim().is_empty())
    })
}

fn layout_cache_key_for_block(
    block: &CaptionBlock,
    content_width_px: f32,
    text_scale: f32,
) -> LayoutCacheKey {
    LayoutCacheKey {
        primary_text: block.primary_text.clone(),
        secondary_text: block.secondary_text.clone(),
        channel: block.channel,
        block_variant: block.block_variant,
        secondary_enabled: block.secondary_enabled,
        primary_font_size_key: scalar_key(DEFAULT_FONT_SIZE_PX * text_scale),
        secondary_font_size_key: scalar_key(
            DEFAULT_FONT_SIZE_PX * text_scale * SECONDARY_FONT_SCALE,
        ),
        content_width_key: content_width_px.round() as u32,
        text_scale_key: scalar_key(text_scale),
    }
}

fn materialize_resolved_block_layout(
    block: &CaptionBlock,
    layout_cache_key: LayoutCacheKey,
    template: &CachedBlockLayoutTemplate,
    strip_left_px: f32,
    stable_top_px: f32,
) -> ResolvedBlockLayout {
    let render_top_px = stable_top_px + block.offset_y_px;
    let bounds = template
        .bounds
        .translate(strip_left_px, render_top_px)
        .scale_y_from_top(block.height_scale);
    let primary_lines = template
        .primary_lines
        .iter()
        .map(|line| {
            materialize_resolved_line_layout(line, strip_left_px, render_top_px, block.height_scale)
        })
        .collect::<Vec<_>>();
    let secondary_line = template.secondary_line.as_ref().map(|line| {
        materialize_resolved_line_layout(line, strip_left_px, render_top_px, block.height_scale)
    });
    let mut visual_bounds = template
        .visual_bounds
        .translate(strip_left_px, render_top_px)
        .scale_y_from_top(render_top_px, block.height_scale);
    let accent_bounds = if block.accent_opacity > f32::EPSILON {
        Some(BlockBounds::new(
            bounds.left_px,
            bounds.top_px,
            bounds.left_px + SLOT_ACCENT_WIDTH_PX,
            bounds.bottom_px,
        ))
    } else {
        None
    };
    if let Some(accent_bounds) = accent_bounds {
        visual_bounds = union_visual_and_block_bounds(visual_bounds, accent_bounds);
    }

    ResolvedBlockLayout {
        id: block.id.clone(),
        layout_cache_key,
        channel: block.channel,
        block_variant: block.block_variant,
        primary_lines,
        secondary_line,
        secondary_reserved: template.secondary_reserved,
        bounds,
        visual_bounds,
        accent_opacity: block.accent_opacity,
        accent_bounds,
        content_width_px: template.content_width_px,
        opacity: block.opacity,
        render_offset_y_px: block.offset_y_px,
        render_height_scale: block.height_scale,
        truncated_primary: template.truncated_primary,
        truncated_secondary: template.truncated_secondary,
    }
}

fn materialize_resolved_line_layout(
    line: &CachedLineLayoutTemplate,
    strip_left_px: f32,
    render_top_px: f32,
    render_height_scale: f32,
) -> ResolvedLineLayout {
    ResolvedLineLayout {
        text: line.text.clone(),
        role: line.role,
        width_px: line.width_px,
        origin_x: strip_left_px + line.origin_x,
        origin_y: render_top_px + line.origin_y * render_height_scale,
        font_size_px: line.font_size_px,
        visual_bounds: line
            .visual_bounds
            .translate(strip_left_px, render_top_px)
            .scale_y_from_top(render_top_px, render_height_scale),
    }
}

fn scalar_key(value: f32) -> u32 {
    (value * 100.0).round() as u32
}

fn line_visual_bounds(
    origin_x: f32,
    origin_y: f32,
    width_px: f32,
    font_size_px: f32,
) -> VisualBounds {
    VisualBounds::new(
        origin_x - TEXT_OUTLINE_OVERHANG_PX,
        origin_y - TEXT_OUTLINE_OVERHANG_PX,
        origin_x + width_px + TEXT_OUTLINE_OVERHANG_PX,
        origin_y + font_size_px * 1.15 + TEXT_OUTLINE_OVERHANG_PX,
    )
}

fn block_visual_bounds_from_templates(
    bounds: BlockBounds,
    primary_lines: &[CachedLineLayoutTemplate],
    secondary_line: Option<&CachedLineLayoutTemplate>,
) -> VisualBounds {
    let mut left_px = bounds.left_px - TEXT_OUTLINE_OVERHANG_PX;
    let mut top_px = bounds.top_px - TEXT_OUTLINE_OVERHANG_PX;
    let mut right_px = bounds.right_px + TEXT_OUTLINE_OVERHANG_PX;
    let mut bottom_px = bounds.bottom_px + TEXT_OUTLINE_OVERHANG_PX;

    for line in primary_lines
        .iter()
        .chain(secondary_line.iter().copied())
        .filter(|line| !line.text.trim().is_empty())
    {
        left_px = left_px.min(line.visual_bounds.left_px);
        top_px = top_px.min(line.visual_bounds.top_px);
        right_px = right_px.max(line.visual_bounds.right_px);
        bottom_px = bottom_px.max(line.visual_bounds.bottom_px);
    }

    VisualBounds::new(left_px, top_px, right_px, bottom_px)
}

fn union_visual_and_block_bounds(visual: VisualBounds, block: BlockBounds) -> VisualBounds {
    VisualBounds::new(
        visual.left_px.min(block.left_px),
        visual.top_px.min(block.top_px),
        visual.right_px.max(block.right_px),
        visual.bottom_px.max(block.bottom_px),
    )
}

fn wrap_text(text: &str, max_width_px: f32, average_glyph_advance_px: f32) -> Vec<String> {
    let mut lines = Vec::new();

    for paragraph in text.lines() {
        let words: Vec<&str> = paragraph.split_whitespace().collect();
        if words.is_empty() {
            lines.push(String::new());
            continue;
        }

        let mut current = String::new();
        for word in words {
            if current.is_empty() {
                push_word_chunks(
                    &mut lines,
                    &mut current,
                    word,
                    max_width_px,
                    average_glyph_advance_px,
                );
                continue;
            }

            let candidate = format!("{current} {word}");
            if measure_text_width(&candidate, average_glyph_advance_px) <= max_width_px {
                current.push(' ');
                current.push_str(word);
                continue;
            }

            lines.push(std::mem::take(&mut current));
            push_word_chunks(
                &mut lines,
                &mut current,
                word,
                max_width_px,
                average_glyph_advance_px,
            );
        }

        if !current.is_empty() {
            lines.push(current);
        }
    }

    if lines.is_empty() {
        lines.push(String::new());
    }

    lines
}

fn ellipsize_text(
    text: &str,
    max_width_px: f32,
    average_glyph_advance_px: f32,
) -> (Option<String>, bool) {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return (None, false);
    }
    if measure_text_width(trimmed, average_glyph_advance_px) <= max_width_px {
        return (Some(trimmed.to_string()), false);
    }

    let ellipsis = "...";
    let ellipsis_width = measure_text_width(ellipsis, average_glyph_advance_px);
    if ellipsis_width >= max_width_px {
        return (Some(ellipsis.to_string()), true);
    }

    let mut out = String::new();
    for ch in trimmed.chars() {
        let candidate = format!("{out}{ch}");
        if measure_text_width(&candidate, average_glyph_advance_px) + ellipsis_width > max_width_px
        {
            break;
        }
        out.push(ch);
    }

    if out.is_empty() {
        (Some(ellipsis.to_string()), true)
    } else {
        out.push_str(ellipsis);
        (Some(out), true)
    }
}

fn push_word_chunks(
    lines: &mut Vec<String>,
    current: &mut String,
    word: &str,
    max_width_px: f32,
    average_glyph_advance_px: f32,
) {
    if measure_text_width(word, average_glyph_advance_px) <= max_width_px {
        current.push_str(word);
        return;
    }

    let mut piece = String::new();
    for ch in word.chars() {
        let candidate = format!("{piece}{ch}");
        if !piece.is_empty()
            && measure_text_width(&candidate, average_glyph_advance_px) > max_width_px
        {
            if current.is_empty() {
                lines.push(std::mem::take(&mut piece));
            } else {
                lines.push(std::mem::take(current));
                lines.push(std::mem::take(&mut piece));
            }
        }
        piece.push(ch);
    }

    if !piece.is_empty() {
        if current.is_empty() {
            current.push_str(&piece);
        } else {
            lines.push(std::mem::take(current));
            current.push_str(&piece);
        }
    }
}

fn measure_text_width(text: &str, average_glyph_advance_px: f32) -> f32 {
    text.chars()
        .map(|ch| match ch {
            ' ' => average_glyph_advance_px * 0.45,
            '0'..='9' | 'A'..='Z' => average_glyph_advance_px * 0.68,
            'a'..='z' => average_glyph_advance_px * 0.62,
            '.' | ',' | ':' | ';' | '\'' | '"' | '!' | '?' | '(' | ')' | '[' | ']' | '{' | '}'
            | '-' | '_' | '/' => average_glyph_advance_px * 0.55,
            _ if super::types::contains_cjk(&ch.to_string()) => average_glyph_advance_px,
            _ => average_glyph_advance_px * 0.72,
        })
        .sum()
}

#[cfg(windows)]
fn select_face_chain<'a>(
    policy: &'a CaptionLayoutPolicy,
    bucket: super::types::TextScriptBucket,
) -> &'a [&'static str] {
    if matches!(bucket, super::types::TextScriptBucket::Cjk) {
        policy.cjk_face_chain()
    } else {
        policy.latin_face_chain()
    }
}

#[cfg(windows)]
fn resolve_family_weight(
    family: &IDWriteFontFamily,
    policy: &CaptionLayoutPolicy,
) -> Result<Option<windows::Win32::Graphics::DirectWrite::DWRITE_FONT_WEIGHT>, windows::core::Error>
{
    for weight in preferred_weight_chain(policy) {
        let font = unsafe {
            family.GetFirstMatchingFont(
                weight,
                DWRITE_FONT_STRETCH_NORMAL,
                DWRITE_FONT_STYLE_NORMAL,
            )?
        };
        if unsafe { font.GetWeight() } == weight {
            return Ok(Some(weight));
        }
    }
    Ok(None)
}

#[cfg(windows)]
fn preferred_weight_chain(
    policy: &CaptionLayoutPolicy,
) -> Vec<windows::Win32::Graphics::DirectWrite::DWRITE_FONT_WEIGHT> {
    policy
        .preferred_weights()
        .into_iter()
        .map(|weight| match weight {
            "Semibold" => DWRITE_FONT_WEIGHT_SEMI_BOLD,
            "Medium" => DWRITE_FONT_WEIGHT_MEDIUM,
            _ => DWRITE_FONT_WEIGHT_NORMAL,
        })
        .collect()
}

#[cfg(windows)]
fn utf16_null(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

#[cfg(test)]
mod tests {
    use super::{measure_text_width, wrap_text};
    use crate::renderer::{
        effective_background_alpha, fill_color_for_channel, outline_offsets_px, text_script_bucket,
        CaptionChannel, CaptionPresentation, TextScriptBucket,
    };

    #[test]
    fn wrap_text_splits_long_words_into_measured_chunks() {
        let lines = wrap_text("abcdefgh", 160.0, 80.0);
        assert_eq!(lines, vec!["abc", "def", "gh"]);
    }

    #[test]
    fn measure_text_width_treats_cjk_as_wider_than_latin() {
        assert!(measure_text_width("안녕", 80.0) > measure_text_width("hi", 80.0));
    }

    #[test]
    fn text_script_bucket_prefers_latin_for_non_cjk_text() {
        assert_eq!(text_script_bucket("hello world"), TextScriptBucket::Latin);
    }

    #[test]
    fn text_script_bucket_uses_cjk_bucket_for_korean_text() {
        assert_eq!(text_script_bucket("안녕하세요"), TextScriptBucket::Cjk);
    }

    #[test]
    fn fill_color_for_channel_uses_fixed_text_only_palette() {
        assert_eq!(
            fill_color_for_channel(CaptionChannel::SelfChannel),
            (1.0, 1.0, 1.0, 1.0)
        );
        assert_eq!(
            fill_color_for_channel(CaptionChannel::PeerChannel),
            (1.0, 215.0 / 255.0, 0.0, 1.0)
        );
    }

    #[test]
    fn outline_offsets_px_match_the_vr_outline_profile() {
        assert_eq!(
            outline_offsets_px().to_vec(),
            vec![(-5.0, 0.0), (5.0, 0.0), (0.0, -5.0), (0.0, 5.0),]
        );
    }

    #[test]
    fn effective_background_alpha_is_zero_for_text_only_overlay() {
        let presentation = CaptionPresentation {
            background_alpha: 0.82,
            text_scale: 1.0,
        };

        assert_eq!(effective_background_alpha(true, &presentation), 0.0);
        assert_eq!(effective_background_alpha(false, &presentation), 0.0);
    }
}
