use std::cell::RefCell;
use std::ffi::c_void;
#[cfg(windows)]
use std::ffi::{CStr, CString};

use thiserror::Error;

use crate::renderer::RenderedFrame;
use crate::state::OverlayCalibration;

#[cfg(windows)]
const OVERLAY_KEY_PREFIX: &str = "com.puripuly.heart.overlay.";
#[cfg(windows)]
const OVERLAY_NAME_PREFIX: &str = "PuriPuly Heart Overlay ";
const DEFAULT_OVERLAY_WIDTH_METERS: f32 = 1.8;
const DEFAULT_OVERLAY_DISTANCE_METERS: f32 = 1.0;

#[derive(Debug, Clone, PartialEq)]
pub struct OverlayPlacementPolicy {
    anchor: &'static str,
    width_meters: f32,
    offset_x_meters: f32,
    offset_y_meters: f32,
    distance_meters: f32,
}

impl Default for OverlayPlacementPolicy {
    fn default() -> Self {
        Self {
            anchor: "head_locked",
            width_meters: DEFAULT_OVERLAY_WIDTH_METERS,
            offset_x_meters: 0.0,
            offset_y_meters: 0.0,
            distance_meters: DEFAULT_OVERLAY_DISTANCE_METERS,
        }
    }
}

impl OverlayPlacementPolicy {
    pub fn is_head_locked(&self) -> bool {
        self.anchor == "head_locked"
    }

    pub fn from_calibration(calibration: &OverlayCalibration) -> Self {
        Self {
            anchor: "head_locked",
            width_meters: DEFAULT_OVERLAY_WIDTH_METERS * calibration.text_scale.max(0.1),
            offset_x_meters: calibration.offset_x,
            offset_y_meters: calibration.offset_y,
            distance_meters: calibration.distance.max(0.1),
        }
    }

    #[cfg(windows)]
    fn apply(
        &self,
        overlay_api: &openvr_sys::VR_IVROverlay_FnTable,
        overlay_handle: openvr_sys::VROverlayHandle_t,
    ) -> Result<(), OpenVrError> {
        let set_overlay_width_in_meters = overlay_api
            .SetOverlayWidthInMeters
            .ok_or_else(missing_overlay_method("SetOverlayWidthInMeters"))?;
        let error = unsafe { set_overlay_width_in_meters(overlay_handle, self.width_meters) };
        map_overlay_init_error(overlay_api, "SetOverlayWidthInMeters", error)?;

        let set_overlay_transform = overlay_api
            .SetOverlayTransformTrackedDeviceRelative
            .ok_or_else(missing_overlay_method("SetOverlayTransformTrackedDeviceRelative"))?;
        let mut transform = self.hmd_relative_transform();
        let error = unsafe {
            set_overlay_transform(
                overlay_handle,
                openvr_sys::k_unTrackedDeviceIndex_Hmd,
                &mut transform,
            )
        };
        map_overlay_init_error(
            overlay_api,
            "SetOverlayTransformTrackedDeviceRelative",
            error,
        )
    }

    #[cfg(windows)]
    fn hmd_relative_transform(&self) -> openvr_sys::HmdMatrix34_t {
        openvr_sys::HmdMatrix34_t {
            m: [
                [1.0, 0.0, 0.0, self.offset_x_meters],
                [0.0, 1.0, 0.0, self.offset_y_meters],
                [0.0, 0.0, 1.0, -self.distance_meters],
            ],
        }
    }
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum OpenVrError {
    #[error("openvr init failed: {0}")]
    Init(String),
    #[error("openvr texture submission failed: {0}")]
    Submit(String),
}

pub trait OverlayTextureSubmitter {
    fn set_overlay_texture(&self, texture_handle: *mut c_void) -> Result<(), OpenVrError>;
}

pub trait OverlayFrameSubmitter {
    fn submit_frame(&mut self, frame: &RenderedFrame) -> Result<(), OpenVrError>;

