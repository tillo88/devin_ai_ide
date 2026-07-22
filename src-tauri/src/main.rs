#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// App nativa (owner, 2026-07-22):
// - la UI e' BUNDLATA nell'app (frontendDist locale), non piu' una pagina
//   servita dal backend: e' una vera app desktop;
// - il frontend bundlato fa discovery rig-first (rig 192.168.1.100:5000, poi
//   backup locale 127.0.0.1:5000) e, se nessun backend risponde, mostra il
//   prompt "procedere in locale?"; il Si' chiama il comando start_local_backend
//   qui sotto. Nessun avvio automatico del backend locale.
// - il backend principale gira SUL RIG; il PC e' solo il backup d'emergenza.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

const LOCAL_BACKEND: &str = "127.0.0.1:5000";

static CLOSE_CLEANUP_SENT: AtomicBool = AtomicBool::new(false);
// Il backup locale, se avviato da noi (comando start_local_backend), va spento
// alla chiusura. Un backend preesistente (rig o gia' attivo) non si tocca.
static SPAWNED_BACKEND: Mutex<Option<Child>> = Mutex::new(None);

fn backend_reachable() -> bool {
    let address: SocketAddr = match LOCAL_BACKEND.parse() {
        Ok(value) => value,
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&address, Duration::from_millis(400)).is_ok()
}

fn find_backend_exe() -> Option<PathBuf> {
    // Override esplicito (utile in dev per puntare a dist\devin-backend).
    if let Ok(custom) = std::env::var("DEVIN_BACKEND_EXE") {
        let path = PathBuf::from(custom);
        if path.is_file() {
            return Some(path);
        }
    }
    // Layout installato: il bundle backend sta accanto all'exe dell'app
    // (bundle.resources -> devin-backend/).
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for candidate in [
                dir.join("devin-backend").join("devin-backend.exe"),
                dir.join("devin-backend.exe"),
            ] {
                if candidate.is_file() {
                    return Some(candidate);
                }
            }
        }
    }
    None
}

/// Avvia il backend di backup locale (comando invocato dal prompt del frontend
/// quando il rig e' offline e l'utente sceglie "Si', in locale"). Attende la
/// readiness e ritorna l'URL base.
#[tauri::command]
fn start_local_backend() -> Result<String, String> {
    let base = format!("http://{LOCAL_BACKEND}");
    if backend_reachable() {
        return Ok(base);
    }
    let exe = find_backend_exe()
        .ok_or_else(|| "devin-backend.exe non trovato (DEVIN_BACKEND_EXE o accanto all'app)".to_string())?;

    let mut command = Command::new(&exe);
    command.env("DEVIN_NO_BROWSER", "1");
    if let Some(dir) = exe.parent() {
        command.current_dir(dir);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let child = command
        .spawn()
        .map_err(|err| format!("avvio backend fallito: {err}"))?;
    *SPAWNED_BACKEND.lock().unwrap() = Some(child);

    // Cold start PyInstaller: fino a ~40s.
    for _ in 0..80 {
        if backend_reachable() {
            return Ok(base);
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    Err("backend locale non pronto entro 40s".to_string())
}

fn stop_spawned_backend() {
    if let Some(mut child) = SPAWNED_BACKEND.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn post_desktop_close_cleanup() {
    // Il cleanup di chiusura riguarda solo il backend LOCALE (modelli in VRAM
    // del PC): il rig e' always-on e non va spento dalla GUI.
    let address: SocketAddr = match LOCAL_BACKEND.parse() {
        Ok(value) => value,
        Err(_) => return,
    };
    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_millis(700)) {
        Ok(value) => value,
        Err(_) => return,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(700)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(700)));

    let request = concat!(
        "POST /api/desktop/close_cleanup HTTP/1.1\r\n",
        "Host: 127.0.0.1:5000\r\n",
        "Content-Type: application/json\r\n",
        "Content-Length: 2\r\n",
        "Connection: close\r\n",
        "\r\n",
        "{}"
    );
    if stream.write_all(request.as_bytes()).is_ok() {
        let mut buffer = [0_u8; 256];
        let _ = stream.read(&mut buffer);
    }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![start_local_backend])
        .on_window_event(|window, event| {
            if window.label() == "main"
                && matches!(event, tauri::WindowEvent::CloseRequested { .. })
            {
                if !CLOSE_CLEANUP_SENT.swap(true, Ordering::SeqCst) {
                    post_desktop_close_cleanup();
                    stop_spawned_backend();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running DEVIN AI IDE desktop shell");
}
