//! La finestrella di DEVIN sempre in primo piano (22/10), come quella di Claude.
//!
//! Quando DEVIN apre e usa un programma, la finestra dell'app DEVIN finisce
//! sotto il programma: il primo uso dal vivo e' rimasto fermo due minuti perche'
//! la richiesta di permesso era li' sotto, e passare alla finestra con Alt+Tab
//! nel momento sbagliato interromperebbe DEVIN. La finestrella sta in un
//! angolo, sopra tutto, senza prendere il focus: dice cosa sta facendo DEVIN,
//! mostra l'ultima foto e porta la richiesta di permesso e il pulsante Ferma.
//!
//! Il contenuto lo manda la finestra principale con gli eventi di Tauri; qui
//! c'e' solo la finestra: crearla, mostrarla senza attivarla, nasconderla e
//! spostarla se copre il punto in cui DEVIN deve cliccare.

use std::sync::atomic::{AtomicIsize, Ordering};

use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::local_workspace::authorize_local_ui;

pub const FLOAT_LABEL: &str = "devin-float";
pub const FLOAT_PAGE: &str = "static/float.html";
pub const FLOAT_WIDTH: f64 = 340.0;
pub const FLOAT_HEIGHT: f64 = 300.0;
pub const FLOAT_MARGIN: i32 = 16;

/// La HWND della finestrella (0 se non c'e'): serve a `local_program` per
/// riconoscerla quando copre il punto di un clic.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
static FLOAT_HWND: AtomicIsize = AtomicIsize::new(0);

#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub fn float_hwnd() -> isize {
    FLOAT_HWND.load(Ordering::SeqCst)
}

/// Un rettangolo dello schermo in pixel fisici.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rect {
    pub left: i32,
    pub top: i32,
    pub width: i32,
    pub height: i32,
}

impl Rect {
    pub fn contains(&self, x: i32, y: i32) -> bool {
        x >= self.left && y >= self.top && x < self.left + self.width && y < self.top + self.height
    }
}

/// I quattro angoli dell'area di lavoro, partendo da quello in basso a destra.
pub fn corners(work: Rect, size: (i32, i32), margin: i32) -> [(i32, i32); 4] {
    let (w, h) = size;
    let right = work.left + work.width - w - margin;
    let bottom = work.top + work.height - h - margin;
    let left = work.left + margin;
    let top = work.top + margin;
    [(right, bottom), (left, bottom), (right, top), (left, top)]
}

/// Il primo angolo in cui la finestrella non copre il punto (x, y).
pub fn corner_away_from(work: Rect, size: (i32, i32), margin: i32, x: i32, y: i32) -> (i32, i32) {
    let all = corners(work, size, margin);
    all.into_iter()
        .find(|(cx, cy)| !Rect { left: *cx, top: *cy, width: size.0, height: size.1 }.contains(x, y))
        .unwrap_or(all[0])
}

#[cfg(target_os = "windows")]
fn show_without_focus(hwnd: isize) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, ShowWindow, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SW_SHOWNOACTIVATE,
    };
    unsafe {
        ShowWindow(hwnd as _, SW_SHOWNOACTIVATE);
        SetWindowPos(hwnd as _, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE);
    }
}

/// Se la finestrella copre (x, y) la sposta in un altro angolo dello stesso
/// schermo. Ritorna true se l'ha spostata.
#[cfg(target_os = "windows")]
pub fn move_away_from(x: i32, y: i32) -> bool {
    use windows_sys::Win32::Foundation::{POINT, RECT};
    use windows_sys::Win32::Graphics::Gdi::{GetMonitorInfoW, MonitorFromPoint, MONITORINFO, MONITOR_DEFAULTTONEAREST};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetWindowRect, SetWindowPos, SWP_NOACTIVATE, SWP_NOSIZE, SWP_NOZORDER,
    };
    let hwnd = float_hwnd();
    if hwnd == 0 {
        return false;
    }
    unsafe {
        let mut rect = RECT { left: 0, top: 0, right: 0, bottom: 0 };
        if GetWindowRect(hwnd as _, &mut rect) == 0 {
            return false;
        }
        let size = (rect.right - rect.left, rect.bottom - rect.top);
        let current = Rect { left: rect.left, top: rect.top, width: size.0, height: size.1 };
        if !current.contains(x, y) {
            return false;
        }
        let monitor = MonitorFromPoint(POINT { x, y }, MONITOR_DEFAULTTONEAREST);
        let mut info: MONITORINFO = std::mem::zeroed();
        info.cbSize = std::mem::size_of::<MONITORINFO>() as u32;
        if GetMonitorInfoW(monitor, &mut info) == 0 {
            return false;
        }
        let w = info.rcWork;
        let work = Rect { left: w.left, top: w.top, width: w.right - w.left, height: w.bottom - w.top };
        let (nx, ny) = corner_away_from(work, size, FLOAT_MARGIN, x, y);
        SetWindowPos(hwnd as _, std::ptr::null_mut(), nx, ny, 0, 0, SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER);
        std::thread::sleep(std::time::Duration::from_millis(60));
        true
    }
}

