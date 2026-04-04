use std::cell::RefCell;
use std::ffi::c_void;
#[cfg(windows)]
use std::mem::ManuallyDrop;

#[cfg(windows)]
use windows::core::{Interface, PCWSTR};
#[cfg(windows)]
use windows::Win32::Foundation::HMODULE;
#[cfg(windows)]
use windows::Win32::Graphics::Direct2D::{
    Common::{
        D2D1_ALPHA_MODE_PREMULTIPLIED, D2D1_COLOR_F, D2D1_COMPOSITE_MODE_SOURCE_OVER,
        D2D1_PIXEL_FORMAT, D2D_RECT_F,
    },
    D2D1CreateFactory, ID2D1Bitmap1, ID2D1DeviceContext, ID2D1Factory1, ID2D1Layer,
    ID2D1SolidColorBrush, D2D1_ANTIALIAS_MODE_PER_PRIMITIVE, D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
    D2D1_BITMAP_OPTIONS_TARGET, D2D1_BITMAP_PROPERTIES1, D2D1_DEVICE_CONTEXT_OPTIONS_NONE,
    D2D1_FACTORY_TYPE_SINGLE_THREADED, D2D1_INTERPOLATION_MODE_LINEAR, D2D1_LAYER_OPTIONS1_NONE,
    D2D1_LAYER_PARAMETERS1, D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE,
};
#[cfg(windows)]
use windows::Win32::Graphics::Direct3D::{
    D3D_DRIVER_TYPE_HARDWARE, D3D_DRIVER_TYPE_WARP, D3D_FEATURE_LEVEL_10_0, D3D_FEATURE_LEVEL_10_1,
    D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_11_1,
};
#[cfg(windows)]
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D,
    D3D11_BIND_RENDER_TARGET, D3D11_BIND_SHADER_RESOURCE, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
    D3D11_SDK_VERSION, D3D11_TEXTURE2D_DESC, D3D11_USAGE_DEFAULT,
};
#[cfg(windows)]
use windows::Win32::Graphics::DirectWrite::{
    DWriteCreateFactory, IDWriteFactory, IDWriteFactory2, IDWriteFontCollection,
    IDWriteFontFallback, IDWriteFontFamily, IDWriteTextFormat, IDWriteTextFormat1,
    IDWriteTextLayout, IDWriteTextLayout2, DWRITE_FACTORY_TYPE_SHARED, DWRITE_FONT_STRETCH_NORMAL,
    DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_WEIGHT, DWRITE_FONT_WEIGHT_MEDIUM,
    DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_WEIGHT_SEMI_BOLD, DWRITE_TEXT_ALIGNMENT_CENTER,
    DWRITE_WORD_WRAPPING_NO_WRAP,
};
#[cfg(windows)]
use windows::Win32::Graphics::Dxgi::{
    Common::{DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC},
    IDXGIDevice, IDXGISurface,
};
#[cfg(windows)]
use windows_numerics::{Matrix3x2, Vector2};

#[cfg(windows)]
use super::cache::{CachedBlockVisual, CachedLineVisual, WindowsRendererCaches};
#[cfg(windows)]
use super::glyph_run::render_text_layout_to_command_list;
use super::layout::{resolved_layout_has_drawable_text, CaptionLayoutPolicy};
#[cfg(windows)]
use super::types::{
    effective_background_alpha, fill_color_for_channel, outline_offsets_px, text_script_bucket,
    BlockCacheKey, CaptionBlockVariant, CaptionChannel, LineCacheKey, LineRole,
    ResolvedBlockLayout, ResolvedLineLayout, ResolvedTextStyle, TextScriptBucket,
    DEFAULT_SURFACE_HEIGHT_PX, DEFAULT_SURFACE_WIDTH_PX, TEXT_OUTLINE_COLOR,
};
use super::types::{
    BlockBounds, CaptionLayoutResult, CaptionPresentation, CaptionRenderError, DamageBand,
    RenderDiagnostics, ResolvedFrameLayout,
};

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

    pub fn render_blocks(
        &self,
        blocks: Vec<super::types::CaptionBlock>,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        let (width, height) = self.policy.default_surface_size();
        let presentation = self.presentation.borrow().clone();
        self.backend
            .borrow_mut()
            .render(&self.policy, &presentation, blocks, width, height)
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
    diagnostics: RenderDiagnostics,
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

    pub fn diagnostics(&self) -> &RenderDiagnostics {
        &self.diagnostics
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
        blocks: Vec<super::types::CaptionBlock>,
        width: u32,
        height: u32,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        match self {
            #[cfg(windows)]
            Self::Windows(renderer) => renderer.render(policy, presentation, blocks, width, height),
            #[cfg(not(windows))]
            Self::Test(renderer) => {
                let _ = presentation;
                let layout =
                    policy.resolve_blocks_for_presentation(blocks, width, height, presentation);
                renderer.render(layout)
            }
        }
    }
}

