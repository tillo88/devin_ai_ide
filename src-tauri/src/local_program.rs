//! DEVIN usa le app che prova (22/10): le apre, ci clicca, ci scrive, le chiude.
//!
//! `guarda_programma` apriva, fotografava e chiudeva. Per provare davvero un
//! programma serve anche usarlo: `apri` lo avvia con le regole di `esegui` e lo
//! tiene aperto, `usa` fa clic, scrive, preme tasti o scorre **solo sulle
//! finestre dei processi del suo job** e restituisce una foto nuova, `chiudi`
//! lo termina e restituisce l'output.
//!
//! Il mouse e la tastiera sono quelli veri del PC (SendInput): prima di ogni
//! azione la finestra del programma viene portata in primo piano e si
//! controlla che lo sia davvero; se no l'azione non parte (forse Alessandro
//! sta usando il PC). Le coordinate sono quelle della foto che DEVIN ha visto:
//! si traducono in coordinate dello schermo con la posizione della finestra.
//!
//! Al piu' `MAX_PROGRAMS` programmi aperti; uno inutilizzato da
//! `IDLE_SECONDS` si chiude da solo alla chiamata successiva, e chiudere l'app
//! chiude il job (e con lui i programmi).

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{State, WebviewWindow};
use uuid::Uuid;

use crate::local_execution::{
    capture_stream, configure_environment, hide_command_window, normalized_program,
    project_venv_program, reject_system_install, resolved_command, safe_cwd, terminate_running,
    validate_args, CapturedStream, RunningProcess,
};
use crate::local_screenshot::{WindowPlace, WindowShot};
use crate::local_workspace::{authorize_local_ui, direct_workspace_root};

pub const MAX_PROGRAMS: usize = 3;
pub const IDLE_SECONDS: u64 = 600;
pub const MAX_ACTIONS: usize = 20;
pub const MAX_TEXT: usize = 2000;
pub const MAX_WAIT_MS: u64 = 10_000;
pub const MAX_START_WAIT: u64 = 60;

#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
struct ProgramSession {
    running: RunningProcess,
    last_use: Instant,
    started: Instant,
    stdout: Option<JoinHandle<CapturedStream>>,
    stderr: Option<JoinHandle<CapturedStream>>,
    /// la finestra fotografata l'ultima volta e la sua foto (per le coordinate)
    last_window: Option<isize>,
    last_shot_size: Option<(u32, u32)>,
}

#[derive(Clone, Default)]
pub struct LocalProgramState {
    programs: Arc<Mutex<HashMap<String, ProgramSession>>>,
    /// programmi con azioni in corso -> Ferma premuto mentre agivano
    busy: Arc<Mutex<HashMap<String, bool>>>,
}

impl LocalProgramState {
    fn mark_busy(&self, id: &str) {
        if let Ok(mut busy) = self.busy.lock() {
            busy.insert(id.to_string(), false);
        }
    }

    /// Ferma premuto per un programma che sta agendo? (senza togliere il segno)
    fn stop_requested(&self, id: &str) -> bool {
        self.busy.lock().map(|busy| busy.get(id).copied().unwrap_or(false)).unwrap_or(false)
    }

    /// Fine delle azioni: ritorna true se nel frattempo e' stato premuto Ferma.
    fn end_busy(&self, id: &str) -> bool {
        self.busy.lock().ok().and_then(|mut busy| busy.remove(id)).unwrap_or(false)
    }

    /// Ferma su un programma che sta agendo: si chiudera' appena finisce l'azione.
    fn request_stop(&self, id: &str) -> bool {
        match self.busy.lock() {
            Ok(mut busy) => match busy.get_mut(id) {
                Some(flag) => {
                    *flag = true;
                    true
                }
                None => false,
            },
            Err(_) => false,
        }
    }
}

#[derive(Debug, Serialize, Default)]
pub struct ProgramReceipt {
    program_id: String,
    running: bool,
    exit_code: Option<i32>,
    windows: Vec<String>,
    screenshot: Option<WindowShot>,
    done: Vec<String>,
    error: Option<String>,
    stdout: String,
    stderr: String,
    duration_ms: u128,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(tag = "tipo", rename_all = "lowercase")]
pub enum ProgramAction {
    Clic {
        x: i32,
        y: i32,
        #[serde(default)]
        pulsante: Option<String>,
        #[serde(default)]
        doppio: Option<bool>,
    },
    Scrivi {
        testo: String,
    },
    Tasti {
        tasti: String,
    },
    Scorri {
        x: i32,
        y: i32,
        quanto: i32,
    },
    Aspetta {
        ms: u64,
    },
}

