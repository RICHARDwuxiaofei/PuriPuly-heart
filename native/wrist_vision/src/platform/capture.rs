use std::ffi::c_void;
use windows::core::Interface;
use windows::Win32::Foundation::HMODULE;
use windows::Win32::Graphics::Direct3D::D3D_DRIVER_TYPE_UNKNOWN;
use windows::Win32::Graphics::Direct3D11::*;
use windows::Win32::Graphics::Dxgi::Common::*;
use windows::Win32::Graphics::Dxgi::{CreateDXGIFactory1, IDXGIAdapter, IDXGIFactory1};

pub struct CaptureDevice {
    device: ID3D11Device,
    context: ID3D11DeviceContext,
}

impl CaptureDevice {
    pub fn new(adapter_index: u32) -> Result<Self, &'static str> {
        unsafe {
            let factory: IDXGIFactory1 = CreateDXGIFactory1().map_err(|_| "dxgi_factory_failed")?;
            let adapter: IDXGIAdapter = factory
                .EnumAdapters1(adapter_index)
                .and_then(|a| a.cast())
                .map_err(|_| "hmd_adapter_unavailable")?;
            let (mut device, mut context) = (None, None);
            D3D11CreateDevice(
                &adapter,
                D3D_DRIVER_TYPE_UNKNOWN,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                None,
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            )
            .map_err(|_| "d3d11_create_failed")?;
            Ok(Self {
                device: device.ok_or("d3d11_create_failed")?,
                context: context.ok_or("d3d11_create_failed")?,
            })
        }
    }
    pub fn raw(&self) -> *mut c_void {
        self.device.as_raw()
    }

    pub unsafe fn read(&self, view: *mut c_void) -> Result<(Vec<u8>, [u32; 2]), &'static str> {
        let srv = ID3D11ShaderResourceView::from_raw_borrowed(&view).ok_or("mirror_invalid")?;
        let resource = srv.GetResource().map_err(|_| "mirror_resource_failed")?;
        let texture: ID3D11Texture2D = resource.cast().map_err(|_| "mirror_not_texture")?;
        let mut desc = D3D11_TEXTURE2D_DESC::default();
        texture.GetDesc(&mut desc);
        if desc.Width == 0
            || desc.Height == 0
            || desc.Width > 8192
            || desc.Height > 8192
            || desc.Width as u64 * desc.Height as u64 > 16_777_216
            || desc.SampleDesc.Count != 1
            || ![
                DXGI_FORMAT_R8G8B8A8_UNORM,
                DXGI_FORMAT_R8G8B8A8_UNORM_SRGB,
                DXGI_FORMAT_B8G8R8A8_UNORM,
                DXGI_FORMAT_B8G8R8A8_UNORM_SRGB,
            ]
            .contains(&desc.Format)
        {
            return Err("mirror_format_unsupported");
        }
        let bgra =
            [DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_B8G8R8A8_UNORM_SRGB].contains(&desc.Format);
        desc.Usage = D3D11_USAGE_STAGING;
        desc.BindFlags = 0;
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ.0 as u32;
        desc.MiscFlags = 0;
        desc.MipLevels = 1;
        desc.ArraySize = 1;
        let mut staging = None;
        self.device
            .CreateTexture2D(&desc, None, Some(&mut staging))
            .map_err(|_| "staging_create_failed")?;
        let staging = staging.ok_or("staging_create_failed")?;
        self.context
            .CopySubresourceRegion(&staging, 0, 0, 0, 0, &resource, 0, None);
        let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
        self.context
            .Map(&staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped))
            .map_err(|_| "mirror_map_failed")?;
        let result = (|| {
            if mapped.pData.is_null() || mapped.RowPitch < desc.Width * 4 {
                return Err("mirror_pitch_invalid");
            }
            let mut rgba = Vec::with_capacity(desc.Width as usize * desc.Height as usize * 4);
            for y in 0..desc.Height as usize {
                rgba.extend_from_slice(std::slice::from_raw_parts(
                    mapped.pData.cast::<u8>().add(y * mapped.RowPitch as usize),
                    desc.Width as usize * 4,
                ));
            }
            for p in rgba.chunks_exact_mut(4) {
                if bgra {
                    p.swap(0, 2);
                }
                p[3] = 255;
            }
            Ok((rgba, [desc.Width, desc.Height]))
        })();
        self.context.Unmap(&staging, 0);
        result
    }
}