#[cfg(not(windows))]
#[derive(Default)]
struct TestCaptionRenderer {
    previous_layout: Option<ResolvedFrameLayout>,
}

#[cfg(not(windows))]
impl TestCaptionRenderer {
    fn render(&mut self, layout: ResolvedFrameLayout) -> Result<RenderedFrame, CaptionRenderError> {
        let layout = prepare_layout_for_render(&mut self.previous_layout, layout);
        let fully_transparent = !resolved_layout_has_drawable_text(&layout);
        let public_layout: CaptionLayoutResult = layout.clone().into();

        Ok(RenderedFrame {
            width: public_layout.surface_width_px,
            height: public_layout.surface_height_px,
            fully_transparent,
            layout: public_layout,
            diagnostics: RenderDiagnostics::default(),
            texture: TextureHandle::Test(TestTextureHandle::new()),
        })
    }
}

#[cfg(windows)]
struct WindowsCaptionRenderer {
    d2d_factory: ID2D1Factory1,
    dwrite_factory: IDWriteFactory,
    system_font_collection: IDWriteFontCollection,
    system_font_fallback: IDWriteFontFallback,
    d2d_context: ID2D1DeviceContext,
    cache_outline_brush: ID2D1SolidColorBrush,
    cache_self_text_brush: ID2D1SolidColorBrush,
    cache_peer_text_brush: ID2D1SolidColorBrush,
    target_bitmap: ID2D1Bitmap1,
    texture: ID3D11Texture2D,
    caches: WindowsRendererCaches,
    previous_layout: Option<ResolvedFrameLayout>,
    _d3d_device: ID3D11Device,
}

