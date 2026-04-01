use std::cell::RefCell;
use std::ffi::c_void;
#[cfg(windows)]
use std::collections::HashMap;
#[cfg(windows)]
use std::mem::ManuallyDrop;

use thiserror::Error;

#[cfg(windows)]
use windows::{
    core::{Interface, PCWSTR},
    Win32::Foundation::HMODULE,
    Win32::Graphics::{
        Direct2D::{
            Common::{
                D2D_RECT_F, D2D1_ALPHA_MODE_PREMULTIPLIED, D2D1_COLOR_F, D2D1_PIXEL_FORMAT,
                D2D1_ROUNDED_RECT,
            },
            D2D1_BITMAP_OPTIONS_CANNOT_DRAW, D2D1_BITMAP_OPTIONS_TARGET,
            D2D1_BITMAP_PROPERTIES1, D2D1_DRAW_TEXT_OPTIONS_NONE,
            D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
            D2D1_FACTORY_TYPE_SINGLE_THREADED, D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE,
            D2D1CreateFactory, D2D1_DEVICE_CONTEXT_OPTIONS_NONE, ID2D1Bitmap1,
            ID2D1DeviceContext, ID2D1Factory1, ID2D1SolidColorBrush,
        },
        Direct3D::{
            D3D_DRIVER_TYPE_HARDWARE, D3D_DRIVER_TYPE_WARP, D3D_FEATURE_LEVEL_10_0,
            D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_11_1,
        },
        Direct3D11::{
            D3D11_BIND_RENDER_TARGET, D3D11_BIND_SHADER_RESOURCE,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_SDK_VERSION, D3D11_TEXTURE2D_DESC,
            D3D11_USAGE_DEFAULT, D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext,
            ID3D11Texture2D,
        },
        DirectWrite::{
            DWriteCreateFactory, DWRITE_FACTORY_TYPE_SHARED, DWRITE_FONT_STRETCH_NORMAL,
            DWRITE_TEXT_ALIGNMENT_CENTER,
            DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_WEIGHT, DWRITE_FONT_WEIGHT_MEDIUM,
            DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_WEIGHT_SEMI_BOLD, IDWriteFontCollection,
            IDWriteFontFamily, DWRITE_MEASURING_MODE_NATURAL, DWRITE_WORD_WRAPPING_NO_WRAP,
            IDWriteFactory, IDWriteTextFormat,
        },
        Dxgi::{
            Common::{DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC},
            IDXGIDevice, IDXGISurface,
        },
    },
};

const DEFAULT_SURFACE_WIDTH_PX: u32 = 3840;
const DEFAULT_SURFACE_HEIGHT_PX: u32 = 1024;
const DEFAULT_HORIZONTAL_PADDING_PX: u32 = 96;
const DEFAULT_VERTICAL_PADDING_PX: u32 = 64;
const DEFAULT_LINE_HEIGHT_PX: u32 = 180;
const DEFAULT_BLOCK_SPACING_PX: u32 = 44;
const DEFAULT_STRIP_HORIZONTAL_PADDING_PX: u32 = 72;
const DEFAULT_STRIP_VERTICAL_PADDING_PX: u32 = 44;
const DEFAULT_AVERAGE_GLYPH_ADVANCE_PX: u32 = 80;
#[cfg(windows)]
const DEFAULT_FONT_SIZE_PX: f32 = 140.0;

#[derive(Debug, Error)]
pub enum CaptionRenderError {
    #[error("renderer init failed: {0}")]
    Init(String),
    #[error("renderer draw failed: {0}")]
    Draw(String),
}

#[derive(Debug, Clone, PartialEq)]
pub struct CaptionBlock {
    pub id: String,
    pub text: String,
    pub channel: Option<CaptionChannel>,
    pub opacity: f32,
    pub offset_y_px: f32,
    pub height_scale: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaptionChannel {
    SelfChannel,
    PeerChannel,
}

impl CaptionBlock {
    pub fn new(id: impl Into<String>, text: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            text: text.into(),
            channel: None,
            opacity: 1.0,
            offset_y_px: 0.0,
            height_scale: 1.0,
        }
    }

    pub fn with_channel(mut self, channel: CaptionChannel) -> Self {
        self.channel = Some(channel);
        self
    }