/// Un punto della foto (ridotta) -> lo stesso punto sullo schermo.
pub fn shot_to_screen(x: i32, y: i32, shot: (u32, u32), place: WindowPlace) -> Result<(i32, i32), String> {
    let (shot_w, shot_h) = shot;
    if shot_w == 0 || shot_h == 0 {
        return Err("foto della finestra non valida".to_string());
    }
    if x < 0 || y < 0 || x >= shot_w as i32 || y >= shot_h as i32 {
        return Err(format!("punto ({x}, {y}) fuori dalla foto {shot_w}x{shot_h}"));
    }
    let sx = place.left + ((x as i64 * place.width as i64) / shot_w as i64) as i32;
    let sy = place.top + ((y as i64 * place.height as i64) / shot_h as i64) as i32;
    Ok((sx, sy))
}

/// Coordinate dello schermo -> 0..65535 sul desktop virtuale (SendInput assoluto).
pub fn normalize(value: i32, origin: i32, size: i32) -> i32 {
    if size <= 1 {
        return 0;
    }
    (((value - origin) as i64 * 65535) / (size as i64 - 1)).clamp(0, 65535) as i32
}

/// "ctrl+shift+s", "invio", "tab", "f5" -> codici dei tasti virtuali.
pub fn parse_keys(raw: &str) -> Result<Vec<u16>, String> {
    let mut keys = Vec::new();
    for part in raw.split('+').map(|p| p.trim().to_ascii_lowercase()) {
        let code: u16 = match part.as_str() {
            "ctrl" | "control" => 0x11,
            "shift" | "maiusc" => 0x10,
            "alt" => 0x12,
            "invio" | "enter" | "return" => 0x0D,
            "tab" => 0x09,
            "esc" | "escape" => 0x1B,
            "spazio" | "space" => 0x20,
            "backspace" | "indietro" => 0x08,
            "canc" | "delete" | "del" => 0x2E,
            "su" | "up" => 0x26,
            "giu" | "giù" | "down" => 0x28,
            "sinistra" | "left" => 0x25,
            "destra" | "right" => 0x27,
            "home" | "inizio" => 0x24,
            "end" | "fine" => 0x23,
            "pagsu" | "pageup" => 0x21,
            "paggiu" | "pagedown" => 0x22,
            single if single.len() == 1 && single.chars().all(|c| c.is_ascii_alphanumeric()) => {
                single.to_ascii_uppercase().as_bytes()[0] as u16
            }
            f if f.starts_with('f') && f[1..].parse::<u16>().map(|n| (1..=12).contains(&n)).unwrap_or(false) => {
                0x70 + f[1..].parse::<u16>().unwrap() - 1
            }
            "win" | "windows" => return Err("il tasto Windows non e' consentito".to_string()),
            other => return Err(format!("tasto sconosciuto: {other:?}")),
        };
        keys.push(code);
    }
    if keys.is_empty() || keys.len() > 4 {
        return Err("da 1 a 4 tasti, separati da +".to_string());
    }
    // ctrl+alt+canc e alt+f4 non si mandano: li gestisce Windows, non l'app
    if keys.contains(&0x12) && (keys.contains(&0x2E) || keys.contains(&0x73)) {
        return Err("combinazione riservata a Windows".to_string());
    }
    Ok(keys)
}

pub fn validate_actions(actions: &[ProgramAction]) -> Result<(), String> {
    if actions.is_empty() || actions.len() > MAX_ACTIONS {
        return Err(format!("da 1 a {MAX_ACTIONS} azioni per volta"));
    }
    for action in actions {
        match action {
            ProgramAction::Clic { pulsante, .. } => {
                if !matches!(pulsante.as_deref(), None | Some("sinistro") | Some("destro")) {
                    return Err("pulsante: sinistro o destro".to_string());
                }
            }
            ProgramAction::Scrivi { testo } => {
                if testo.is_empty() || testo.chars().count() > MAX_TEXT {
                    return Err(format!("testo da scrivere vuoto o oltre {MAX_TEXT} caratteri"));
                }
            }
            ProgramAction::Tasti { tasti } => {
                parse_keys(tasti)?;
            }
            ProgramAction::Scorri { quanto, .. } => {
                if *quanto == 0 || quanto.abs() > 20 {
                    return Err("scorri: quanto fra -20 e 20, diverso da 0".to_string());
                }
            }
            ProgramAction::Aspetta { ms } => {
                if *ms > MAX_WAIT_MS {
                    return Err(format!("aspetta al massimo {MAX_WAIT_MS} ms"));
                }
            }
        }
    }
    Ok(())
}

