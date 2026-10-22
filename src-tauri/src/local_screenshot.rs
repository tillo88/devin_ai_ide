//! DEVIN guarda le finestre di un programma che ha avviato sul PC (22/10).
//!
//! `guarda_programma` avvia il programma con le regole di `esegui`, aspetta
//! qualche secondo, fotografa le finestre visibili dei processi del suo job
//! Windows e lo chiude. Le foto tornano al modello come immagini (visione):
//! cosi' DEVIN controlla un'app con interfaccia come farebbe una persona.
//!
//! Solo le finestre dei processi avviati da DEVIN (il job), mai il resto
//! dello schermo. `PrintWindow` con `PW_RENDERFULLCONTENT` funziona anche se
//! la finestra e' coperta; se fallisce si copia il rettangolo dallo schermo.

use base64::Engine;
use serde::Serialize;

pub const MAX_SHOTS: usize = 3;
pub const MAX_SIDE: u32 = 1280;

#[derive(Debug, Serialize, Clone)]
pub struct WindowShot {
    pub title: String,
    pub width: u32,
    pub height: u32,
    pub png_base64: String,
}

/// Riduce un'immagine RGBA finche' il lato maggiore non supera `max_side`,
/// con la media dei pixel di ogni blocco (fattore intero).
pub fn downscale_rgba(width: u32, height: u32, rgba: &[u8], max_side: u32) -> (u32, u32, Vec<u8>) {
    let largest = width.max(height);
    if largest <= max_side || width == 0 || height == 0 {
        return (width, height, rgba.to_vec());
    }
    let factor = largest.div_ceil(max_side);
    let out_w = (width / factor).max(1);
    let out_h = (height / factor).max(1);
    let mut out = vec![0u8; (out_w * out_h * 4) as usize];
    for oy in 0..out_h {
        for ox in 0..out_w {
            let mut sum = [0u32; 4];
            let mut count = 0u32;
            for dy in 0..factor {
                for dx in 0..factor {
                    let x = ox * factor + dx;
                    let y = oy * factor + dy;
                    if x >= width || y >= height {
                        continue;
                    }
                    let i = ((y * width + x) * 4) as usize;
                    for c in 0..4 {
                        sum[c] += rgba[i + c] as u32;
                    }
                    count += 1;
                }
            }
            let o = ((oy * out_w + ox) * 4) as usize;
            for c in 0..4 {
                out[o + c] = (sum[c] / count.max(1)) as u8;
            }
        }
    }
    (out_w, out_h, out)
}

/// GDI consegna BGRA con alfa spesso a zero: diventa RGBA opaco.
pub fn bgra_to_rgba(mut pixels: Vec<u8>) -> Vec<u8> {
    for px in pixels.chunks_exact_mut(4) {
        px.swap(0, 2);
        px[3] = 255;
    }
    pixels
}

pub fn encode_png(width: u32, height: u32, rgba: &[u8]) -> Result<Vec<u8>, String> {
    if rgba.len() != (width as usize) * (height as usize) * 4 {
        return Err("dimensioni dell'immagine incoerenti".to_string());
    }
    let mut out = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut out, width, height);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().map_err(|err| format!("PNG: {err}"))?;
        writer.write_image_data(rgba).map_err(|err| format!("PNG: {err}"))?;
    }
    Ok(out)
}

pub fn shot_from_bgra(title: String, width: u32, height: u32, bgra: Vec<u8>) -> Result<WindowShot, String> {
    let rgba = bgra_to_rgba(bgra);
    let (w, h, small) = downscale_rgba(width, height, &rgba, MAX_SIDE);
    let png = encode_png(w, h, &small)?;
    Ok(WindowShot {
        title,
        width: w,
        height: h,
        png_base64: base64::engine::general_purpose::STANDARD.encode(png),
    })
}

/// I processi del job (il programma e i suoi figli: `py` avvia `python.exe`).
#[cfg(target_os = "windows")]
pub fn job_process_ids(job: isize) -> Vec<u32> {
    use windows_sys::Win32::System::JobObjects::{JobObjectBasicProcessIdList, QueryInformationJobObject};
    // JOBOBJECT_BASIC_PROCESS_ID_LIST: due u32 (in un usize) e poi gli id
    let mut buffer = vec![0usize; 1 + 256];
    let ok = unsafe {
        QueryInformationJobObject(
            job as _,
            JobObjectBasicProcessIdList,
            buffer.as_mut_ptr() as *mut core::ffi::c_void,
            (buffer.len() * std::mem::size_of::<usize>()) as u32,
            std::ptr::null_mut(),
        )
    };
    if ok == 0 {
        return Vec::new();
    }
    let listed = ((buffer[0] as u64) >> 32) as usize;
    buffer[1..1 + listed.min(256)].iter().map(|pid| *pid as u32).collect()
}

