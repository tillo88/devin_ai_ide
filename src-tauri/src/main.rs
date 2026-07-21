#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

static CLOSE_CLEANUP_SENT: AtomicBool = AtomicBool::new(false);
// FASE 2 packaging (2026-07-21): backend avviato come sidecar dall'app.
// Se lo abbiamo avviato noi, alla chiusura va spento; se era gia' attivo
// (dev con devin-tauri-dev.ps1, o backend gia' in esecuzione) non lo tocchiamo.
static SPAWNED_BACKEND: Mutex<Option<Child>> = Mutex::new(None);

fn backend_reachable() -> bool {
    let address: SocketAddr = match "127.0.0.1:5000".parse() {
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

fn spawn_backend_if_needed() {
    if backend_reachable() {
        println!("[sidecar] backend gia' attivo su 127.0.0.1:5000: non avvio nulla");
        return;
    }
    let exe = match find_backend_exe() {
        Some(value) => value,
        None => {
            eprintln!(
                "[sidecar] devin-backend.exe non trovato (DEVIN_BACKEND_EXE o accanto \
                 all'app): avvia il backend manualmente o usa il launcher dev"
            );
            return;
        }
    };
    println!("[sidecar] avvio backend: {}", exe.display());
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
            // Cold start PyInstaller: anche 10-20s. Aspettiamo (max 40s) per
            // far arrivare la finestra su una UI gia' viva.
            for _ in 0..80 {
                if backend_reachable() {
                    println!("[sidecar] backend pronto");
                    return;
                }
                std::thread::sleep(Duration::from_millis(500));
            }
            eprintln!("[sidecar] backend non pronto entro 40s: la UI ritentera' da sola");
        }
        Err(err) => eprintln!("[sidecar] avvio backend fallito: {err}"),
    }
}

fn stop_spawned_backend() {
    if let Some(mut child) = SPAWNED_BACKEND.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn post_desktop_close_cleanup() {
    let address: SocketAddr = match "127.0.0.1:5000".parse() {
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
    spawn_backend_if_needed();
    tauri::Builder::default()
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