#[cfg(not(target_os = "windows"))]
#[allow(dead_code)]
pub fn move_away_from(_x: i32, _y: i32) -> bool {
    false
}

fn only_main(window: &WebviewWindow) -> Result<(), String> {
    authorize_local_ui(window)?;
    if window.label() != "main" {
        return Err("la finestrella la comanda solo la finestra principale di DEVIN".to_string());
    }
    Ok(())
}

fn create(app: &AppHandle, main: &WebviewWindow) -> Result<WebviewWindow, String> {
    let float = WebviewWindowBuilder::new(app, FLOAT_LABEL, WebviewUrl::App(FLOAT_PAGE.into()))
        .title("DEVIN")
        .inner_size(FLOAT_WIDTH, FLOAT_HEIGHT)
        .resizable(false)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .focused(false)
        .visible(false)
        .shadow(true)
        .build()
        .map_err(|err| format!("finestrella non creata: {err}"))?;
    // angolo in basso a destra dello schermo su cui sta la finestra principale
    if let Ok(Some(monitor)) = main.current_monitor() {
        let area = monitor.work_area();
        let work = Rect {
            left: area.position.x,
            top: area.position.y,
            width: area.size.width as i32,
            height: area.size.height as i32,
        };
        let scale = monitor.scale_factor();
        let size = ((FLOAT_WIDTH * scale) as i32, (FLOAT_HEIGHT * scale) as i32);
        let (x, y) = corners(work, size, FLOAT_MARGIN)[0];
        let _ = float.set_position(tauri::PhysicalPosition::new(x, y));
    }
    #[cfg(target_os = "windows")]
    if let Ok(hwnd) = float.hwnd() {
        FLOAT_HWND.store(hwnd.0 as isize, Ordering::SeqCst);
    }
    Ok(float)
}

#[tauri::command]
pub async fn show_program_float(app: AppHandle, window: WebviewWindow) -> Result<(), String> {
    only_main(&window)?;
    let float = match app.get_webview_window(FLOAT_LABEL) {
        Some(existing) => existing,
        None => create(&app, &window)?,
    };
    #[cfg(target_os = "windows")]
    if float_hwnd() != 0 {
        show_without_focus(float_hwnd());
        return Ok(());
    }
    float.show().map_err(|err| format!("finestrella non mostrata: {err}"))?;
    Ok(())
}

#[tauri::command]
pub async fn hide_program_float(app: AppHandle, window: WebviewWindow) -> Result<(), String> {
    only_main(&window)?;
    if let Some(float) = app.get_webview_window(FLOAT_LABEL) {
        float.hide().map_err(|err| format!("finestrella non nascosta: {err}"))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const WORK: Rect = Rect { left: 0, top: 0, width: 1920, height: 1040 };

    #[test]
    fn the_float_starts_bottom_right_inside_the_work_area() {
        let [first, ..] = corners(WORK, (340, 300), 16);
        assert_eq!(first, (1920 - 340 - 16, 1040 - 300 - 16));
        let second = corners(Rect { left: -1920, top: 0, width: 1920, height: 1080 }, (340, 300), 16);
        assert_eq!(second[3], (-1920 + 16, 16), "anche su uno schermo a sinistra del principale");
    }

    #[test]
    fn a_click_under_the_float_moves_it_to_another_corner() {
        let size = (340, 300);
        // clic in basso a destra, proprio dove sta la finestrella
        assert_eq!(corner_away_from(WORK, size, 16, 1800, 900), (16, 1040 - 300 - 16));
        // clic in basso a sinistra e in basso a destra non c'entra: resta li'
        assert_eq!(corner_away_from(WORK, size, 16, 100, 900), (1920 - 340 - 16, 1040 - 300 - 16));
        assert!(!Rect { left: 0, top: 0, width: 10, height: 10 }.contains(10, 5), "bordo destro escluso");
        assert!(Rect { left: 0, top: 0, width: 10, height: 10 }.contains(0, 9));
    }
}
