use super::{capture::CaptureDevice, render};
use crate::geometry::{self, Pose, IDENTITY};
use serde_json::{json, Value};
use std::{
    ffi::{c_void, CString},
    time::{Duration, Instant},
};

struct Session;
impl Drop for Session {
    fn drop(&mut self) {
        unsafe {
            openvr_sys::VR_ShutdownInternal();
        }
    }
}
struct Mirror {
    view: *mut c_void,
    release: unsafe extern "C" fn(*mut c_void),
}
impl Drop for Mirror {
    fn drop(&mut self) {
        if !self.view.is_null() {
            unsafe {
                (self.release)(self.view);
            }
        }
    }
}

unsafe fn table<T>(version: &[u8]) -> Result<*mut T, &'static str> {
    let bytes = version.strip_suffix(&[0]).unwrap_or(version);
    let name =
        CString::new([b"FnTable:".as_slice(), bytes].concat()).map_err(|_| "interface_invalid")?;
    let mut error = 0;
    let pointer = openvr_sys::VR_GetGenericInterface(name.as_ptr(), &mut error) as *mut T;
    if error != 0 || pointer.is_null() {
        Err("openvr_interface_missing")
    } else {
        Ok(pointer.cast())
    }
}

pub struct Vr {
    system: *mut openvr_sys::VR_IVRSystem_FnTable,
    overlay: *mut openvr_sys::VR_IVROverlay_FnTable,
    compositor: *mut openvr_sys::VR_IVRCompositor_FnTable,
    panel: u64,
    frame: u64,
    mirror: Option<Mirror>,
    capture_device: Option<CaptureDevice>,
    pub hand: i32,
    pub width: f32,
    pub offset: [f32; 3],
    pub head_frame: bool,
    last_capture: Option<Instant>,
    _session: Session,
}

fn check(code: i32) -> Result<(), &'static str> {
    if code == 0 {
        Ok(())
    } else {
        Err("openvr_overlay_failed")
    }
}