    fn apply_calibration(&mut self, _calibration: &OverlayCalibration) -> Result<(), OpenVrError> {
        Ok(())
    }
}

pub fn submit_texture<T: OverlayTextureSubmitter>(
    openvr: &T,
    frame: &RenderedFrame,
) -> Result<(), OpenVrError> {
    let texture_handle = frame
        .texture_ptr()
        .ok_or_else(|| OpenVrError::Submit("renderer returned no texture".into()))?;
    openvr.set_overlay_texture(texture_handle)
}

#[derive(Debug, Default)]
pub struct FakeOpenVr {
    last_call: RefCell<Option<String>>,
}

impl FakeOpenVr {
    pub fn last_call(&self) -> Option<String> {
        self.last_call.borrow().clone()
    }
}

impl OverlayTextureSubmitter for FakeOpenVr {
    fn set_overlay_texture(&self, _texture_handle: *mut c_void) -> Result<(), OpenVrError> {
        self.last_call
            .replace(Some("SetOverlayTexture".to_string()));
        Ok(())
    }
}

pub struct OpenVrOverlay {
    backend: OpenVrBackend,
}

impl OpenVrOverlay {
    pub fn new(overlay_instance_id: &str) -> Result<Self, OpenVrError> {
        Ok(Self {
            backend: OpenVrBackend::new(overlay_instance_id)?,
        })
    }
}

impl OverlayFrameSubmitter for OpenVrOverlay {
    fn submit_frame(&mut self, frame: &RenderedFrame) -> Result<(), OpenVrError> {
        self.backend.submit_frame(frame)
    }

    fn apply_calibration(&mut self, calibration: &OverlayCalibration) -> Result<(), OpenVrError> {
        self.backend.apply_calibration(calibration)
    }
}

enum OpenVrBackend {
    #[cfg(windows)]
    Windows(WindowsOpenVrOverlay),
    Test(FakeOpenVr),
}

impl OpenVrBackend {
    fn new(overlay_instance_id: &str) -> Result<Self, OpenVrError> {
        #[cfg(windows)]
        {
            return WindowsOpenVrOverlay::new(overlay_instance_id).map(Self::Windows);
        }

        #[cfg(not(windows))]
        {
            let _ = overlay_instance_id;
            Ok(Self::Test(FakeOpenVr::default()))
        }
    }

    fn submit_frame(&mut self, frame: &RenderedFrame) -> Result<(), OpenVrError> {
        match self {
            #[cfg(windows)]
            Self::Windows(openvr) => openvr.submit_frame(frame),
            Self::Test(openvr) => submit_texture(openvr, frame),
        }
    }

    fn apply_calibration(&mut self, calibration: &OverlayCalibration) -> Result<(), OpenVrError> {
        #[cfg(not(windows))]
        let _ = calibration;

        match self {
            #[cfg(windows)]
            Self::Windows(openvr) => openvr.apply_calibration(calibration),
            Self::Test(_) => Ok(()),
        }
    }
}

#[cfg(windows)]
struct WindowsOpenVrOverlay {
    overlay_api: *mut openvr_sys::VR_IVROverlay_FnTable,
    overlay_handle: openvr_sys::VROverlayHandle_t,
    placement_policy: OverlayPlacementPolicy,
    visible: bool,
}

#[cfg(windows)]
impl WindowsOpenVrOverlay {
    fn new(overlay_instance_id: &str) -> Result<Self, OpenVrError> {
        let overlay_api = initialize_overlay_api()?;
        let overlay_handle = create_overlay_handle(overlay_api, overlay_instance_id)?;

        let instance = Self {
            overlay_api,
            overlay_handle,
            placement_policy: OverlayPlacementPolicy::default(),
            visible: false,
        };
        instance.configure_overlay()?;
        Ok(instance)
    }

    fn submit_frame(&mut self, frame: &RenderedFrame) -> Result<(), OpenVrError> {
        submit_texture(self, frame)?;
        if !self.visible {
            self.show_overlay()?;
            self.visible = true;
        }
        Ok(())
    }

