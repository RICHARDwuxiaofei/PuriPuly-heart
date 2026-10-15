use std::ffi::c_void;
use windows_sys::Win32::Foundation::RECT;
use windows_sys::Win32::Graphics::Gdi::*;

pub const WIDTH: u32 = 768;
pub const HEIGHT: u32 = 1024;

struct Canvas {
    dc: HDC,
    bitmap: HBITMAP,
    font: HFONT,
    old_bitmap: HGDIOBJ,
    old_font: HGDIOBJ,
    pixels: *mut c_void,
}
impl Drop for Canvas {
    fn drop(&mut self) {
        unsafe {
            SelectObject(self.dc, self.old_font);
            SelectObject(self.dc, self.old_bitmap);
            DeleteObject(self.font);
            DeleteObject(self.bitmap);
            DeleteDC(self.dc);
        }
    }
}
impl Canvas {
    fn new() -> Result<Self, &'static str> {
        unsafe {
            let dc = CreateCompatibleDC(std::ptr::null_mut());
            if dc.is_null() {
                return Err("render_dc_failed");
            }
            let mut info = BITMAPINFO::default();
            info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
            info.bmiHeader.biWidth = WIDTH as i32;
            info.bmiHeader.biHeight = -(HEIGHT as i32);
            info.bmiHeader.biPlanes = 1;
            info.bmiHeader.biBitCount = 32;
            info.bmiHeader.biCompression = BI_RGB;
            let mut pixels = std::ptr::null_mut();
            let bitmap = CreateDIBSection(
                dc,
                &info,
                DIB_RGB_COLORS,
                &mut pixels,
                std::ptr::null_mut(),
                0,
            );
            if bitmap.is_null() {
                DeleteDC(dc);
                return Err("render_bitmap_failed");
            }
            let name: Vec<u16> = "Segoe UI\0".encode_utf16().collect();
            let font = CreateFontW(
                28,
                0,
                0,
                0,
                400,
                0,
                0,
                0,
                DEFAULT_CHARSET as u32,
                0,
                0,
                ANTIALIASED_QUALITY as u32,
                0,
                name.as_ptr(),
            );
            if font.is_null() {
                DeleteObject(bitmap);
                DeleteDC(dc);
                return Err("render_font_failed");
            }
            let old_bitmap = SelectObject(dc, bitmap);
            let old_font = SelectObject(dc, font);
            SetBkMode(dc, TRANSPARENT as i32);
            Ok(Self {
                dc,
                bitmap,
                font,
                old_bitmap,
                old_font,
                pixels,
            })
        }
    }
    fn fill(&self, rect: [i32; 4], rgb: [u8; 3]) {
        unsafe {
            let brush =
                CreateSolidBrush(rgb[0] as u32 | ((rgb[1] as u32) << 8) | ((rgb[2] as u32) << 16));
            FillRect(
                self.dc,
                &RECT {
                    left: rect[0],
                    top: rect[1],
                    right: rect[2],
                    bottom: rect[3],
                },
                brush,
            );
            DeleteObject(brush);
        }
    }
    fn text(&self, text: &str, rect: [i32; 4]) {
        unsafe {
            let text: Vec<u16> = text
                .chars()
                .filter(|c| *c != '\0')
                .take(5000)
                .collect::<String>()
                .encode_utf16()
                .collect();
            if text.is_empty() {
                return;
            }
            SetTextColor(self.dc, 0x00f7f3ec);
            let mut rect = RECT {
                left: rect[0],
                top: rect[1],
                right: rect[2],
                bottom: rect[3],
            };
            DrawTextW(
                self.dc,
                text.as_ptr(),
                text.len() as i32,
                &mut rect,
                DT_WORDBREAK | DT_NOPREFIX | DT_END_ELLIPSIS,
            );
        }
    }
}

pub fn panel(
    title: &str,
    status: &str,
    rows: &[crate::state::ChatRow],
    labels: &[String],
    page: usize,
    point: Option<[f32; 2]>,
) -> Result<Vec<u8>, &'static str> {
    let canvas = Canvas::new()?;
    canvas.fill([0, 0, WIDTH as i32, HEIGHT as i32], [22, 24, 31]);
    canvas.text(title, [28, 24, 740, 68]);
    canvas.text(status, [28, 76, 740, 160]);
    let end = rows.len().saturating_sub(page * 3);
    let start = end.saturating_sub(3);
    for (index, row) in rows[start..end].iter().enumerate() {
        let y = 180 + index as i32 * 210;
        let is_self = row.role == "self";
        let x = if is_self { 64 } else { 24 };
        canvas.fill(
            [x, y, x + 680, y + 194],
            if is_self { [42, 65, 76] } else { [64, 48, 72] },
        );
        canvas.text(
            if is_self {
                "SELF"
            } else if row.role == "vision" {
                "VISION"
            } else {
                "PEER"
            },
            [x + 16, y + 8, x + 656, y + 42],
        );
        canvas.text(&row.text, [x + 16, y + 46, x + 656, y + 182]);
    }
    for i in 0..4 {
        let x = 16 + i as i32 * 188;
        canvas.fill([x, 880, x + 180, 1000], [53, 60, 78]);
        canvas.text(
            labels.get(i).map(String::as_str).unwrap_or(""),
            [x + 12, 900, x + 168, 988],
        );
    }
    if let Some([x, y]) = point {
        let (x, y) = ((x * WIDTH as f32) as i32, (y * HEIGHT as f32) as i32);
        canvas.fill([x - 5, y - 5, x + 5, y + 5], [80, 240, 230]);
    }
    let mut rgba = unsafe {
        std::slice::from_raw_parts(
            canvas.pixels.cast::<u8>(),
            WIDTH as usize * HEIGHT as usize * 4,
        )
    }
    .to_vec();
    for pixel in rgba.chunks_exact_mut(4) {
        pixel.swap(0, 2);
        pixel[3] = 255;
    }
    Ok(rgba)
}

pub fn frame() -> Vec<u8> {
    let (w, h) = (512, 320);
    let mut pixels = vec![0; w * h * 4];
    for y in 0..h {
        for x in 0..w {
            if x < 4 || x >= w - 4 || y < 4 || y >= h - 4 {
                pixels[(y * w + x) * 4..(y * w + x) * 4 + 4].copy_from_slice(&[80, 240, 230, 255]);
            }
        }
    }
    pixels
}