#[cfg(target_os = "windows")]
pub fn capture_windows_of(pids: &[u32]) -> Vec<WindowShot> {
    use windows_sys::core::BOOL;
    use windows_sys::Win32::Foundation::{HWND, LPARAM};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetWindowThreadProcessId, IsIconic, IsWindowVisible,
    };

    struct Search {
        pids: Vec<u32>,
        found: Vec<HWND>,
    }
    unsafe extern "system" fn each(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let search = &mut *(lparam as *mut Search);
        let mut pid = 0u32;
        GetWindowThreadProcessId(hwnd, &mut pid);
        if search.pids.contains(&pid) && IsWindowVisible(hwnd) != 0 && IsIconic(hwnd) == 0 {
            search.found.push(hwnd);
        }
        1
    }
    let mut search = Search { pids: pids.to_vec(), found: Vec::new() };
    unsafe {
        EnumWindows(Some(each), &mut search as *mut Search as LPARAM);
    }
    search
        .found
        .into_iter()
        .filter_map(|hwnd| unsafe { capture_window(hwnd) })
        .take(MAX_SHOTS)
        .collect()
}

#[cfg(target_os = "windows")]
unsafe fn capture_window(hwnd: windows_sys::Win32::Foundation::HWND) -> Option<WindowShot> {
    use windows_sys::Win32::Foundation::RECT;
    use windows_sys::Win32::Graphics::Gdi::{
        BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC, GetDIBits,
        ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, SRCCOPY,
    };
    use windows_sys::Win32::Storage::Xps::PrintWindow;
    use windows_sys::Win32::UI::WindowsAndMessaging::{GetWindowRect, GetWindowTextW, PW_RENDERFULLCONTENT};

    let mut rect = RECT { left: 0, top: 0, right: 0, bottom: 0 };
    if GetWindowRect(hwnd, &mut rect) == 0 {
        return None;
    }
    let width = rect.right - rect.left;
    let height = rect.bottom - rect.top;
    if width < 40 || height < 30 || width > 10_000 || height > 10_000 {
        return None; // finestre di servizio o dimensioni assurde
    }
    let mut title_buffer = [0u16; 256];
    let length = GetWindowTextW(hwnd, title_buffer.as_mut_ptr(), title_buffer.len() as i32);
    let title = String::from_utf16_lossy(&title_buffer[..length.max(0) as usize]);

    let screen = GetDC(std::ptr::null_mut());
    if screen.is_null() {
        return None;
    }
    let memory = CreateCompatibleDC(screen);
    let bitmap = CreateCompatibleBitmap(screen, width, height);
    let previous = SelectObject(memory, bitmap);
    if PrintWindow(hwnd, memory, PW_RENDERFULLCONTENT) == 0 {
        BitBlt(memory, 0, 0, width, height, screen, rect.left, rect.top, SRCCOPY);
    }
    let mut info: BITMAPINFO = std::mem::zeroed();
    info.bmiHeader = BITMAPINFOHEADER {
        biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
        biWidth: width,
        biHeight: -height, // dall'alto in basso
        biPlanes: 1,
        biBitCount: 32,
        biCompression: BI_RGB,
        ..std::mem::zeroed()
    };
    let mut pixels = vec![0u8; (width as usize) * (height as usize) * 4];
    let lines = GetDIBits(
        memory,
        bitmap,
        0,
        height as u32,
        pixels.as_mut_ptr() as *mut core::ffi::c_void,
        &mut info,
        DIB_RGB_COLORS,
    );
    SelectObject(memory, previous);
    DeleteObject(bitmap);
    DeleteDC(memory);
    ReleaseDC(std::ptr::null_mut(), screen);
    if lines == 0 {
        return None;
    }
    shot_from_bgra(title, width as u32, height as u32, pixels).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn downscale_keeps_small_images_and_shrinks_large_ones() {
        let small = vec![10u8; 4 * 4 * 4];
        assert_eq!(downscale_rgba(4, 4, &small, 8), (4, 4, small.clone()));
        let (w, h, out) = downscale_rgba(2600, 1000, &vec![200u8; 2600 * 1000 * 4], MAX_SIDE);
        assert!(w <= MAX_SIDE && h <= MAX_SIDE, "{w}x{h}");
        assert_eq!((w, h), (866, 333));
        assert_eq!(out.len(), (w * h * 4) as usize);
        assert!(out.iter().all(|v| *v == 200));
    }

    #[test]
    fn downscale_averages_each_block() {
        // 4x2 -> 2x1 con fattore 2: ogni pixel e' la media del suo blocco 2x2
        let mut px = Vec::new();
        for v in [0u8, 100, 50, 50, 0, 100, 50, 50] {
            px.extend_from_slice(&[v, v, v, 255]);
        }
        let (w, h, out) = downscale_rgba(4, 2, &px, 2);
        assert_eq!((w, h), (2, 1));
        assert_eq!(&out[..4], &[50, 50, 50, 255]);
        assert_eq!(&out[4..], &[50, 50, 50, 255]);
    }

    #[test]
    fn bgra_becomes_opaque_rgba() {
        assert_eq!(bgra_to_rgba(vec![1, 2, 3, 0]), vec![3, 2, 1, 255]);
    }

    #[test]
    fn png_round_trip_and_incoherent_sizes_are_refused() {
        let shot = shot_from_bgra("Finestra".to_string(), 3, 2, vec![9u8; 3 * 2 * 4]).unwrap();
        let png = base64::engine::general_purpose::STANDARD.decode(&shot.png_base64).unwrap();
        assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
        let decoder = png::Decoder::new(std::io::Cursor::new(png));
        let reader = decoder.read_info().unwrap();
        assert_eq!((reader.info().width, reader.info().height), (3, 2));
        assert!(encode_png(3, 2, &[0u8; 5]).is_err());
    }
}