impl Vr {
    pub fn new() -> Result<Self, &'static str> {
        unsafe {
            let mut error = 0;
            openvr_sys::VR_InitInternal(
                &mut error,
                openvr_sys::EVRApplicationType_VRApplication_Overlay,
            );
            if error != 0 {
                return Err("steamvr_unavailable");
            }
            let session = Session;
            let system = table(openvr_sys::IVRSystem_Version)?;
            let overlay = table(openvr_sys::IVROverlay_Version)?;
            let compositor = table(openvr_sys::IVRCompositor_Version)?;
            let mut this = Self {
                system,
                overlay,
                compositor,
                panel: 0,
                frame: 0,
                mirror: None,
                capture_device: None,
                hand: 1,
                width: 0.24,
                offset: [0., 0.08, 0.02],
                head_frame: false,
                last_capture: None,
                _session: session,
            };
            let create = (*overlay).CreateOverlay.ok_or("overlay_api_missing")?;
            for (suffix, handle) in [("chat", &mut this.panel), ("frame", &mut this.frame)] {
                let key = CString::new(format!(
                    "com.puripuly.heart.wrist.{}.{}",
                    std::process::id(),
                    suffix
                ))
                .unwrap();
                let name = CString::new(format!("PuriPuly Wrist {}", suffix)).unwrap();
                check(create(
                    key.as_ptr().cast_mut(),
                    name.as_ptr().cast_mut(),
                    handle,
                ))?;
            }
            Ok(this)
        }
    }
    pub fn configure(&mut self, values: &Value) -> Result<(), &'static str> {
        unsafe {
            self.hand = if values["hand"] == "right" { 2 } else { 1 };
            self.width = values["width_m"].as_f64().unwrap_or(0.24).clamp(0.12, 0.6) as f32;
            self.offset = ["offset_x", "offset_y", "offset_z"].map(|k| {
                values[k]
                    .as_f64()
                    .unwrap_or(if k == "offset_y" { 0.08 } else { 0.02 })
                    .clamp(-0.5, 0.5) as f32
            });
            self.head_frame = values["capture_mode"] == "head_frame";
            check((*self.overlay)
                .SetOverlayWidthInMeters
                .ok_or("overlay_api_missing")?(
                self.panel, self.width
            ))?;
            check((*self.overlay)
                .SetOverlayAlpha
                .ok_or("overlay_api_missing")?(
                self.panel,
                values["opacity"].as_f64().unwrap_or(1.).clamp(0.4, 1.) as f32,
            ))?;
            Ok(())
        }
    }
    fn role_device(&self, other: bool) -> Result<u32, &'static str> {
        unsafe {
            let role = if other {
                if self.hand == 1 {
                    2
                } else {
                    1
                }
            } else {
                self.hand
            };
            let device = (*self.system)
                .GetTrackedDeviceIndexForControllerRole
                .ok_or("controller_api_missing")?(role);
            if device == openvr_sys::k_unTrackedDeviceIndexInvalid {
                Err("controller_unavailable")
            } else {
                Ok(device)
            }
        }
    }
    pub fn tracking(&self) -> Result<(Pose, u32, i32), &'static str> {
        unsafe {
            let table = &*self.compositor;
            let mut pose: openvr_sys::TrackedDevicePose_t = std::mem::zeroed();
            let result =
                table
                    .GetLastPoseForTrackedDeviceIndex
                    .ok_or("pose_api_missing")?(0, &mut pose, std::ptr::null_mut());
            if result != 0 || !pose.bPoseIsValid || !pose.bDeviceIsConnected {
                return Err("hmd_tracking_unavailable");
            }
            let pid = table.GetLastFrameRenderer.ok_or("scene_api_missing")?();
            if pid == 0 {
                return Err("scene_unavailable");
            }
            Ok((
                pose.mDeviceToAbsoluteTracking.m,
                pid,
                table.GetTrackingSpace.ok_or("pose_api_missing")?(),
            ))
        }
    }
    fn device_pose(&self, device: u32, origin: i32) -> Result<Pose, &'static str> {
        unsafe {
            let mut poses: [openvr_sys::TrackedDevicePose_t; 64] = std::mem::zeroed();
            (*self.system)
                .GetDeviceToAbsoluteTrackingPose
                .ok_or("pose_api_missing")?(origin, 0., poses.as_mut_ptr(), 64);
            let p = poses.get(device as usize).ok_or("controller_unavailable")?;
            if !p.bPoseIsValid || !p.bDeviceIsConnected {
                return Err("controller_tracking_unavailable");
            }
            Ok(p.mDeviceToAbsoluteTracking.m)
        }
    }
    fn local_panel(&self) -> Pose {
        [
            [1., 0., 0., self.offset[0]],
            [0., 0., 1., self.offset[1]],
            [0., -1., 0., self.offset[2]],
        ]
    }
    pub fn panel_pose(&self) -> Result<Pose, &'static str> {
        let (_, _, origin) = self.tracking()?;
        Ok(geometry::compose(
            self.device_pose(self.role_device(false)?, origin)?,
            self.local_panel(),
        ))
    }
    pub fn place(&self) -> Result<(), &'static str> {
        unsafe {
            let device = self.role_device(false)?;
            let mut pose = openvr_sys::HmdMatrix34_t {
                m: self.local_panel(),
            };
            check((*self.overlay)
                .SetOverlayTransformTrackedDeviceRelative
                .ok_or("overlay_api_missing")?(
                self.panel, device, &mut pose
            ))
        }
    }
    pub fn submit(&self, pixels: &mut [u8]) -> Result<(), &'static str> {
        unsafe {
            if pixels.len() != render::WIDTH as usize * render::HEIGHT as usize * 4 {
                return Err("render_size_invalid");
            }
            check((*self.overlay)
                .SetOverlayRaw
                .ok_or("overlay_api_missing")?(
                self.panel,
                pixels.as_mut_ptr().cast(),
                render::WIDTH,
                render::HEIGHT,
                4,
            ))?;
            check((*self.overlay).ShowOverlay.ok_or("overlay_api_missing")?(
                self.panel,
            ))
        }
    }
    pub fn hide(&self, handle: u64) {
        unsafe {
            if let Some(hide) = (*self.overlay).HideOverlay {
                hide(handle);
            }
        }
    }
    pub fn hide_all(&self) {
        self.hide(self.panel);
        self.hide(self.frame);
    }
    pub fn hide_frame(&self) {
        self.hide(self.frame);
    }
    pub fn show_frame(&self) -> Result<(), &'static str> {
        unsafe {
            self.hide(self.panel);
            let set = (*self.overlay)
                .SetOverlayTransformTrackedDeviceRelative
                .ok_or("overlay_api_missing")?;
            let (device, m, width) = if self.head_frame {
                (
                    0,
                    [[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., -0.7]],
                    0.7,
                )
            } else {
                (self.role_device(false)?, self.local_panel(), self.width)
            };
            let mut matrix = openvr_sys::HmdMatrix34_t { m };
            check(set(self.frame, device, &mut matrix))?;
            check((*self.overlay)
                .SetOverlayWidthInMeters
                .ok_or("overlay_api_missing")?(
                self.frame, width
            ))?;
            let mut pixels = render::frame();
            check((*self.overlay)
                .SetOverlayRaw
                .ok_or("overlay_api_missing")?(
                self.frame,
                pixels.as_mut_ptr().cast(),
                512,
                320,
                4,
            ))?;
            check((*self.overlay).ShowOverlay.ok_or("overlay_api_missing")?(
                self.frame,
            ))
        }
    }
    pub fn frame_world(&self) -> Result<[[f32; 3]; 4], &'static str> {
        if self.head_frame {
            let (head, _, _) = self.tracking()?;
            let mut local = IDENTITY;
            local[2][3] = -0.7;
            Ok(geometry::corners(
                geometry::compose(head, local),
                0.7,
                0.7 * 320. / 512.,
            ))
        } else {
            Ok(geometry::corners(
                self.panel_pose()?,
                self.width,
                self.width * 320. / 512.,
            ))
        }
    }
    pub fn input(&self) -> Result<(bool, bool, Option<[f32; 2]>), &'static str> {
        unsafe {
            let (_, _, origin) = self.tracking()?;
            let other = self.role_device(true)?;
            let pose = self.device_pose(other, origin)?;
            let mut state: openvr_sys::VRControllerState_t = std::mem::zeroed();
            if !(*self.system)
                .GetControllerState
                .ok_or("controller_input_unavailable")?(
                other,
                &mut state,
                std::mem::size_of_val(&state) as u32,
            ) {
                return Err("controller_input_unavailable");
            }
            let trigger = state.ulButtonPressed & (1_u64 << 33) != 0 || state.rAxis[1].x > 0.75;
            let cancel = state.ulButtonPressed & ((1_u64 << 1) | (1_u64 << 2)) != 0;
            let panel_inverse = geometry::inverse(self.panel_pose()?);
            let start = geometry::transform(panel_inverse, [pose[0][3], pose[1][3], pose[2][3]]);
            let end = geometry::transform(panel_inverse, geometry::transform(pose, [0., 0., -1.]));
            let direction = std::array::from_fn::<_, 3, _>(|r| end[r] - start[r]);
            let point = if direction[2].abs() > 0.00001 {
                let t = -start[2] / direction[2];
                let x = (start[0] + t * direction[0]) / self.width + 0.5;
                let y = 0.5
                    - (start[1] + t * direction[1])
                        / (self.width * render::HEIGHT as f32 / render::WIDTH as f32);
                (t > 0. && (0. ..=1.).contains(&x) && (0. ..=1.).contains(&y)).then_some([x, y])
            } else {
                None
            };
            Ok((trigger, cancel, point))
        }
    }
    pub fn capture(&mut self) -> Result<Value, &'static str> {
        unsafe {
            if self
                .last_capture
                .is_some_and(|t| t.elapsed() < Duration::from_secs(1))
            {
                return Err("capture_rate_limited");
            }
            self.last_capture = Some(Instant::now());
            let world = self.frame_world()?;
            let (before, pid, origin) = self.tracking()?;
            self.hide_all();
            std::thread::sleep(Duration::from_millis(90));
            if self.capture_device.is_none() {
                let mut index = 0;
                (*self.system)
                    .GetDXGIOutputInfo
                    .ok_or("hmd_adapter_unavailable")?(&mut index);
                if index < 0 {
                    return Err("hmd_adapter_unavailable");
                }
                self.capture_device = Some(CaptureDevice::new(index as u32)?);
            }
            self.mirror = None;
            let comp = &*self.compositor;
            let mut mirror = Mirror {
                view: std::ptr::null_mut(),
                release: comp.ReleaseMirrorTextureD3D11.ok_or("mirror_api_missing")?,
            };
            if comp.GetMirrorTextureD3D11.ok_or("mirror_api_missing")?(
                0,
                self.capture_device.as_ref().unwrap().raw(),
                &mut mirror.view,
            ) != 0
                || mirror.view.is_null()
            {
                return Err("mirror_acquire_failed");
            }
            self.mirror = Some(mirror);
            let (rgba, size) = self
                .capture_device
                .as_ref()
                .unwrap()
                .read(self.mirror.as_ref().unwrap().view)?;
            let (head, after_pid, after_origin) = self.tracking()?;
            if pid != after_pid || origin != after_origin || !geometry::stable(before, head) {
                return Err("capture_tracking_changed");
            }
            let mut raw = [0.; 4];
            let s = &*self.system;
            s.GetProjectionRaw.ok_or("projection_api_missing")?(
                0,
                &mut raw[0],
                &mut raw[1],
                &mut raw[2],
                &mut raw[3],
            );
            let projection = [raw[0], raw[1], -raw[3], -raw[2]];
            let eye = s.GetEyeToHeadTransform.ok_or("projection_api_missing")?(0).m;
            let quad = geometry::project(world, geometry::compose(head, eye), projection, size)?;
            let (rgb, output) = geometry::rectify(&rgba, size, quad)?;
            let hex = rgb.iter().map(|v| format!("{v:02x}")).collect::<String>();
            let timestamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|_| "clock_unavailable")?
                .as_millis();
            Ok(
                json!({"type":"capture","rgb_hex":hex,"size":output,"metadata":{"head_pose":head,"eye_to_head":eye,"projection":projection,"world_corners":world,"image_quad":quad,"eye_size":size,"scene_pid":pid,"origin":origin,"captured_at_ms":timestamp,"eye":"left","synchronization":"best_effort_pose_guarded"}}),
            )
        }
    }
}
impl Drop for Vr {
    fn drop(&mut self) {
        self.hide_all();
        self.mirror = None;
        unsafe {
            if let Some(destroy) = (*self.overlay).DestroyOverlay {
                if self.panel != 0 {
                    destroy(self.panel);
                }
                if self.frame != 0 {
                    destroy(self.frame);
                }
            }
        }
    }
}