#[cfg(windows)]
impl WindowsCaptionRenderer {
    fn new() -> Result<Self, CaptionRenderError> {
        let (device, _context) = create_d3d_device()?;
        let d2d_factory = create_d2d_factory()?;
        let dwrite_factory = create_dwrite_factory()?;
        let factory2: IDWriteFactory2 = dwrite_factory
            .cast()
            .map_err(|error| CaptionRenderError::Init(error.to_string()))?;
        let system_font_collection = get_system_font_collection(&dwrite_factory)?;
        let system_font_fallback = unsafe {
            factory2
                .GetSystemFontFallback()
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let texture = create_target_texture(&device)?;
        let d2d_context = create_d2d_context(&device, &d2d_factory)?;
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
        let cache_outline_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(&d2d_color(TEXT_OUTLINE_COLOR), None)
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let cache_self_text_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(
                    &d2d_color(fill_color_for_channel(CaptionChannel::SelfChannel)),
                    None,
                )
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        let cache_peer_text_brush = unsafe {
            d2d_context
                .CreateSolidColorBrush(
                    &d2d_color(fill_color_for_channel(CaptionChannel::PeerChannel)),
                    None,
                )
                .map_err(|error| CaptionRenderError::Init(error.to_string()))?
        };
        Ok(Self {
            d2d_factory,
            dwrite_factory,
            system_font_collection,
            system_font_fallback,
            d2d_context,
            cache_outline_brush,
            cache_self_text_brush,
            cache_peer_text_brush,
            target_bitmap,
            texture,
            caches: WindowsRendererCaches::default(),
            previous_layout: None,
            _d3d_device: device,
        })
    }

    fn cache_brush_for_channel(&self, channel: CaptionChannel) -> ID2D1SolidColorBrush {
        match channel {
            CaptionChannel::SelfChannel => self.cache_self_text_brush.clone(),
            CaptionChannel::PeerChannel => self.cache_peer_text_brush.clone(),
        }
    }

    fn line_cache_key(
        &self,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
    ) -> LineCacheKey {
        LineCacheKey {
            text: line.text.clone(),
            role,
            channel: block.channel,
            block_variant: block.block_variant,
            font_size_key: (line.font_size_px * 100.0).round() as u32,
            content_width_key: block.content_width_px.round() as u32,
            text_scale_key: block.layout_cache_key.text_scale_key,
        }
    }

    fn block_cache_key(&self, block: &ResolvedBlockLayout) -> BlockCacheKey {
        block.block_cache_key()
    }

    fn cacheable_block(&self, block: &ResolvedBlockLayout) -> bool {
        block.block_variant == CaptionBlockVariant::Finalized
    }

    fn draw_cached_command_list_with_state(
        &self,
        command_list: &windows::Win32::Graphics::Direct2D::ID2D1CommandList,
        origin_x: f32,
        origin_y: f32,
        opacity: f32,
        render_height_scale: f32,
    ) -> Result<(), CaptionRenderError> {
        let mut previous_transform = Matrix3x2::default();
        unsafe {
            self.d2d_context.GetTransform(&mut previous_transform);
        }
        let transform = Matrix3x2 {
            M11: 1.0,
            M12: 0.0,
            M21: 0.0,
            M22: render_height_scale,
            M31: origin_x,
            M32: origin_y,
        };
        let mut opacity_layer: Option<ID2D1Layer> = None;

        unsafe {
            self.d2d_context.SetTransform(&transform);
        }
        let draw_result = (|| {
            unsafe {
                if opacity < 1.0 - f32::EPSILON {
                    let layer = self
                        .d2d_context
                        .CreateLayer(None)
                        .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
                    let layer_parameters = D2D1_LAYER_PARAMETERS1 {
                        contentBounds: D2D_RECT_F {
                            left: 0.0,
                            top: 0.0,
                            right: DEFAULT_SURFACE_WIDTH_PX as f32,
                            bottom: DEFAULT_SURFACE_HEIGHT_PX as f32,
                        },
                        geometricMask: ManuallyDrop::new(None),
                        maskAntialiasMode: D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
                        maskTransform: identity_matrix(),
                        opacity,
                        opacityBrush: ManuallyDrop::new(None),
                        layerOptions: D2D1_LAYER_OPTIONS1_NONE,
                    };
                    self.d2d_context.PushLayer(&layer_parameters, &layer);
                    opacity_layer = Some(layer);
                }
                self.d2d_context.DrawImage(
                    command_list,
                    None,
                    None,
                    D2D1_INTERPOLATION_MODE_LINEAR,
                    D2D1_COMPOSITE_MODE_SOURCE_OVER,
                );
            }
            Ok(())
        })();

        unsafe {
            if opacity_layer.is_some() {
                self.d2d_context.PopLayer();
            }
            self.d2d_context.SetTransform(&previous_transform);
        }

        draw_result
    }

    fn build_cached_line_visual(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
    ) -> Result<CachedLineVisual, CaptionRenderError> {
        let channel = block.channel.unwrap_or(CaptionChannel::SelfChannel);
        let fill_brush = self.cache_brush_for_channel(channel);
        let outline_brush = self.cache_outline_brush.clone();
        unsafe {
            fill_brush.SetOpacity(1.0);
            outline_brush.SetOpacity(1.0);
        }
        self.build_line_visual_with_brushes(policy, block, line, role, &fill_brush, &outline_brush)
    }

    fn build_line_visual_with_brushes(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
        fill_brush: &ID2D1SolidColorBrush,
        outline_brush: &ID2D1SolidColorBrush,
    ) -> Result<CachedLineVisual, CaptionRenderError> {
        let text_layout = self.create_text_layout(
            policy,
            line.text.trim(),
            line.font_size_px,
            block.content_width_px,
            line.font_size_px * 1.15,
        )?;
        let glyph_visual = render_text_layout_to_command_list(
            &self.d2d_context,
            &self.d2d_factory,
            &text_layout,
            fill_brush,
            outline_brush,
            outline_offsets_px()[0]
                .0
                .abs()
                .max(outline_offsets_px()[2].1.abs())
                * 2.0,
        )?;
        let _ = role;
        Ok(CachedLineVisual {
            command_list: glyph_visual.command_list,
            visual_bounds: glyph_visual.visual_bounds,
        })
    }

    fn cached_line_visual(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
        diagnostics: &mut RenderDiagnostics,
    ) -> Result<CachedLineVisual, CaptionRenderError> {
        let key = self.line_cache_key(block, line, role);
        if let Some(cached) = self.caches.line_cache.get(&key) {
            diagnostics.line_cache_hits += 1;
            return Ok(cached.clone());
        }
        diagnostics.line_cache_misses += 1;
        let cached = self.build_cached_line_visual(policy, block, line, role)?;
        self.caches.line_cache.insert(key, cached.clone());
        Ok(cached)
    }

    fn prepared_line_visual(
        &self,
        block: &ResolvedBlockLayout,
        line: &ResolvedLineLayout,
        role: LineRole,
    ) -> Result<CachedLineVisual, CaptionRenderError> {
        let key = self.line_cache_key(block, line, role);
        self.caches.line_cache.get(&key).cloned().ok_or_else(|| {
            CaptionRenderError::Draw(format!(
                "missing prepared line cache for block={} role={role:?}",
                block.id
            ))
        })
    }

    fn build_cached_block_visual(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        _diagnostics: &mut RenderDiagnostics,
    ) -> Result<CachedBlockVisual, CaptionRenderError> {
        let previous_target = unsafe { self.d2d_context.GetTarget().ok() };
        let command_list = unsafe {
            self.d2d_context
                .CreateCommandList()
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?
        };
        unsafe {
            self.d2d_context.SetTarget(&command_list);
            self.d2d_context.BeginDraw();
        }

        let mut visual_bounds: Option<super::types::VisualBounds> = None;
        let build_result = (|| {
            for (role, line) in block_lines(block) {
                let cached = self.prepared_line_visual(block, line, role)?;
                let offset = Vector2 {
                    X: policy.strip_horizontal_padding_px() as f32,
                    Y: stable_line_origin_y(block, line),
                };
                unsafe {
                    self.d2d_context.DrawImage(
                        &cached.command_list,
                        Some(&offset),
                        None,
                        D2D1_INTERPOLATION_MODE_LINEAR,
                        D2D1_COMPOSITE_MODE_SOURCE_OVER,
                    );
                }
                let translated = super::types::VisualBounds::new(
                    cached.visual_bounds.left_px + offset.X,
                    cached.visual_bounds.top_px + offset.Y,
                    cached.visual_bounds.right_px + offset.X,
                    cached.visual_bounds.bottom_px + offset.Y,
                );
                visual_bounds = Some(match visual_bounds {
                    Some(current) => super::types::VisualBounds::new(
                        current.left_px.min(translated.left_px),
                        current.top_px.min(translated.top_px),
                        current.right_px.max(translated.right_px),
                        current.bottom_px.max(translated.bottom_px),
                    ),
                    None => translated,
                });
            }
            Ok(())
        })();
        let end_draw_result = unsafe {
            self.d2d_context
                .EndDraw(None, None)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))
        };
        unsafe {
            self.d2d_context.SetTarget(previous_target.as_ref());
        }