fn job_pids(running: &RunningProcess) -> Vec<u32> {
    #[cfg(target_os = "windows")]
    let mut pids = crate::local_screenshot::job_process_ids(running.job.0);
    #[cfg(not(target_os = "windows"))]
    let mut pids = Vec::new();
    if let Ok(child) = running.child.lock() {
        pids.push(child.id());
    }
    pids
}

/// La finestra principale: quella in primo piano se e' del programma, se no la piu' grande.
#[cfg(target_os = "windows")]
fn main_window(pids: &[u32]) -> Option<isize> {
    use windows_sys::Win32::UI::WindowsAndMessaging::GetForegroundWindow;
    let windows = crate::local_screenshot::windows_of(pids);
    let foreground = unsafe { GetForegroundWindow() } as isize;
    if windows.contains(&foreground) {
        return Some(foreground);
    }
    windows.into_iter().max_by_key(|hwnd| {
        crate::local_screenshot::place_of(*hwnd)
            .map(|p| p.width as i64 * p.height as i64)
            .unwrap_or(0)
    })
}

/// Foto della finestra principale e titoli di tutte quelle del programma.
fn look(session: &mut ProgramSession, receipt: &mut ProgramReceipt) {
    let pids = job_pids(&session.running);
    #[cfg(target_os = "windows")]
    {
        receipt.windows = crate::local_screenshot::windows_of(&pids)
            .into_iter()
            .filter_map(|hwnd| unsafe { crate::local_screenshot::capture_hwnd(hwnd) }.map(|(s, _)| s.title))
            .collect();
        if let Some(hwnd) = main_window(&pids) {
            if let Some((shot, _)) = unsafe { crate::local_screenshot::capture_hwnd(hwnd) } {
                session.last_window = Some(hwnd);
                session.last_shot_size = Some((shot.width, shot.height));
                receipt.screenshot = Some(shot);
            }
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = pids;
        receipt.error.get_or_insert_with(|| "usare le finestre e' possibile solo su Windows".to_string());
    }
}

#[cfg(target_os = "windows")]
fn bring_to_front(hwnd: isize) -> bool {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        BringWindowToTop, GetForegroundWindow, IsIconic, SetForegroundWindow, ShowWindow, SW_RESTORE,
    };
    unsafe {
        if GetForegroundWindow() as isize == hwnd {
            return true;
        }
        if IsIconic(hwnd as _) != 0 {
            ShowWindow(hwnd as _, SW_RESTORE);
        }
        // Windows lascia passare SetForegroundWindow subito dopo un input: un ALT vuoto
        let alt = |flags| INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 { ki: KEYBDINPUT { wVk: 0x12, wScan: 0, dwFlags: flags, time: 0, dwExtraInfo: 0 } },
        };
        let inputs = [alt(0), alt(KEYEVENTF_KEYUP)];
        SendInput(inputs.len() as u32, inputs.as_ptr(), std::mem::size_of::<INPUT>() as i32);
        SetForegroundWindow(hwnd as _);
        BringWindowToTop(hwnd as _);
        std::thread::sleep(Duration::from_millis(150));
        GetForegroundWindow() as isize == hwnd
    }
}

/// Il punto dello schermo deve essere davvero del programma: se sopra c'e' la
/// finestrella di DEVIN la si sposta, se c'e' un'altra finestra il clic non parte.
#[cfg(target_os = "windows")]
fn uncovered(sx: i32, sy: i32, pids: &[u32]) -> Result<(), String> {
    use windows_sys::Win32::Foundation::POINT;
    use windows_sys::Win32::UI::WindowsAndMessaging::{GetAncestor, GetWindowThreadProcessId, WindowFromPoint, GA_ROOT};
    for attempt in 0..2 {
        let (root, pid) = unsafe {
            let at = WindowFromPoint(POINT { x: sx, y: sy });
            let root = GetAncestor(at, GA_ROOT) as isize;
            let mut pid = 0u32;
            GetWindowThreadProcessId(root as _, &mut pid);
            (root, pid)
        };
        if pids.contains(&pid) {
            return Ok(());
        }
        if attempt == 0 && root != 0 && root == crate::local_float::float_hwnd()
            && crate::local_float::move_away_from(sx, sy)
        {
            continue;
        }
        let title = unsafe {
            use windows_sys::Win32::UI::WindowsAndMessaging::GetWindowTextW;
            let mut buffer = [0u16; 256];
            let length = GetWindowTextW(root as _, buffer.as_mut_ptr(), buffer.len() as i32);
            String::from_utf16_lossy(&buffer[..length.max(0) as usize])
        };
        return Err(format!(
            "il punto e' coperto da un'altra finestra («{}»): azione non eseguita, guarda di nuovo il programma",
            if title.is_empty() { "senza titolo" } else { &title }
        ));
    }
    Err("il punto e' coperto da un'altra finestra: azione non eseguita".to_string())
}