    pub fn with_visual_state(
        mut self,
        opacity: f32,
        offset_y_px: f32,
        height_scale: f32,
    ) -> Self {
        self.opacity = opacity.clamp(0.0, 1.0);
        self.offset_y_px = offset_y_px;
        self.height_scale = height_scale.clamp(0.35, 1.0);
        self
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BlockBounds {
    pub left_px: f32,
    pub top_px: f32,
    pub right_px: f32,
    pub bottom_px: f32,
}

impl BlockBounds {
    pub fn new(left_px: f32, top_px: f32, right_px: f32, bottom_px: f32) -> Self {
        Self {
            left_px,
            top_px,
            right_px,
            bottom_px,
        }
    }

    pub fn center_x(&self) -> f32 {
        (self.left_px + self.right_px) * 0.5
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct CaptionLineLayout {
    pub text: String,
    pub width_px: f32,
    pub origin_x: f32,
    pub origin_y: f32,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DamageBand {
    pub top_px: f32,
    pub bottom_px: f32,
}

impl DamageBand {
    pub fn from_bounds<I>(bounds: I) -> Option<Self>
    where
        I: IntoIterator<Item = BlockBounds>,
    {
        let mut iter = bounds.into_iter();
        let first = iter.next()?;
        let mut top_px = first.top_px;
        let mut bottom_px = first.bottom_px;

        for bounds in iter {
            top_px = top_px.min(bounds.top_px);
            bottom_px = bottom_px.max(bounds.bottom_px);
        }

        Some(Self { top_px, bottom_px })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct VisibleCaptionBlock {
    pub id: String,
    pub channel: Option<CaptionChannel>,
    pub lines: Vec<String>,
    pub line_metrics: Vec<CaptionLineLayout>,
    pub bounds: BlockBounds,
    pub content_width_px: f32,
    pub opacity: f32,
    pub truncated: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CaptionLayoutResult {
    pub visible_blocks: Vec<VisibleCaptionBlock>,
    pub dropped_block_ids: Vec<String>,
    pub surface_width_px: u32,
    pub surface_height_px: u32,
    pub damage_band: Option<DamageBand>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CaptionPresentation {
    pub background_alpha: f32,
}

impl Default for CaptionPresentation {
    fn default() -> Self {
        Self {
            background_alpha: 0.24,
        }
    }
}

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
    line_height_px: u32,
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
            line_height_px: DEFAULT_LINE_HEIGHT_PX,
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

    pub fn compose_self_line(&self, original: &str, translation: &str) -> String {
        compose_caption_pair(original, translation)
    }

    pub fn compose_peer_line(&self, original: &str, translation: &str) -> String {
        compose_caption_pair(translation, original)
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

    pub fn layout_blocks(
        &self,
        blocks: Vec<CaptionBlock>,
        surface_width_px: u32,
        surface_height_px: u32,
    ) -> CaptionLayoutResult {
        let total_blocks = blocks.len();
        let visible_window_start = total_blocks.saturating_sub(self.visible_window_target_blocks);
        let available_height_px = surface_height_px
            .saturating_sub(self.vertical_padding_px.saturating_mul(2))
            .max(self.line_height_px);
        let content_width_px = self.content_width_px(surface_width_px);

        let mut selected_newest_first: Vec<(CaptionBlock, Vec<String>, bool)> = Vec::new();
        let mut dropped_block_ids = Vec::with_capacity(visible_window_start);
        let mut used_height_px = 0;

        for (index, block) in blocks.into_iter().enumerate().rev() {
            if index < visible_window_start {
                dropped_block_ids.push(block.id);
                continue;
            }

            let wrapped_lines = wrap_text(
                &block.text,
                content_width_px,
                self.average_glyph_advance_px as f32,
            );
            let block_height_px = self.block_height_px(wrapped_lines.len());
            let spacing_px = if selected_newest_first.is_empty() {
                0
            } else {
                self.block_spacing_px
            };

            if used_height_px + spacing_px + block_height_px <= available_height_px {
                used_height_px += spacing_px + block_height_px;
                selected_newest_first.push((block, wrapped_lines, false));
                continue;
            }

            if !selected_newest_first.is_empty() {
                dropped_block_ids.push(block.id);
                continue;
            }

            let max_lines = self.max_visible_lines(available_height_px);
            let truncated = wrapped_lines.len() > max_lines;
            selected_newest_first.push((
                block,
                wrapped_lines.into_iter().take(max_lines).collect(),
                truncated,
            ));
            used_height_px = available_height_px;
        }

        selected_newest_first.reverse();
        let strip_left_px = self.horizontal_padding_px as f32;
        let strip_right_px = (surface_width_px - self.horizontal_padding_px) as f32;
        let content_left_px = strip_left_px + self.strip_horizontal_padding_px as f32;
        let content_width_px = content_width_px.max(1.0);
        let mut top_px = self.vertical_padding_px as f32;
        let mut visible_blocks = Vec::with_capacity(selected_newest_first.len());

        for (block, lines, truncated) in selected_newest_first {
            let line_count = lines.len();
            let line_height_px = self.line_height_px as f32 * block.height_scale;
            let vertical_padding_px = self.strip_vertical_padding_px as f32 * block.height_scale;
            let block_height_px =
                (vertical_padding_px * 2.0 + line_count.max(1) as f32 * line_height_px)
                    .max(self.line_height_px as f32);
            let bounds = BlockBounds::new(
                strip_left_px,
                top_px + block.offset_y_px,
                strip_right_px,
                top_px + block.offset_y_px + block_height_px,
            );
            let line_metrics = lines
                .iter()
                .enumerate()
                .map(|(index, line)| {
                    let width_px = measure_text_width(line, self.average_glyph_advance_px as f32);
                    let origin_x = content_left_px + ((content_width_px - width_px).max(0.0) * 0.5);
                    let origin_y =
                        bounds.top_px + vertical_padding_px + index as f32 * line_height_px;
                    CaptionLineLayout {
                        text: line.clone(),
                        width_px,
                        origin_x,
                        origin_y,
                    }
                })
                .collect();

            visible_blocks.push(VisibleCaptionBlock {
                id: block.id,
                channel: block.channel,
                lines,
                line_metrics,
                bounds,
                content_width_px,
                opacity: block.opacity,
                truncated,
            });
            top_px += self.block_height_px(line_count) as f32;
            top_px += self.block_spacing_px as f32;
        }

        CaptionLayoutResult {
            visible_blocks,
            dropped_block_ids,
            surface_width_px,
            surface_height_px,
            damage_band: None,
        }
    }

    fn content_width_px(&self, surface_width_px: u32) -> f32 {
        surface_width_px
            .saturating_sub(self.horizontal_padding_px.saturating_mul(2))
            .saturating_sub(self.strip_horizontal_padding_px.saturating_mul(2))
            .max(self.average_glyph_advance_px) as f32
    }

    fn max_visible_lines(&self, available_height_px: u32) -> usize {
        available_height_px
            .saturating_sub(self.strip_vertical_padding_px.saturating_mul(2))
            .max(self.line_height_px)
            .div_ceil(self.line_height_px) as usize
    }

    fn block_height_px(&self, line_count: usize) -> u32 {
        self.strip_vertical_padding_px.saturating_mul(2)
            + (line_count.max(1) as u32).saturating_mul(self.line_height_px)
    }
}

fn compose_caption_pair(primary: &str, secondary: &str) -> String {
    if primary.is_empty() {
        return secondary.to_string();
    }
    if secondary.is_empty() {
        return primary.to_string();
    }
    format!("{primary} ({secondary})")
}

pub struct CaptionRenderer {
    policy: CaptionLayoutPolicy,
    presentation: RefCell<CaptionPresentation>,
    backend: RefCell<RenderBackend>,
}

impl CaptionRenderer {
    pub fn new() -> Result<Self, CaptionRenderError> {
        Self::with_policy(CaptionLayoutPolicy::default(), BackendMode::Runtime)
    }

    pub fn new_for_test() -> Result<Self, CaptionRenderError> {
        Self::with_policy(CaptionLayoutPolicy::default(), BackendMode::Test)
    }

    pub fn render_empty_frame(&self) -> Result<RenderedFrame, CaptionRenderError> {
        self.render_blocks(Vec::new())
    }

    pub fn render_blocks(&self, blocks: Vec<CaptionBlock>) -> Result<RenderedFrame, CaptionRenderError> {
        let (width, height) = self.policy.default_surface_size();
        let layout = self.policy.layout_blocks(blocks, width, height);
        let presentation = self.presentation.borrow().clone();
        self.backend
            .borrow_mut()
            .render(&self.policy, &presentation, layout)
    }

    fn with_policy(
        policy: CaptionLayoutPolicy,
        backend_mode: BackendMode,
    ) -> Result<Self, CaptionRenderError> {
        Ok(Self {
            policy,
            presentation: RefCell::new(CaptionPresentation::default()),
            backend: RefCell::new(match backend_mode {
                BackendMode::Runtime => RenderBackend::new_runtime()?,
                BackendMode::Test => RenderBackend::new_test()?,
            }),
        })
    }

    pub fn set_presentation(&self, presentation: CaptionPresentation) {
        self.presentation.replace(presentation);
    }
}

enum BackendMode {
    Runtime,
    Test,
}

#[derive(Debug)]
pub struct RenderedFrame {
    width: u32,
    height: u32,
    fully_transparent: bool,
    layout: CaptionLayoutResult,
    texture: TextureHandle,
}

impl RenderedFrame {
    pub fn width(&self) -> u32 {
        self.width
    }

    pub fn height(&self) -> u32 {
        self.height
    }

    pub fn is_fully_transparent(&self) -> bool {
        self.fully_transparent
    }

    pub fn texture_ptr(&self) -> Option<*mut c_void> {
        Some(self.texture.as_ptr())
    }

    pub fn layout(&self) -> &CaptionLayoutResult {
        &self.layout
    }

    #[cfg(windows)]
    pub fn d3d11_texture(&self) -> Option<&ID3D11Texture2D> {
        self.texture.d3d11_texture()
    }
}

#[derive(Debug)]
enum TextureHandle {
    #[cfg(windows)]
    D3D11(ID3D11Texture2D),
    #[cfg(not(windows))]
    Test(TestTextureHandle),
}

impl TextureHandle {
    fn as_ptr(&self) -> *mut c_void {
        match self {
            #[cfg(windows)]
            Self::D3D11(texture) => texture.as_raw(),
            #[cfg(not(windows))]
            Self::Test(texture) => texture.as_ptr(),
        }
    }

    #[cfg(windows)]
    fn d3d11_texture(&self) -> Option<&ID3D11Texture2D> {
        match self {
            Self::D3D11(texture) => Some(texture),
        }
    }
}

#[cfg(not(windows))]
#[derive(Debug)]
struct TestTextureHandle {
    marker: Box<u8>,
}

#[cfg(not(windows))]
impl TestTextureHandle {
    fn new() -> Self {
        Self {
            marker: Box::new(1),
        }
    }

    fn as_ptr(&self) -> *mut c_void {
        (&*self.marker as *const u8 as *mut u8).cast()
    }
}

enum RenderBackend {
    #[cfg(windows)]
    Windows(WindowsCaptionRenderer),
    #[cfg(not(windows))]
    Test(TestCaptionRenderer),
}

impl RenderBackend {
    fn new_runtime() -> Result<Self, CaptionRenderError> {
        #[cfg(windows)]
        {
            return WindowsCaptionRenderer::new().map(Self::Windows);
        }

        #[cfg(not(windows))]
        {
            Err(CaptionRenderError::Init(
                "the Direct3D11 caption renderer is only available on Windows".into(),
            ))
        }
    }

    fn new_test() -> Result<Self, CaptionRenderError> {
        #[cfg(windows)]
        {
            return WindowsCaptionRenderer::new().map(Self::Windows);
        }

        #[cfg(not(windows))]
        {
            Ok(Self::Test(TestCaptionRenderer::default()))
        }
    }

    fn render(
        &mut self,
        policy: &CaptionLayoutPolicy,
        presentation: &CaptionPresentation,
        layout: CaptionLayoutResult,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        match self {
            #[cfg(windows)]
            Self::Windows(renderer) => renderer.render(policy, presentation, layout),
            #[cfg(not(windows))]
            Self::Test(renderer) => {
                let _ = (policy, presentation);
                renderer.render(layout)
            }
        }
    }
}

#[cfg(not(windows))]
#[derive(Default)]
struct TestCaptionRenderer {
    previous_layout: Option<CaptionLayoutResult>,
}

#[cfg(not(windows))]
impl TestCaptionRenderer {
    fn render(&mut self, layout: CaptionLayoutResult) -> Result<RenderedFrame, CaptionRenderError> {
        let layout = prepare_layout_for_render(&mut self.previous_layout, layout);
        let fully_transparent = !layout_has_drawable_text(&layout);

        Ok(RenderedFrame {
            width: layout.surface_width_px,
            height: layout.surface_height_px,
            fully_transparent,
            layout,
            texture: TextureHandle::Test(TestTextureHandle::new()),
        })
    }
}

#[cfg(windows)]
struct WindowsCaptionRenderer {
    dwrite_factory: IDWriteFactory,
    system_font_collection: IDWriteFontCollection,
    d2d_context: ID2D1DeviceContext,
    self_strip_brush: ID2D1SolidColorBrush,
    peer_strip_brush: ID2D1SolidColorBrush,
    self_text_brush: ID2D1SolidColorBrush,
    peer_text_brush: ID2D1SolidColorBrush,
    target_bitmap: ID2D1Bitmap1,
    texture: ID3D11Texture2D,
    text_format_cache: HashMap<TextScriptBucket, IDWriteTextFormat>,
    previous_layout: Option<CaptionLayoutResult>,
    _d3d_device: ID3D11Device,
    d3d_context: ID3D11DeviceContext,
}

#[cfg_attr(not(windows), allow(dead_code))]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum TextScriptBucket {
    Latin,
    Cjk,
}

#[cfg(windows)]
impl WindowsCaptionRenderer {
    fn new() -> Result<Self, CaptionRenderError> {
        let (device, context) = create_d3d_device()?;
        let dwrite_factory = create_dwrite_factory()?;
        let system_font_collection = get_system_font_collection(&dwrite_factory)?;
        let texture = create_target_texture(&device)?;
        let d2d_context = create_d2d_context(&device)?;
        let dxgi_surface: IDXGISurface = texture
            .cast()
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
        let bitmap_properties = D2D1_BITMAP_PROPERTIES1 {
            pixelFormat: D2D1_PIXEL_FORMAT {
                format: DXGI_FORMAT_B8G8R8A8_UNORM,
                alphaMode: D2D1_ALPHA_MODE_PREMULTIPLIED,
            },
            dpiX: 96.0,
            dpiY: 96.0,
            bitmapOptions: D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
            colorContext: ManuallyDrop::new(None),
        };
        let target_bitmap = unsafe {
            d2d_context
                .CreateBitmapFromDxgiSurface(&dxgi_surface, Some(&bitmap_properties))
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        unsafe {
            d2d_context.SetTarget(&target_bitmap);
            d2d_context.SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);
        }
        let self_strip_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(
                    &D2D1_COLOR_F {
                        r: 0.16,
                        g: 0.16,
                        b: 0.18,
                        a: 0.82,
                    },
                    None,
                )
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let peer_strip_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(
                    &D2D1_COLOR_F {
                        r: 0.12,
                        g: 0.18,
                        b: 0.21,
                        a: 0.82,
                    },
                    None,
                )
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let self_text_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(
                    &D2D1_COLOR_F {
                        r: 1.0,
                        g: 0.95,
                        b: 0.88,
                        a: 0.95,
                    },
                    None,
                )
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let peer_text_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(
                    &D2D1_COLOR_F {
                        r: 0.79,
                        g: 0.91,
                        b: 1.0,
                        a: 0.95,
                    },
                    None,
                )
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        Ok(Self {
            dwrite_factory,
            system_font_collection,
            d2d_context,
            self_strip_brush,
            peer_strip_brush,
            self_text_brush,
            peer_text_brush,
            target_bitmap,
            texture,
            text_format_cache: HashMap::new(),
            previous_layout: None,
            _d3d_device: device,
            d3d_context: context,
        })
    }

    fn render(
        &mut self,
        policy: &CaptionLayoutPolicy,
        presentation: &CaptionPresentation,
        layout: CaptionLayoutResult,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        let layout = prepare_layout_for_render(&mut self.previous_layout, layout);
        let clear_alpha = if layout_has_drawable_text(&layout) {
            presentation.background_alpha
        } else {
            0.0
        };
        let damage_band = layout.damage_band.unwrap_or(DamageBand {
            top_px: 0.0,
            bottom_px: layout.surface_height_px as f32,
        });
        unsafe {
            self.d2d_context.SetTarget(&self.target_bitmap);
            self.d2d_context.BeginDraw();
            self.d2d_context.PushAxisAlignedClip(
                &D2D_RECT_F {
                    left: 0.0,
                    top: damage_band.top_px,
                    right: layout.surface_width_px as f32,
                    bottom: damage_band.bottom_px,
                },
                D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
            );
            self.d2d_context.Clear(Some(&D2D1_COLOR_F {
                r: 0.0,
                g: 0.0,
                b: 0.0,
                a: clear_alpha,
            }));
            self.d2d_context.PopAxisAlignedClip();
        }

        let line_height = policy.line_height_px as f32;

        for block in &layout.visible_blocks {
            if !bounds_intersect_damage_band(block.bounds, damage_band) {
                continue;
            }
            let block_text = block.lines.join(" ");
            let text_format = self.create_text_format(policy, &block_text)?;
            let background_brush = match block.channel.unwrap_or(CaptionChannel::SelfChannel) {
                CaptionChannel::SelfChannel => &self.self_strip_brush,
                CaptionChannel::PeerChannel => &self.peer_strip_brush,
            };
            let text_brush = match block.channel.unwrap_or(CaptionChannel::SelfChannel) {
                CaptionChannel::SelfChannel => &self.self_text_brush,
                CaptionChannel::PeerChannel => &self.peer_text_brush,
            };
            let rounded_rect = D2D1_ROUNDED_RECT {
                rect: D2D_RECT_F {
                    left: block.bounds.left_px,
                    top: block.bounds.top_px,
                    right: block.bounds.right_px,
                    bottom: block.bounds.bottom_px,
                },
                radiusX: 28.0,
                radiusY: 28.0,
            };

            unsafe {
                background_brush.SetOpacity((block.opacity * 0.92).clamp(0.0, 1.0));
                text_brush.SetOpacity(block.opacity.clamp(0.0, 1.0));
                self.d2d_context
                    .FillRoundedRectangle(&rounded_rect, background_brush);
            }

            for line in &block.line_metrics {
                let trimmed = line.text.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let utf16: Vec<u16> = trimmed.encode_utf16().collect();
                let rect = D2D_RECT_F {
                    left: block.bounds.left_px + policy.strip_horizontal_padding_px as f32,
                    top: line.origin_y,
                    right: block.bounds.right_px - policy.strip_horizontal_padding_px as f32,
                    bottom: line.origin_y + line_height,
                };
                unsafe {
                    self.d2d_context.DrawText(
                        &utf16,
                        &text_format,
                        &rect,
                        text_brush,
                        D2D1_DRAW_TEXT_OPTIONS_NONE,
                        DWRITE_MEASURING_MODE_NATURAL,
                    );
                }
            }
        }

        unsafe {
            self.d2d_context
                .EndDraw(None, None)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
        }

        Ok(RenderedFrame {
            width: layout.surface_width_px,
            height: layout.surface_height_px,
            fully_transparent: !layout_has_drawable_text(&layout),
            layout,
            texture: TextureHandle::D3D11(self.texture.clone()),
        })
    }

    fn create_text_format(
        &mut self,
        policy: &CaptionLayoutPolicy,
        text: &str,
    ) -> Result<IDWriteTextFormat, CaptionRenderError> {
        let bucket = text_script_bucket(text);
        if let Some(text_format) = self.text_format_cache.get(&bucket) {
            return Ok(text_format.clone());
        }

        let resolved_style = self.resolve_text_style(policy, bucket)?;
        let locale = utf16_null("en-us");
        let face_name = utf16_null(&resolved_style.family_name);
        let text_format = unsafe {
            self.dwrite_factory
                .CreateTextFormat(
                    PCWSTR::from_raw(face_name.as_ptr()),
                    None,
                    resolved_style.weight,
                    DWRITE_FONT_STYLE_NORMAL,
                    DWRITE_FONT_STRETCH_NORMAL,
                    DEFAULT_FONT_SIZE_PX,
                    PCWSTR::from_raw(locale.as_ptr()),
                )
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?
        };
        unsafe {
            text_format
                .SetWordWrapping(DWRITE_WORD_WRAPPING_NO_WRAP)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            text_format
                .SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
        }
        self.text_format_cache.insert(bucket, text_format.clone());
        Ok(text_format)
    }

    fn resolve_text_style(
        &self,
        policy: &CaptionLayoutPolicy,
        bucket: TextScriptBucket,
    ) -> Result<ResolvedTextStyle, CaptionRenderError> {
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
            return Ok(ResolvedTextStyle {
                family_name: family_name.to_string(),
                weight,
            });
        }

        Err(CaptionRenderError::Draw(
            "no compatible DirectWrite font face was available".into(),
        ))
    }

    fn find_font_family(
        &self,
        family_name: &str,
    ) -> Result<Option<IDWriteFontFamily>, CaptionRenderError> {
        let family_name = utf16_null(family_name);
        let mut index = 0;
        let mut exists = false.into();
        unsafe {
            self.system_font_collection
                .FindFamilyName(PCWSTR::from_raw(family_name.as_ptr()), &mut index, &mut exists)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            if !exists.as_bool() {
                return Ok(None);
            }
            self.system_font_collection
                .GetFontFamily(index)
                .map(Some)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))
        }
    }
}

#[cfg(windows)]
fn create_dwrite_factory() -> Result<IDWriteFactory, CaptionRenderError> {
    unsafe {
        DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))
    }
}

#[cfg(windows)]
fn get_system_font_collection(
    factory: &IDWriteFactory,
) -> Result<IDWriteFontCollection, CaptionRenderError> {
    let mut collection = None;
    unsafe {
        factory
            .GetSystemFontCollection(&mut collection, false)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
    }
    collection.ok_or_else(|| CaptionRenderError::Init("system font collection missing".into()))
}

#[cfg(windows)]
fn create_d3d_device() -> Result<(ID3D11Device, ID3D11DeviceContext), CaptionRenderError> {
    let feature_levels = [
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    ];

    create_d3d_device_for_driver(D3D_DRIVER_TYPE_HARDWARE, &feature_levels)
        .or_else(|_| create_d3d_device_for_driver(D3D_DRIVER_TYPE_WARP, &feature_levels))
}

#[cfg(windows)]
fn create_d3d_device_for_driver(
    driver_type: windows::Win32::Graphics::Direct3D::D3D_DRIVER_TYPE,
    feature_levels: &[windows::Win32::Graphics::Direct3D::D3D_FEATURE_LEVEL],
) -> Result<(ID3D11Device, ID3D11DeviceContext), CaptionRenderError> {
    let mut device = None;
    let mut context = None;

    unsafe {
        D3D11CreateDevice(
            None,
            driver_type,
            HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            Some(feature_levels),
            D3D11_SDK_VERSION,
            Some(&mut device),
            None,
            Some(&mut context),
        )
        .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
    }

    let device = device.ok_or_else(|| CaptionRenderError::Init("d3d device missing".into()))?;
    let context =
        context.ok_or_else(|| CaptionRenderError::Init("d3d device context missing".into()))?;
    Ok((device, context))
}

#[cfg(windows)]
fn create_target_texture(device: &ID3D11Device) -> Result<ID3D11Texture2D, CaptionRenderError> {
    let mut texture = None;
    let description = D3D11_TEXTURE2D_DESC {
        Width: DEFAULT_SURFACE_WIDTH_PX,
        Height: DEFAULT_SURFACE_HEIGHT_PX,
        MipLevels: 1,
        ArraySize: 1,
        Format: DXGI_FORMAT_B8G8R8A8_UNORM,
        SampleDesc: DXGI_SAMPLE_DESC {
            Count: 1,
            Quality: 0,
        },
        Usage: D3D11_USAGE_DEFAULT,
        BindFlags: (D3D11_BIND_RENDER_TARGET.0 | D3D11_BIND_SHADER_RESOURCE.0) as u32,
        CPUAccessFlags: 0,
        MiscFlags: 0,
    };

    unsafe {
        device
            .CreateTexture2D(&description, None, Some(&mut texture))
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
    }

    texture.ok_or_else(|| CaptionRenderError::Init("renderer texture missing".into()))
}

#[cfg(windows)]
fn create_d2d_context(device: &ID3D11Device) -> Result<ID2D1DeviceContext, CaptionRenderError> {
    let factory: ID2D1Factory1 = unsafe {
        D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, None)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?
    };
    let dxgi_device: IDXGIDevice = device
        .cast()
        .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
    let d2d_device = unsafe {
        factory
            .CreateDevice(&dxgi_device)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?
    };
    let d2d_context = unsafe {
        d2d_device
            .CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?
    };
    Ok(d2d_context)
}

fn layout_has_drawable_text(layout: &CaptionLayoutResult) -> bool {
    layout
        .visible_blocks
        .iter()
        .flat_map(|block| &block.lines)
        .any(|line| !line.trim().is_empty())
}

fn prepare_layout_for_render(
    previous_layout: &mut Option<CaptionLayoutResult>,
    mut layout: CaptionLayoutResult,
) -> CaptionLayoutResult {
    layout.damage_band = compute_damage_band(previous_layout.as_ref(), &layout);
    *previous_layout = Some(layout.clone());
    layout
}

fn compute_damage_band(
    previous_layout: Option<&CaptionLayoutResult>,
    next_layout: &CaptionLayoutResult,
) -> Option<DamageBand> {
    let Some(previous_layout) = previous_layout else {
        return Some(DamageBand {
            top_px: 0.0,
            bottom_px: next_layout.surface_height_px as f32,
        });
    };

    if previous_layout.surface_width_px != next_layout.surface_width_px
        || previous_layout.surface_height_px != next_layout.surface_height_px
    {
        return Some(DamageBand {
            top_px: 0.0,
            bottom_px: next_layout.surface_height_px as f32,
        });
    }

    let previous_bounds = previous_layout
        .visible_blocks
        .iter()
        .map(|block| (block.id.as_str(), block.bounds))
        .collect::<std::collections::HashMap<_, _>>();
    let next_bounds = next_layout
        .visible_blocks
        .iter()
        .map(|block| (block.id.as_str(), block.bounds))
        .collect::<std::collections::HashMap<_, _>>();

    let mut changed_bounds = Vec::new();
    for (id, bounds) in &previous_bounds {
        match next_bounds.get(id) {
            Some(next_bounds) if next_bounds == bounds => {}
            Some(next_bounds) => {
                changed_bounds.push(*bounds);
                changed_bounds.push(*next_bounds);
            }
            None => changed_bounds.push(*bounds),
        }
    }
    for (id, bounds) in &next_bounds {
        if !previous_bounds.contains_key(id) {
            changed_bounds.push(*bounds);
        }
    }

    DamageBand::from_bounds(changed_bounds)
}

#[cfg_attr(not(windows), allow(dead_code))]
fn bounds_intersect_damage_band(bounds: BlockBounds, damage_band: DamageBand) -> bool {
    bounds.bottom_px >= damage_band.top_px && bounds.top_px <= damage_band.bottom_px
}

#[cfg(windows)]
fn select_face_chain<'a>(
    policy: &'a CaptionLayoutPolicy,
    bucket: TextScriptBucket,
) -> &'a [&'static str] {
    if matches!(bucket, TextScriptBucket::Cjk) {
        policy.cjk_face_chain()
    } else {
        policy.latin_face_chain()
    }
}