    fn configure_overlay(&self) -> Result<(), OpenVrError> {
        let set_overlay_rendering_pid = self
            .overlay_api()
            .SetOverlayRenderingPid
            .ok_or_else(missing_overlay_method("SetOverlayRenderingPid"))?;
        let error =
            unsafe { set_overlay_rendering_pid(self.overlay_handle, std::process::id()) };
        map_overlay_init_error(self.overlay_api(), "SetOverlayRenderingPid", error)?;

        let set_overlay_flag = self
            .overlay_api()
            .SetOverlayFlag
            .ok_or_else(missing_overlay_method("SetOverlayFlag"))?;
        let error = unsafe {
            set_overlay_flag(
                self.overlay_handle,
                openvr_sys::VROverlayFlags_IsPremultiplied,
                true,
            )
        };
        map_overlay_init_error(self.overlay_api(), "SetOverlayFlag", error)?;
        self.placement_policy
            .apply(self.overlay_api(), self.overlay_handle)?;
        Ok(())
    }

    fn show_overlay(&self) -> Result<(), OpenVrError> {
        let show_overlay = self
            .overlay_api()
            .ShowOverlay
            .ok_or_else(missing_overlay_method("ShowOverlay"))?;
        let error = unsafe { show_overlay(self.overlay_handle) };
        map_overlay_init_error(self.overlay_api(), "ShowOverlay", error)
    }

    fn overlay_api(&self) -> &openvr_sys::VR_IVROverlay_FnTable {
        unsafe { &*self.overlay_api }
    }