        match (build_result, end_draw_result) {
            (Err(error), _) => Err(error),
            (Ok(()), Err(error)) => Err(error),
            (Ok(()), Ok(())) => {
                unsafe {
                    command_list
                        .Close()
                        .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
                }
                Ok(CachedBlockVisual {
                    command_list,
                    visual_bounds: visual_bounds
                        .unwrap_or_else(|| super::types::VisualBounds::new(0.0, 0.0, 0.0, 0.0)),
                })
            }
        }
    }

    fn cached_block_visual(
        &mut self,
        policy: &CaptionLayoutPolicy,
        block: &ResolvedBlockLayout,
        diagnostics: &mut RenderDiagnostics,
    ) -> Result<CachedBlockVisual, CaptionRenderError> {
        let key = self.block_cache_key(block);
        if let Some(cached) = self.caches.block_cache.get(&key) {
            diagnostics.block_cache_hits += 1;
            return Ok(cached.clone());
        }
        diagnostics.block_cache_misses += 1;
        let cached = self.build_cached_block_visual(policy, block, diagnostics)?;
        self.caches.block_cache.insert(key, cached.clone());
        Ok(cached)
    }

    fn prepare_line_visuals(
        &mut self,
        policy: &CaptionLayoutPolicy,
        layout: &ResolvedFrameLayout,
        diagnostics: &mut RenderDiagnostics,
    ) -> Result<(), CaptionRenderError> {
        for block in &layout.visible_blocks {
            for (role, line) in block_lines(block) {
                if line.text.trim().is_empty() {
                    continue;
                }
                let _ = self
                    .cached_line_visual(policy, block, line, role, diagnostics)
                    .map_err(|error| prefix_render_error("line_cache_build", error))?;
            }
        }
        Ok(())
    }

    fn prepare_block_visuals(
        &mut self,
        policy: &CaptionLayoutPolicy,
        layout: &ResolvedFrameLayout,
        diagnostics: &mut RenderDiagnostics,
    ) -> Result<(), CaptionRenderError> {
        for block in &layout.visible_blocks {
            if !self.cacheable_block(block) {
                continue;
            }
            let _ = self
                .cached_block_visual(policy, block, diagnostics)
                .map_err(|error| prefix_render_error("block_cache_build", error))?;
        }
        Ok(())
    }

    fn render(
        &mut self,
        policy: &CaptionLayoutPolicy,
        presentation: &CaptionPresentation,
        blocks: Vec<super::types::CaptionBlock>,
        width: u32,
        height: u32,
    ) -> Result<RenderedFrame, CaptionRenderError> {
        let mut diagnostics = RenderDiagnostics::default();
        for block in &blocks {
            let key = policy.layout_cache_key_for_block(block, width, presentation);
            if self.caches.layout_cache.get(&key).is_some() {
                diagnostics.layout_cache_hits += 1;
            } else {
                diagnostics.layout_cache_misses += 1;
            }
        }
        let layout = match policy.resolve_blocks_for_presentation_windows_cached(
            blocks.clone(),
            width,
            height,
            presentation,
            Some(&mut self.caches.layout_cache),
        ) {
            Ok(layout) => layout,
            Err(_) => policy.resolve_blocks_for_presentation(blocks, width, height, presentation),
        };
        let layout = prepare_layout_for_render(&mut self.previous_layout, layout);
        self.prepare_line_visuals(policy, &layout, &mut diagnostics)?;
        self.prepare_block_visuals(policy, &layout, &mut diagnostics)?;
        let clear_alpha =
            effective_background_alpha(resolved_layout_has_drawable_text(&layout), presentation);
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

        let render_result = (|| {
            for block in &layout.visible_blocks {
                if !bounds_intersect_damage_band(block.visual_bounds.as_block_bounds(), damage_band)
                {
                    continue;
                }

                if self.cacheable_block(block) {
                    let cached_block = self
                        .caches
                        .block_cache
                        .get(&self.block_cache_key(block))
                        .cloned()
                        .ok_or_else(|| {
                            CaptionRenderError::Draw(format!(
                                "missing prepared block cache for block={}",
                                block.id
                            ))
                        })?;
                    self.draw_cached_command_list_with_state(
                        &cached_block.command_list,
                        block.bounds.left_px,
                        block.bounds.top_px,
                        block.opacity,
                        block.render_height_scale,
                    )?;
                    continue;
                }

                for (role, line) in block_lines(block) {
                    let trimmed = line.text.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    let line_visual = self.prepared_line_visual(block, line, role)?;
                    self.draw_cached_command_list_with_state(
                        &line_visual.command_list,
                        block.bounds.left_px + policy.strip_horizontal_padding_px() as f32,
                        line.origin_y,
                        block.opacity,
                        block.render_height_scale,
                    )?;
                }
            }
            let public_layout: CaptionLayoutResult = layout.clone().into();
            Ok(RenderedFrame {
                width: public_layout.surface_width_px,
                height: public_layout.surface_height_px,
                fully_transparent: !resolved_layout_has_drawable_text(&layout),
                layout: public_layout,
                diagnostics,
                texture: TextureHandle::D3D11(self.texture.clone()),
            })
        })()
        .map_err(|error| prefix_render_error("frame_compose", error));
        let end_draw_result = unsafe {
            self.d2d_context
                .EndDraw(None, None)
                .map_err(|error| CaptionRenderError::Draw(format!("frame_compose: {}", error)))
        };

        match (render_result, end_draw_result) {
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) => Err(error),
            (Ok(frame), Ok(())) => Ok(frame),
        }
    }

    fn create_text_format(
        &mut self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        font_size_px: f32,
    ) -> Result<IDWriteTextFormat, CaptionRenderError> {
        let bucket = text_script_bucket(text);
        let font_size_key = (font_size_px * 100.0).round() as u32;
        if let Some(text_format) = self.caches.text_format_cache.get(&(bucket, font_size_key)) {
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
                    font_size_px,
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
            if let Ok(text_format_1) = text_format.cast::<IDWriteTextFormat1>() {
                text_format_1
                    .SetFontFallback(&self.system_font_fallback)
                    .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            }
        }
        self.caches
            .text_format_cache
            .insert((bucket, font_size_key), text_format.clone());
        Ok(text_format)
    }

    fn create_text_layout(
        &mut self,
        policy: &CaptionLayoutPolicy,
        text: &str,
        font_size_px: f32,
        max_width_px: f32,
        max_height_px: f32,
    ) -> Result<IDWriteTextLayout, CaptionRenderError> {
        let text_format = self.create_text_format(policy, text, font_size_px)?;
        let utf16: Vec<u16> = text.encode_utf16().collect();
        let text_layout = unsafe {
            self.dwrite_factory
                .CreateTextLayout(&utf16, &text_format, max_width_px, max_height_px)
                .map_err(|error| CaptionRenderError::Draw(error.to_string()))?
        };
        if let Ok(text_layout_2) = text_layout.cast::<IDWriteTextLayout2>() {
            unsafe {
                text_layout_2
                    .SetFontFallback(&self.system_font_fallback)
                    .map_err(|error| CaptionRenderError::Draw(error.to_string()))?;
            }
        }
        Ok(text_layout)
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

        Ok(ResolvedTextStyle {
            family_name: "Segoe UI".to_string(),
            weight: DWRITE_FONT_WEIGHT_NORMAL,
        })
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
                .FindFamilyName(
                    PCWSTR::from_raw(family_name.as_ptr()),
                    &mut index,
                    &mut exists,
                )
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
fn d2d_color(color: (f32, f32, f32, f32)) -> D2D1_COLOR_F {
    D2D1_COLOR_F {
        r: color.0,
        g: color.1,
        b: color.2,
        a: color.3,
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
fn create_d2d_factory() -> Result<ID2D1Factory1, CaptionRenderError> {
    unsafe {
        D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, None)
            .map_err(|error| CaptionRenderError::Init(error.to_string()))
    }
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
fn create_d2d_context(
    device: &ID3D11Device,
    factory: &ID2D1Factory1,
) -> Result<ID2D1DeviceContext, CaptionRenderError> {
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

#[cfg(windows)]
fn identity_matrix() -> Matrix3x2 {
    Matrix3x2 {
        M11: 1.0,
        M12: 0.0,
        M21: 0.0,
        M22: 1.0,
        M31: 0.0,
        M32: 0.0,
    }
}

#[cfg(windows)]
fn stable_line_origin_y(block: &ResolvedBlockLayout, line: &ResolvedLineLayout) -> f32 {
    (line.origin_y - block.bounds.top_px) / block.render_height_scale.max(f32::EPSILON)
}

#[cfg(windows)]
fn prefix_render_error(stage: &str, error: CaptionRenderError) -> CaptionRenderError {
    match error {
        CaptionRenderError::Init(message) => {
            CaptionRenderError::Init(format!("{stage}: {message}"))
        }
        CaptionRenderError::Draw(message) => {
            CaptionRenderError::Draw(format!("{stage}: {message}"))
        }
    }
}

#[cfg(windows)]
fn block_lines(
    block: &ResolvedBlockLayout,
) -> impl Iterator<Item = (LineRole, &ResolvedLineLayout)> + '_ {
    block
        .primary_lines
        .iter()
        .map(|line| (LineRole::Primary, line))
        .chain(
            block
                .secondary_line
                .iter()
                .map(|line| (LineRole::Secondary, line)),
        )
}

fn prepare_layout_for_render(
    previous_layout: &mut Option<ResolvedFrameLayout>,
    mut layout: ResolvedFrameLayout,
) -> ResolvedFrameLayout {
    layout.damage_band = compute_damage_band(previous_layout.as_ref(), &layout);
    *previous_layout = Some(layout.clone());
    layout
}

fn compute_damage_band(
    previous_layout: Option<&ResolvedFrameLayout>,
    next_layout: &ResolvedFrameLayout,
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
        .map(|block| (block.id.as_str(), block.visual_bounds.as_block_bounds()))
        .collect::<std::collections::HashMap<_, _>>();
    let next_bounds = next_layout
        .visible_blocks
        .iter()
        .map(|block| (block.id.as_str(), block.visual_bounds.as_block_bounds()))
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