#[cfg(windows)]
struct ResolvedTextStyle {
    family_name: String,
    weight: DWRITE_FONT_WEIGHT,
}

#[cfg(windows)]
fn resolve_family_weight(
    family: &IDWriteFontFamily,
    policy: &CaptionLayoutPolicy,
) -> Result<Option<DWRITE_FONT_WEIGHT>, CaptionRenderError> {
    for weight in preferred_weight_chain(policy) {
        let font = unsafe {
            family
                .GetFirstMatchingFont(weight, DWRITE_FONT_STRETCH_NORMAL, DWRITE_FONT_STYLE_NORMAL)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?
        };
        if unsafe { font.GetWeight() } == weight {
            return Ok(Some(weight));
        }
    }
    Ok(None)
}

#[cfg(windows)]
fn preferred_weight_chain(policy: &CaptionLayoutPolicy) -> Vec<DWRITE_FONT_WEIGHT> {
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

#[cfg_attr(not(windows), allow(dead_code))]
fn contains_cjk(text: &str) -> bool {
    text.chars().any(|ch| {
        matches!(
            ch as u32,
            0x3040..=0x30ff
                | 0x3400..=0x4dbf
                | 0x4e00..=0x9fff
                | 0xac00..=0xd7af
                | 0xf900..=0xfaff
        )
    })
}

#[cfg_attr(not(windows), allow(dead_code))]
fn text_script_bucket(text: &str) -> TextScriptBucket {
    if contains_cjk(text) {
        TextScriptBucket::Cjk
    } else {
        TextScriptBucket::Latin
    }
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
            '.' | ',' | ':' | ';' | '\'' | '"' | '!' | '?' | '(' | ')' | '[' | ']' | '{'
            | '}' | '-' | '_' | '/' => average_glyph_advance_px * 0.55,
            _ if contains_cjk(&ch.to_string()) => average_glyph_advance_px,
            _ => average_glyph_advance_px * 0.72,
        })
        .sum()
}

#[cfg(test)]
mod tests {
    use super::{measure_text_width, text_script_bucket, wrap_text, TextScriptBucket};

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
}