#[cfg(target_os = "windows")]
fn send(inputs: &[windows_sys::Win32::UI::Input::KeyboardAndMouse::INPUT]) -> Result<(), String> {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{SendInput, INPUT};
    let sent = unsafe {
        SendInput(inputs.len() as u32, inputs.as_ptr(), std::mem::size_of::<INPUT>() as i32)
    };
    if sent as usize != inputs.len() {
        return Err("Windows ha rifiutato l'input (forse una finestra con privilegi piu' alti)".to_string());
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn perform(session: &ProgramSession, action: &ProgramAction) -> Result<String, String> {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE,
        MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MOVE,
        MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_WHEEL,
        MOUSEINPUT,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
    };

    if let ProgramAction::Aspetta { ms } = action {
        std::thread::sleep(Duration::from_millis(*ms));
        return Ok(format!("aspettato {ms} ms"));
    }
    let hwnd = session.last_window.ok_or("nessuna finestra del programma: prima guardala")?;
    let pids = job_pids(&session.running);
    if !crate::local_screenshot::windows_of(&pids).contains(&hwnd) {
        return Err("la finestra fotografata non c'e' piu': guarda di nuovo il programma".to_string());
    }
    if !bring_to_front(hwnd) {
        return Err("non riesco a mettere la finestra in primo piano (forse Alessandro sta usando il PC): \
                    azione non eseguita"
            .to_string());
    }
    let mouse = |x: i32, y: i32, data: u32, flags: u32| INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 { mi: MOUSEINPUT { dx: x, dy: y, mouseData: data, dwFlags: flags, time: 0, dwExtraInfo: 0 } },
    };
    let key = |vk: u16, scan: u16, flags: u32| INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 { ki: KEYBDINPUT { wVk: vk, wScan: scan, dwFlags: flags, time: 0, dwExtraInfo: 0 } },
    };
    let point = |x: i32, y: i32| -> Result<(i32, i32), String> {
        let place = crate::local_screenshot::place_of(hwnd).ok_or("posizione della finestra non leggibile")?;
        let shot = session.last_shot_size.ok_or("nessuna foto della finestra")?;
        let (sx, sy) = shot_to_screen(x, y, shot, place)?;
        uncovered(sx, sy, &pids)?;
        let (ox, oy, w, h) = unsafe {
            (
                GetSystemMetrics(SM_XVIRTUALSCREEN),
                GetSystemMetrics(SM_YVIRTUALSCREEN),
                GetSystemMetrics(SM_CXVIRTUALSCREEN),
                GetSystemMetrics(SM_CYVIRTUALSCREEN),
            )
        };
        Ok((normalize(sx, ox, w), normalize(sy, oy, h)))
    };
    let absolute = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK | MOUSEEVENTF_MOVE;
    match action {
        ProgramAction::Clic { x, y, pulsante, doppio } => {
            let (nx, ny) = point(*x, *y)?;
            let (down, up) = if pulsante.as_deref() == Some("destro") {
                (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
            } else {
                (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
            };
            let mut inputs = vec![mouse(nx, ny, 0, absolute), mouse(nx, ny, 0, absolute | down), mouse(nx, ny, 0, absolute | up)];
            if doppio.unwrap_or(false) {
                inputs.push(mouse(nx, ny, 0, absolute | down));
                inputs.push(mouse(nx, ny, 0, absolute | up));
            }
            send(&inputs)?;
            Ok(format!("clic {}in ({x}, {y})", if doppio.unwrap_or(false) { "doppio " } else { "" }))
        }
        ProgramAction::Scrivi { testo } => {
            let mut inputs = Vec::new();
            for unit in testo.encode_utf16() {
                inputs.push(key(0, unit, KEYEVENTF_UNICODE));
                inputs.push(key(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
            }
            send(&inputs)?;
            Ok(format!("scritto {} caratteri", testo.chars().count()))
        }
        ProgramAction::Tasti { tasti } => {
            let codes = parse_keys(tasti)?;
            let mut inputs: Vec<INPUT> = codes.iter().map(|vk| key(*vk, 0, 0)).collect();
            inputs.extend(codes.iter().rev().map(|vk| key(*vk, 0, KEYEVENTF_KEYUP)));
            send(&inputs)?;
            Ok(format!("tasti {tasti}"))
        }
        ProgramAction::Scorri { x, y, quanto } => {
            let (nx, ny) = point(*x, *y)?;
            // WHEEL_DELTA = 120 per scatto; positivo = in su
            let delta = (quanto * 120) as u32;
            send(&[mouse(nx, ny, 0, absolute), mouse(nx, ny, delta, absolute | MOUSEEVENTF_WHEEL)])?;
            Ok(format!("scorso di {quanto} in ({x}, {y})"))
        }
        ProgramAction::Aspetta { .. } => unreachable!(),
    }
}

#[cfg(not(target_os = "windows"))]
fn perform(_session: &ProgramSession, action: &ProgramAction) -> Result<String, String> {
    if let ProgramAction::Aspetta { ms } = action {
        std::thread::sleep(Duration::from_millis(*ms));
        return Ok(format!("aspettato {ms} ms"));
    }
    Err("usare le finestre e' possibile solo su Windows".to_string())
}

fn exit_code(session: &ProgramSession) -> Option<i32> {
    session
        .running
        .child
        .lock()
        .ok()
        .and_then(|mut child| child.try_wait().ok().flatten())
        .map(|status| status.code().unwrap_or(-1))
}

fn finish(mut session: ProgramSession, program_id: String) -> ProgramReceipt {
    terminate_running(&session.running);
    if let Ok(mut child) = session.running.child.lock() {
        let _ = child.wait();
    }
    let stdout = session.stdout.take().and_then(|handle| handle.join().ok());
    let stderr = session.stderr.take().and_then(|handle| handle.join().ok());
    ProgramReceipt {
        program_id,
        running: false,
        exit_code: exit_code(&session),
        stdout: stdout.map(|s| s.text).unwrap_or_default(),
        stderr: stderr.map(|s| s.text).unwrap_or_default(),
        duration_ms: session.started.elapsed().as_millis(),
        ..Default::default()
    }
}

/// Chiude i programmi abbandonati (nessuna azione da IDLE_SECONDS).
fn close_idle(state: &LocalProgramState) {
    let expired: Vec<(String, ProgramSession)> = {
        let Ok(mut programs) = state.programs.lock() else { return };
        let keys: Vec<String> = programs
            .iter()
            .filter(|(_, s)| s.last_use.elapsed() > Duration::from_secs(IDLE_SECONDS))
            .map(|(k, _)| k.clone())
            .collect();
        keys.into_iter().filter_map(|k| programs.remove(&k).map(|s| (k, s))).collect()
    };
    for (id, session) in expired {
        finish(session, id);
    }
}

#[allow(clippy::too_many_arguments)]
fn start_blocking(
    state: LocalProgramState,
    root: PathBuf,
    program_id: String,
    display_program: String,
    args: Vec<String>,
    cwd: String,
    wait_seconds: u64,
) -> Result<ProgramReceipt, String> {
    close_idle(&state);
    if state.programs.lock().map_err(|_| "registro programmi non disponibile")?.len() >= MAX_PROGRAMS {
        return Err(format!("gia' {MAX_PROGRAMS} programmi aperti: chiudine uno"));
    }
    let (working_directory, _display_cwd) = safe_cwd(&root, &cwd)?;
    let venv = project_venv_program(&root, &display_program)?;
    let normalized = match &venv {
        Some((kind, _)) => kind.clone(),
        None => normalized_program(&display_program)?,
    };
    validate_args(&normalized, &args)?;
    if venv.is_none() {
        reject_system_install(&normalized, &args)?;
    }
    let (program, resolved_args) = match &venv {
        Some((_, path)) => (path.to_string_lossy().into_owned(), args.clone()),
        None => resolved_command(&normalized, &args),
    };
    let mut command = Command::new(program);
    command
        .args(&resolved_args)
        .current_dir(&working_directory)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_environment(&mut command);
    hide_command_window(&mut command);
    let mut child = command.spawn().map_err(|err| format!("Avvio del programma fallito: {err}"))?;
    let stdout = child.stdout.take().ok_or("stdout non disponibile")?;
    let stderr = child.stderr.take().ok_or("stderr non disponibile")?;
    #[cfg(target_os = "windows")]
    let job = match crate::local_execution::assign_kill_on_close_job(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            return Err(error);
        }
    };
    let running = RunningProcess {
        child: Arc::new(Mutex::new(child)),
        #[cfg(target_os = "windows")]
        job,
    };
    let mut session = ProgramSession {
        running,
        last_use: Instant::now(),
        started: Instant::now(),
        stdout: Some(std::thread::spawn(move || capture_stream(stdout))),
        stderr: Some(std::thread::spawn(move || capture_stream(stderr))),
        last_window: None,
        last_shot_size: None,
    };
    let deadline = Instant::now() + Duration::from_secs(wait_seconds.clamp(1, MAX_START_WAIT));
    while Instant::now() < deadline {
        if exit_code(&session).is_some() {
            // finito prima di aprirsi: si restituisce tutto, non resta aperto
            return Ok(finish(session, program_id));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let mut receipt = ProgramReceipt { program_id: program_id.clone(), running: true, ..Default::default() };
    look(&mut session, &mut receipt);
    receipt.duration_ms = session.started.elapsed().as_millis();
    state
        .programs
        .lock()
        .map_err(|_| "registro programmi non disponibile")?
        .insert(program_id, session);
    Ok(receipt)
}

fn act_blocking(
    state: LocalProgramState,
    program_id: String,
    actions: Vec<ProgramAction>,
    wait_ms: u64,
) -> Result<ProgramReceipt, String> {
    close_idle(&state);
    validate_actions(&actions)?;
    let mut session = state
        .programs
        .lock()
        .map_err(|_| "registro programmi non disponibile")?
        .remove(&program_id)
        .ok_or("programma non aperto (o chiuso perche' inutilizzato da 10 minuti)")?;
    state.mark_busy(&program_id);
    let mut receipt = ProgramReceipt { program_id: program_id.clone(), running: true, ..Default::default() };
    for action in &actions {
        if exit_code(&session).is_some() || state.stop_requested(&program_id) {
            break;
        }
        match perform(&session, action) {
            Ok(done) => receipt.done.push(done),
            Err(error) => {
                receipt.error = Some(error);
                break;
            }
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    if !state.stop_requested(&program_id) {
        std::thread::sleep(Duration::from_millis(wait_ms.min(MAX_WAIT_MS)));
    }
    if state.end_busy(&program_id) {
        let done = std::mem::take(&mut receipt.done);
        let mut finished = finish(session, program_id);
        finished.done = done;
        finished.error = Some("fermato da Alessandro (Ferma): programma chiuso".to_string());
        return Ok(finished);
    }
    if exit_code(&session).is_some() {
        let done = std::mem::take(&mut receipt.done);
        let error = receipt.error.take();
        let mut finished = finish(session, program_id);
        finished.done = done;
        finished.error = error.or_else(|| Some("il programma si e' chiuso".to_string()));
        return Ok(finished);
    }
    session.last_use = Instant::now();
    look(&mut session, &mut receipt);
    receipt.duration_ms = session.started.elapsed().as_millis();
    state
        .programs
        .lock()
        .map_err(|_| "registro programmi non disponibile")?
        .insert(program_id, session);
    Ok(receipt)
}

fn stop_blocking(state: LocalProgramState, program_id: String) -> Result<ProgramReceipt, String> {
    let session = state
        .programs
        .lock()
        .map_err(|_| "registro programmi non disponibile")?
        .remove(&program_id);
    match session {
        Some(session) => Ok(finish(session, program_id)),
        // Ferma mentre il programma sta agendo: lo chiude act_blocking appena finisce l'azione
        None if state.request_stop(&program_id) => Ok(ProgramReceipt {
            program_id,
            running: true,
            error: Some("chiusura chiesta: si chiude appena finisce l'azione in corso".to_string()),
            ..Default::default()
        }),
        None => Err("programma non aperto".to_string()),
    }
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub async fn start_local_program(
    window: WebviewWindow,
    state: State<'_, LocalProgramState>,
    bridge_id: String,
    program_id: String,
    program: String,
    args: Vec<String>,
    cwd: Option<String>,
    wait_seconds: Option<u64>,
) -> Result<ProgramReceipt, String> {
    authorize_local_ui(&window)?;
    Uuid::parse_str(&program_id).map_err(|_| "program_id non valido".to_string())?;
    let root = direct_workspace_root(&bridge_id)?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        start_blocking(state, root, program_id, program, args, cwd.unwrap_or_default(), wait_seconds.unwrap_or(5))
    })
    .await
    .map_err(|err| format!("Worker del programma fallito: {err}"))?
}

#[tauri::command]
pub async fn act_local_program(
    window: WebviewWindow,
    state: State<'_, LocalProgramState>,
    program_id: String,
    actions: Vec<ProgramAction>,
    wait_ms: Option<u64>,
) -> Result<ProgramReceipt, String> {
    authorize_local_ui(&window)?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || act_blocking(state, program_id, actions, wait_ms.unwrap_or(500)))
        .await
        .map_err(|err| format!("Worker del programma fallito: {err}"))?
}

#[tauri::command]
pub async fn stop_local_program(
    window: WebviewWindow,
    state: State<'_, LocalProgramState>,
    program_id: String,
) -> Result<ProgramReceipt, String> {
    authorize_local_ui(&window)?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || stop_blocking(state, program_id))
        .await
        .map_err(|err| format!("Worker del programma fallito: {err}"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn photo_points_become_screen_points() {
        let place = WindowPlace { left: 100, top: 50, width: 1936, height: 1096 };
        // foto ridotta della meta'
        assert_eq!(shot_to_screen(0, 0, (968, 548), place).unwrap(), (100, 50));
        assert_eq!(shot_to_screen(484, 274, (968, 548), place).unwrap(), (100 + 968, 50 + 548));
        assert!(shot_to_screen(968, 10, (968, 548), place).is_err(), "fuori dalla foto");
        assert!(shot_to_screen(-1, 10, (968, 548), place).is_err());
        assert_eq!(normalize(0, 0, 1920), 0);
        assert_eq!(normalize(1919, 0, 1920), 65535);
        assert_eq!(normalize(-1920, -1920, 3840), 0, "monitor a sinistra del principale");
    }

    #[test]
    fn keys_are_parsed_and_dangerous_ones_refused() {
        assert_eq!(parse_keys("ctrl+s").unwrap(), vec![0x11, b'S' as u16]);
        assert_eq!(parse_keys("Invio").unwrap(), vec![0x0D]);
        assert_eq!(parse_keys("shift+tab").unwrap(), vec![0x10, 0x09]);
        assert_eq!(parse_keys("f5").unwrap(), vec![0x74]);
        assert!(parse_keys("win+r").is_err());
        assert!(parse_keys("ctrl+alt+canc").is_err());
        assert!(parse_keys("alt+f4").is_err());
        assert!(parse_keys("tastomagico").is_err());
        assert!(parse_keys("a+b+c+d+e").is_err());
    }

    #[test]
    fn actions_are_validated_before_anything_moves() {
        let ok: Vec<ProgramAction> = serde_json::from_str(
            r#"[{"tipo":"clic","x":10,"y":20},{"tipo":"scrivi","testo":"ciao"},{"tipo":"tasti","tasti":"invio"},
                {"tipo":"scorri","x":5,"y":5,"quanto":-3},{"tipo":"aspetta","ms":300}]"#,
        )
        .unwrap();
        assert!(validate_actions(&ok).is_ok());
        assert!(validate_actions(&[]).is_err());
        assert!(validate_actions(&vec![ProgramAction::Aspetta { ms: 1 }; MAX_ACTIONS + 1]).is_err());
        assert!(validate_actions(&[ProgramAction::Aspetta { ms: MAX_WAIT_MS + 1 }]).is_err());
        assert!(validate_actions(&[ProgramAction::Scrivi { testo: "x".repeat(MAX_TEXT + 1) }]).is_err());
        assert!(validate_actions(&[ProgramAction::Tasti { tasti: "win+r".into() }]).is_err());
        assert!(validate_actions(&[ProgramAction::Clic { x: 1, y: 1, pulsante: Some("centrale".into()), doppio: None }]).is_err());
        assert!(serde_json::from_str::<ProgramAction>(r#"{"tipo":"formatta_disco"}"#).is_err());
    }

    #[test]
    fn a_program_is_opened_kept_running_and_closed() {
        let root = std::env::temp_dir().join(format!("devin-program-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("app.js"), "console.log('pronta'); setTimeout(() => {}, 60000);\n").unwrap();
        std::fs::write(root.join("breve.js"), "console.log('fatto');\n").unwrap();
        let state = LocalProgramState::default();
        let id = Uuid::new_v4().to_string();
        let started = Instant::now();
        let receipt = start_blocking(state.clone(), root.clone(), id.clone(), "node".into(), vec!["app.js".into()], String::new(), 1).unwrap();
        assert!(receipt.running && started.elapsed() < Duration::from_secs(10));
        assert_eq!(state.programs.lock().unwrap().len(), 1, "resta aperto per le azioni");
        let waited = act_blocking(state.clone(), id.clone(), vec![ProgramAction::Aspetta { ms: 50 }], 0).unwrap();
        assert_eq!(waited.done, vec!["aspettato 50 ms".to_string()]);
        #[cfg(not(target_os = "windows"))]
        {
            let refused = act_blocking(state.clone(), id.clone(), vec![ProgramAction::Tasti { tasti: "invio".into() }], 0).unwrap();
            assert!(refused.error.unwrap().contains("solo su Windows"));
        }
        let closed = stop_blocking(state.clone(), id.clone()).unwrap();
        assert!(!closed.running && closed.stdout.contains("pronta"));
        assert!(state.programs.lock().unwrap().is_empty());
        assert!(act_blocking(state.clone(), id, vec![ProgramAction::Aspetta { ms: 1 }], 0).is_err(), "chiuso e' chiuso");

        // un programma che finisce subito non resta aperto
        let short = start_blocking(state.clone(), root.clone(), Uuid::new_v4().to_string(), "node".into(), vec!["breve.js".into()], String::new(), 3).unwrap();
        assert!(!short.running && short.stdout.contains("fatto") && short.exit_code == Some(0));
        assert!(state.programs.lock().unwrap().is_empty());

        // al piu' MAX_PROGRAMS aperti
        let ids: Vec<String> = (0..MAX_PROGRAMS).map(|_| Uuid::new_v4().to_string()).collect();
        for pid in &ids {
            start_blocking(state.clone(), root.clone(), pid.clone(), "node".into(), vec!["app.js".into()], String::new(), 1).unwrap();
        }
        assert!(start_blocking(state.clone(), root.clone(), Uuid::new_v4().to_string(), "node".into(), vec!["app.js".into()], String::new(), 1).is_err());
        for pid in ids {
            stop_blocking(state.clone(), pid).unwrap();
        }
        // le stesse regole di esegui
        assert!(start_blocking(state.clone(), root.clone(), Uuid::new_v4().to_string(), "powershell".into(), vec![], String::new(), 1).is_err());
        assert!(start_blocking(state, root.clone(), Uuid::new_v4().to_string(), "python".into(), vec!["-m".into(), "pip".into(), "install".into(), "x".into()], String::new(), 1).is_err());
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn ferma_while_acting_closes_the_program_after_the_current_action() {
        let root = std::env::temp_dir().join(format!("devin-program-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("app.js"), "console.log('pronta'); setTimeout(() => {}, 60000);\n").unwrap();
        let state = LocalProgramState::default();
        let id = Uuid::new_v4().to_string();
        start_blocking(state.clone(), root.clone(), id.clone(), "node".into(), vec!["app.js".into()], String::new(), 1).unwrap();
        let acting = {
            let (state, id) = (state.clone(), id.clone());
            std::thread::spawn(move || {
                act_blocking(state, id, vec![ProgramAction::Aspetta { ms: 400 }, ProgramAction::Aspetta { ms: 5000 }], 5000)
            })
        };
        std::thread::sleep(Duration::from_millis(150));
        // Ferma premuto mentre agisce: prima diceva "programma non aperto" e il programma restava li'
        let asked = stop_blocking(state.clone(), id.clone()).unwrap();
        assert!(asked.running && asked.error.unwrap().contains("si chiude appena finisce"));
        let started = Instant::now();
        let receipt = acting.join().unwrap().unwrap();
        assert!(started.elapsed() < Duration::from_secs(3), "non aspetta le azioni successive");
        assert!(!receipt.running && receipt.error.unwrap().contains("fermato da Alessandro"));
        assert_eq!(receipt.done, vec!["aspettato 400 ms".to_string()]);
        assert!(state.programs.lock().unwrap().is_empty() && state.busy.lock().unwrap().is_empty());
        assert!(stop_blocking(state, id).is_err(), "chiuso davvero");
        std::fs::remove_dir_all(&root).unwrap();
    }
}
