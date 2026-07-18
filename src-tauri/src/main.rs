#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

static CLOSE_CLEANUP_SENT: AtomicBool = AtomicBool::new(false);

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
    tauri::Builder::default()
        .on_window_event(|window, event| {
            if window.label() == "main" && matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if !CLOSE_CLEANUP_SENT.swap(true, Ordering::SeqCst) {
                    post_desktop_close_cleanup();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running DEVIN AI IDE desktop shell");
}