    fn apply_calibration(&mut self, calibration: &OverlayCalibration) -> Result<(), OpenVrError> {
        self.placement_policy = OverlayPlacementPolicy::from_calibration(calibration);
        self.placement_policy
            .apply(self.overlay_api(), self.overlay_handle)
    }
}

#[cfg(windows)]
impl OverlayTextureSubmitter for WindowsOpenVrOverlay {
    fn set_overlay_texture(&self, texture_handle: *mut c_void) -> Result<(), OpenVrError> {
        let method = self
            .overlay_api()
            .SetOverlayTexture
            .ok_or_else(missing_overlay_method("SetOverlayTexture"))?;
        let mut descriptor = openvr_sys::Texture_t {
            handle: texture_handle,
            eType: openvr_sys::ETextureType_TextureType_DirectX,
            eColorSpace: openvr_sys::EColorSpace_ColorSpace_Auto,
        };
        let error = unsafe { method(self.overlay_handle, &mut descriptor) };
        map_overlay_submit_error(self.overlay_api(), "SetOverlayTexture", error)
    }
}

#[cfg(windows)]
impl Drop for WindowsOpenVrOverlay {
    fn drop(&mut self) {
        if self.overlay_api.is_null() {
            return;
        }
        if let Some(destroy_overlay) = self.overlay_api().DestroyOverlay {
            unsafe {
                destroy_overlay(self.overlay_handle);
            }
        }
        unsafe {
            openvr_sys::VR_ShutdownInternal();
        }
    }
}

#[cfg(windows)]
fn initialize_overlay_api() -> Result<*mut openvr_sys::VR_IVROverlay_FnTable, OpenVrError> {
    let mut init_error = openvr_sys::EVRInitError_VRInitError_None;
    unsafe {
        openvr_sys::VR_InitInternal(
            &mut init_error,
            openvr_sys::EVRApplicationType_VRApplication_Overlay,
        );
    }
    if init_error != openvr_sys::EVRInitError_VRInitError_None {
        return Err(OpenVrError::Init(format!(
            "VR_InitInternal failed: {}",
            vr_init_error_name(init_error)
        )));
    }

    let mut interface_error = openvr_sys::EVRInitError_VRInitError_None;
    let overlay_api = unsafe {
        openvr_sys::VR_GetGenericInterface(
            openvr_sys::IVROverlay_Version.as_ptr().cast(),
            &mut interface_error,
        )
    };
    if interface_error != openvr_sys::EVRInitError_VRInitError_None || overlay_api == 0 {
        unsafe {
            openvr_sys::VR_ShutdownInternal();
        }
        return Err(OpenVrError::Init(format!(
            "VR_GetGenericInterface failed: {}",
            vr_init_error_name(interface_error)
        )));
    }

    Ok(overlay_api as *mut openvr_sys::VR_IVROverlay_FnTable)
}

#[cfg(windows)]
fn create_overlay_handle(
    overlay_api: *mut openvr_sys::VR_IVROverlay_FnTable,
    overlay_instance_id: &str,
) -> Result<openvr_sys::VROverlayHandle_t, OpenVrError> {
    let key = CString::new(format!("{OVERLAY_KEY_PREFIX}{overlay_instance_id}"))
        .map_err(|error| OpenVrError::Init(error.to_string()))?;
    let name = CString::new(format!("{OVERLAY_NAME_PREFIX}{overlay_instance_id}"))
        .map_err(|error| OpenVrError::Init(error.to_string()))?;
    let create_overlay = unsafe { (*overlay_api).CreateOverlay }
        .ok_or_else(missing_overlay_method("CreateOverlay"))?;
    let mut handle = 0;
    let error = unsafe {
        create_overlay(
            key.as_ptr().cast_mut(),
            name.as_ptr().cast_mut(),
            &mut handle,
        )
    };
    if error != openvr_sys::EVROverlayError_VROverlayError_None {
        unsafe {
            openvr_sys::VR_ShutdownInternal();
        }
        return Err(OpenVrError::Init(format!(
            "CreateOverlay failed: {}",
            overlay_error_name(unsafe { &*overlay_api }, error)
        )));
    }
    Ok(handle)
}

#[cfg(windows)]
fn missing_overlay_method(method_name: &'static str) -> impl FnOnce() -> OpenVrError {
    move || OpenVrError::Init(format!("missing OpenVR overlay method: {method_name}"))
}

#[cfg(windows)]
fn map_overlay_init_error(
    overlay_api: &openvr_sys::VR_IVROverlay_FnTable,
    method_name: &str,
    error: openvr_sys::EVROverlayError,
) -> Result<(), OpenVrError> {
    if error == openvr_sys::EVROverlayError_VROverlayError_None {
        return Ok(());
    }
    Err(OpenVrError::Init(format!(
        "{method_name} failed: {}",
        overlay_error_name(overlay_api, error)
    )))
}

#[cfg(windows)]
fn map_overlay_submit_error(
    overlay_api: &openvr_sys::VR_IVROverlay_FnTable,
    method_name: &str,
    error: openvr_sys::EVROverlayError,
) -> Result<(), OpenVrError> {
    if error == openvr_sys::EVROverlayError_VROverlayError_None {
        return Ok(());
    }
    Err(OpenVrError::Submit(format!(
        "{method_name} failed: {}",
        overlay_error_name(overlay_api, error)
    )))
}

#[cfg(windows)]
fn overlay_error_name(
    overlay_api: &openvr_sys::VR_IVROverlay_FnTable,
    error: openvr_sys::EVROverlayError,
) -> String {
    let Some(get_error_name) = overlay_api.GetOverlayErrorNameFromEnum else {
        return format!("code {error}");
    };
    let name = unsafe { get_error_name(error) };
    if name.is_null() {
        return format!("code {error}");
    }
    unsafe { CStr::from_ptr(name) }
        .to_string_lossy()
        .into_owned()
}

#[cfg(windows)]
fn vr_init_error_name(error: openvr_sys::EVRInitError) -> String {
    let name = unsafe { openvr_sys::VR_GetVRInitErrorAsSymbol(error) };
    if name.is_null() {
        return format!("code {error}");
    }
    unsafe { CStr::from_ptr(name) }
        .to_string_lossy()
        .into_owned()
}
