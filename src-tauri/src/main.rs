#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Visione finale (owner, 2026-07-21):
// - backend principale SUL RIG (Linux, sempre attivo col ruolo DEVIN);
// - questo exe e' il frontend desktop: all'avvio cerca il backend del rig,
//   se c'e' lo usa (zero processi locali), altrimenti avvia il backup
//   locale (devin-backend.exe sidecar, invisibile);
// - la web app resta servita dal rig per l'accesso esterno.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::Manager;

const LOCAL_BACKEND: &str = "127.0.0.1:5000";
const DEFAULT_RIG_BACKEND: &str = "192.168.1.100:5000";

static CLOSE_CLEANUP_SENT: AtomicBool = AtomicBool::new(false);
// Se il backend locale lo abbiamo avviato noi, alla chiusura va spento;
// un backend preesistente (dev o rig) non si tocca mai.
static SPAWNED_BACKEND: Mutex<Option<Child>> = Mutex::new(None);

fn host_reachable(host_port: &str, timeout_ms: u64) -> bool {
    let Ok(mut addrs) = host_port.to_socket_addrs() else {
        return false;
    };
    let Some(address) = addrs.next() else {
        return false;
    };
    TcpStream::connect_timeout(&address, Duration::from_millis(timeout_ms)).is_ok()
}

fn find_backend_exe() -> Option<PathBuf> {
    // Override esplicito (utile in dev per puntare a dist\devin-backend).
    if let Ok(custom) = std::env::var("DEVIN_BACKEND_EXE") {
        let path = PathBuf::from(custom);
        if path.is_file() {
            return Some(path);
        }
    }
    // Layout installato: il bundle backend sta accanto all'exe dell'app.
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

fn spawn_local_backup() {
    let exe = match find_backend_exe() {
        Some(value) => value,
        None => {
            eprintln!(
                "[backend] backup locale non trovato (DEVIN_BACKEND_EXE o accanto \
                 all'app) e rig non raggiungibile: la UI restera' in attesa"
            );
            return;
        }
    };
    println!("[backend] rig giu': avvio backup locale {}", exe.display());
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
    match command.spawn() {
        Ok(child) => {
            *SPAWNED_BACKEND.lock().unwrap() = Some(child);
            // Cold start PyInstaller: anche 10-20s.
            for _ in 0..80 {
                if host_reachable(LOCAL_BACKEND, 400) {
                    println!("[backend] backup locale pronto");
                    return;
                }
                std::thread::sleep(Duration::from_millis(500));
            }
            eprintln!("[backend] backup locale non pronto entro 40s: la UI ritentera' da sola");
        }
        Err(err) => eprintln!("[backend] avvio backup locale fallito: {err}"),
    }
}

fn desktop_config_path() -> Option<PathBuf> {
    let base = std::env::var("APPDATA").ok()?;
    Some(PathBuf::from(base).join("DEVIN").join("desktop.json"))
}

/// rig_url da %APPDATA%\DEVIN\desktop.json (pre-wizard FASE 3, 2026-07-21).
/// Al primo avvio crea il file col default, cosi' l'utente lo puo' editare.
fn load_rig_url_from_config() -> Option<String> {
    let path = desktop_config_path()?;
    if !path.is_file() {
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let _ = std::fs::write(
            &path,
            format!("{{\n  \"rig_url\": \"{DEFAULT_RIG_BACKEND}\"\n}}\n"),
        );
        return None;
    }
    let text = std::fs::read_to_string(&path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    value
        .get("rig_url")?
        .as_str()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Decide quale backend usare e ritorna l'URL /app da caricare nella finestra.
/// Priorita' rig_url: env DEVIN_RIG_URL > desktop.json > default compilato.
fn discover_backend_url() -> String {
    let rig = std::env::var("DEVIN_RIG_URL")
        .ok()
        .or_else(load_rig_url_from_config)
        .unwrap_or_else(|| DEFAULT_RIG_BACKEND.to_string());
    if host_reachable(&rig, 900) {
        println!("[backend] rig attivo ({rig}): uso il rig, niente processi locali");
        return format!("http://{rig}/app");
    }
    if host_reachable(LOCAL_BACKEND, 400) {
        println!("[backend] backend locale gia' attivo: lo uso");
        return format!("http://{LOCAL_BACKEND}/app");
    }
    spawn_local_backup();
    format!("http://{LOCAL_BACKEND}/app")
}

fn stop_spawned_backend() {
    if let Some(mut child) = SPAWNED_BACKEND.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn post_desktop_close_cleanup() {
    // Il cleanup di chiusura riguarda solo il backend LOCALE (modelli in
    // VRAM del PC): il rig e' always-on e non va spento dalla GUI.
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
    let target_url = discover_backend_url();
    tauri::Builder::default()
        .setup(move |app| {
            if let Some(window) = app.get_webview_window("main") {
                if let Ok(url) = tauri::Url::parse(&target_url) {
                    let _ = window.navigate(url);
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == "main" && matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if !CLOSE_CLEANUP_SENT.swap(true, Ordering::SeqCst) {
                    post_desktop_close_cleanup();
                    stop_spawned_backend();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running DEVIN AI IDE desktop shell");
}
