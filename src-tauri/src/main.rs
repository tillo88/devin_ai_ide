#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// DEVIN Desktop is a thin client for the trusted-LAN, always-on front door
// on the rig. The UI remains the local bundle shipped with the Windows app;
// only API traffic crosses the network. This process validates connection
// settings and owns the narrow local-folder agent (picker, reads and
// conflict-checked atomic apply). No local backend or model is spawned, and closing
// the window does not stop a remote session (the front door owns its idle
// policy).

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;
use tauri::Manager;

mod desktop_transport;
mod local_execution;
mod local_workspace;
use desktop_transport::{desktop_http_cancel, desktop_http_stream, DesktopTransportState};
use local_execution::{
    cancel_local_workspace_command, run_local_workspace_command, LocalExecutionState,
};
use local_workspace::{
    apply_local_workspace_changes, apply_local_workspace_plan, local_workspace_context,
    local_workspace_read, local_workspace_tree, select_and_sync_local_workspace,
    sync_local_workspace,
};

const CONFIG_SCHEMA: &str = "devin_desktop_frontdoor_v1";
const CONFIG_FILE: &str = "desktop.json";
const CONNECT_TIMEOUT: Duration = Duration::from_millis(1200);

#[derive(Debug)]
struct DesktopConfig {
    frontdoor_url: tauri::Url,
}

#[derive(Debug, Serialize)]
struct DesktopConfigStatus {
    schema: &'static str,
    configured: bool,
    frontdoor_url: Option<String>,
    managed_by_environment: bool,
    issue: Option<String>,
}

#[derive(Debug, Serialize)]
struct FrontdoorProbe {
    schema: &'static str,
    reachable: bool,
    origin: String,
    detail: &'static str,
}

#[derive(Debug, Serialize)]
struct DesktopConnection {
    schema: &'static str,
    api_base: String,
}

fn configured_path() -> Result<PathBuf, String> {
    if let Some(custom) = std::env::var_os("DEVIN_DESKTOP_CONFIG") {
        let path = PathBuf::from(custom);
        if path.as_os_str().is_empty() {
            return Err("DEVIN_DESKTOP_CONFIG e' vuota".to_string());
        }
        return Ok(path);
    }
    let appdata = std::env::var_os("APPDATA")
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            "APPDATA non disponibile; impossibile trovare la configurazione DEVIN".to_string()
        })?;
    Ok(PathBuf::from(appdata).join("DEVIN").join(CONFIG_FILE))
}

fn optional_string(document: &Value, key: &str) -> Option<String> {
    document
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .filter(|value| !value.is_empty())
}

fn validate_frontdoor_url(raw: &str) -> Result<tauri::Url, String> {
    if raw.trim() != raw {
        return Err("frontdoor_url contiene spazi iniziali o finali".to_string());
    }
    let mut url =
        tauri::Url::parse(raw).map_err(|_| "frontdoor_url non e' un URL valido".to_string())?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err("frontdoor_url deve usare http oppure https".to_string());
    }
    if url.host_str().is_none() {
        return Err("frontdoor_url non contiene un host".to_string());
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("frontdoor_url non puo' contenere credenziali".to_string());
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err("frontdoor_url non puo' contenere query o frammenti".to_string());
    }
    if !matches!(url.path(), "" | "/") {
        return Err("frontdoor_url deve indicare la radice del frontdoor".to_string());
    }
    url.set_path("/");
    Ok(url)
}

fn load_config() -> Result<DesktopConfig, String> {
    let path = configured_path()?;
    reject_symlink(&path, "file")?;
    if let Some(directory) = path.parent() {
        reject_symlink(directory, "directory")?;
    }
    let document = match fs::read_to_string(&path) {
        Ok(raw) => serde_json::from_str::<Value>(&raw).map_err(|err| {
            format!(
                "Configurazione DEVIN non valida ({}): {err}",
                path.display()
            )
        })?,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Value::Null,
        Err(err) => {
            return Err(format!(
                "Impossibile leggere la configurazione DEVIN ({}): {err}",
                path.display()
            ))
        }
    };

    if !document.is_null() && !document.is_object() {
        return Err(format!(
            "La configurazione DEVIN deve essere un oggetto JSON ({})",
            path.display()
        ));
    }
    if let Some(schema) = optional_string(&document, "schema") {
        if schema != CONFIG_SCHEMA {
            return Err(format!(
                "Schema configurazione DEVIN non supportato: {schema} (atteso {CONFIG_SCHEMA})"
            ));
        }
    }

    let frontdoor = std::env::var("DEVIN_FRONTDOOR_URL")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| optional_string(&document, "frontdoor_url"))
        .ok_or_else(|| format!("frontdoor_url mancante in {}", path.display()))?;
    Ok(DesktopConfig {
        frontdoor_url: validate_frontdoor_url(&frontdoor)?,
    })
}

fn environment_override_present() -> bool {
    std::env::var_os("DEVIN_FRONTDOOR_URL").is_some()
}

fn status_snapshot() -> DesktopConfigStatus {
    let managed_by_environment = environment_override_present();
    match load_config() {
        Ok(config) => DesktopConfigStatus {
            schema: "devin_desktop_config_status_v1",
            configured: true,
            frontdoor_url: Some(config.frontdoor_url.origin().ascii_serialization()),
            managed_by_environment,
            issue: None,
        },
        Err(issue) => DesktopConfigStatus {
            schema: "devin_desktop_config_status_v1",
            configured: false,
            frontdoor_url: None,
            managed_by_environment,
            issue: Some(issue),
        },
    }
}

fn frontdoor_reachable(url: &tauri::Url) -> bool {
    let Some(host) = url.host_str() else {
        return false;
    };
    let Some(port) = url.port_or_known_default() else {
        return false;
    };
    let Ok(addresses) = (host, port).to_socket_addrs() else {
        return false;
    };
    addresses
        .take(4)
        .any(|address| TcpStream::connect_timeout(&address, CONNECT_TIMEOUT).is_ok())
}

#[cfg(target_os = "windows")]
fn hide_command_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x0800_0000);
}

#[cfg(not(target_os = "windows"))]
fn hide_command_window(_command: &mut Command) {}

#[cfg(target_os = "windows")]
fn protect_config_directory(directory: &std::path::Path) -> Result<(), String> {
    let mut whoami = Command::new("whoami.exe");
    whoami.args(["/user", "/fo", "csv", "/nh"]);
    hide_command_window(&mut whoami);
    let output = whoami
        .output()
        .map_err(|err| format!("Impossibile identificare l'utente Windows: {err}"))?;
    if !output.status.success() {
        return Err("Impossibile identificare il SID dell'utente Windows".to_string());
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let sid = text
        .split(|character: char| character == ',' || character == '"' || character.is_whitespace())
        .find(|part| part.starts_with("S-1-"))
        .ok_or_else(|| "SID utente Windows non trovato".to_string())?;

    let mut icacls = Command::new("icacls.exe");
    icacls
        .arg(directory)
        .args(["/inheritance:r", "/grant:r"])
        .arg(format!("*{sid}:(OI)(CI)F"))
        .arg("*S-1-5-18:(OI)(CI)F");
    hide_command_window(&mut icacls);
    let output = icacls
        .output()
        .map_err(|err| format!("Impossibile proteggere la configurazione DEVIN: {err}"))?;
    if !output.status.success() {
        return Err(format!(
            "icacls non ha protetto la configurazione DEVIN (exit {})",
            output.status.code().unwrap_or(-1)
        ));
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn protect_config_directory(_directory: &std::path::Path) -> Result<(), String> {
    Ok(())
}

#[cfg(target_os = "windows")]
fn replace_file(source: &std::path::Path, destination: &std::path::Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source_wide: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination_wide: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            source_wide.as_ptr(),
            destination_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
fn replace_file(source: &std::path::Path, destination: &std::path::Path) -> std::io::Result<()> {
    if destination.exists() {
        fs::remove_file(destination)?;
    }
    fs::rename(source, destination)
}

fn reject_symlink(path: &std::path::Path, kind: &str) -> Result<(), String> {
    if fs::symlink_metadata(path)
        .map(|metadata| metadata.file_type().is_symlink())
        .unwrap_or(false)
    {
        return Err(format!(
            "Configurazione DEVIN rifiutata: {kind} e' un collegamento"
        ));
    }
    Ok(())
}

fn persist_config_at(
    path: &std::path::Path,
    config: &DesktopConfig,
    protect_acl: bool,
) -> Result<(), String> {
    reject_symlink(path, "file")?;
    let directory = path
        .parent()
        .ok_or_else(|| "Directory configurazione DEVIN non valida".to_string())?;
    fs::create_dir_all(directory)
        .map_err(|err| format!("Impossibile creare la directory DEVIN: {err}"))?;
    reject_symlink(directory, "directory")?;
    if protect_acl {
        protect_config_directory(directory)?;
    }

    let document = serde_json::json!({
        "schema": CONFIG_SCHEMA,
        "frontdoor_url": config.frontdoor_url.origin().ascii_serialization(),
    });
    let raw = serde_json::to_vec_pretty(&document)
        .map_err(|err| format!("Impossibile serializzare la configurazione DEVIN: {err}"))?;
    let temporary = directory.join(format!(".desktop.{}.tmp", std::process::id()));
    let write_result = (|| -> Result<(), String> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|err| format!("Impossibile creare la configurazione temporanea: {err}"))?;
        file.write_all(&raw)
            .and_then(|_| file.write_all(b"\n"))
            .and_then(|_| file.sync_all())
            .map_err(|err| format!("Impossibile salvare la configurazione DEVIN: {err}"))?;
        replace_file(&temporary, path)
            .map_err(|err| format!("Impossibile pubblicare la configurazione DEVIN: {err}"))?;
        Ok(())
    })();
    if write_result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    write_result
}

fn persist_config(config: &DesktopConfig) -> Result<(), String> {
    let path = configured_path()?;
    persist_config_at(&path, config, true)
}

#[tauri::command]
fn connect_frontdoor(app: tauri::AppHandle) -> Result<DesktopConnection, String> {
    let config = load_config()?;
    if !frontdoor_reachable(&config.frontdoor_url) {
        return Err(format!(
            "Frontdoor DEVIN non raggiungibile su {}. Controlla rete, rig e configurazione.",
            config.frontdoor_url.origin().ascii_serialization()
        ));
    }
    app.get_webview_window("main")
        .ok_or_else(|| "Finestra DEVIN non disponibile".to_string())?;
    Ok(DesktopConnection {
        schema: "devin_desktop_connection_v1",
        api_base: config.frontdoor_url.origin().ascii_serialization(),
    })
}

#[tauri::command]
fn desktop_config_status() -> DesktopConfigStatus {
    status_snapshot()
}

#[tauri::command]
fn test_frontdoor_connection(frontdoor_url: String) -> Result<FrontdoorProbe, String> {
    let url = validate_frontdoor_url(&frontdoor_url)?;
    let reachable = frontdoor_reachable(&url);
    Ok(FrontdoorProbe {
        schema: "devin_desktop_probe_v1",
        reachable,
        origin: url.origin().ascii_serialization(),
        detail: if reachable {
            "frontdoor_tcp_reachable_no_activation"
        } else {
            "frontdoor_tcp_unreachable"
        },
    })
}

#[tauri::command]
fn save_frontdoor_config(frontdoor_url: String) -> Result<DesktopConfigStatus, String> {
    if environment_override_present() {
        return Err(
            "Configurazione gestita da DEVIN_FRONTDOOR_URL; rimuovi l'override prima di salvarla dall'app."
                .to_string(),
        );
    }
    let url = validate_frontdoor_url(&frontdoor_url)?;
    persist_config(&DesktopConfig { frontdoor_url: url })?;
    Ok(status_snapshot())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(DesktopTransportState::new().expect("desktop HTTP transport unavailable"))
        .manage(LocalExecutionState::default())
        .invoke_handler(tauri::generate_handler![
            connect_frontdoor,
            desktop_config_status,
            test_frontdoor_connection,
            save_frontdoor_config,
            desktop_http_stream,
            desktop_http_cancel,
            select_and_sync_local_workspace,
            sync_local_workspace,
            local_workspace_tree,
            local_workspace_read,
            local_workspace_context,
            run_local_workspace_command,
            cancel_local_workspace_command,
            apply_local_workspace_plan,
            apply_local_workspace_changes
        ])
        .run(tauri::generate_context!())
        .expect("error while running DEVIN AI IDE desktop shell");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_local_frontend_connection_without_token_in_url() {
        let config = DesktopConfig {
            frontdoor_url: validate_frontdoor_url("http://192.0.2.10:5000").unwrap(),
        };
        let connection = DesktopConnection {
            schema: "devin_desktop_connection_v1",
            api_base: config.frontdoor_url.origin().ascii_serialization(),
        };
        let serialized = serde_json::to_string(&connection).unwrap();
        assert_eq!(connection.api_base, "http://192.0.2.10:5000");
        assert!(!serialized.contains("access_token"));
        assert!(!serialized.contains("/app?token="));
    }

    #[test]
    fn rejects_ambiguous_or_unsafe_frontdoor_urls() {
        for value in [
            "file:///tmp/devin",
            "http://user:secret@example.test:5000",
            "http://example.test:5000/nested",
            "http://example.test:5000?token=secret",
            " http://example.test:5000",
        ] {
            assert!(validate_frontdoor_url(value).is_err(), "accepted {value}");
        }
    }

    #[test]
    fn status_snapshot_exposes_only_connection_metadata() {
        let status = DesktopConfigStatus {
            schema: "devin_desktop_config_status_v1",
            configured: true,
            frontdoor_url: Some("http://192.0.2.10:5000".to_string()),
            managed_by_environment: false,
            issue: None,
        };
        let serialized = serde_json::to_string(&status).unwrap();
        assert!(serialized.contains("frontdoor_url"));
        assert!(!serialized.contains("access_token"));
    }

    #[test]
    fn persists_config_atomically_without_leaving_temporary_files() {
        let directory = std::env::temp_dir().join(format!(
            "devin-desktop-config-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&directory).unwrap();
        let path = directory.join("desktop.json");
        let config = DesktopConfig {
            frontdoor_url: validate_frontdoor_url("http://192.0.2.10:5000").unwrap(),
        };

        persist_config_at(&path, &config, false).unwrap();
        let document: Value = serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(document["schema"], CONFIG_SCHEMA);
        assert_eq!(document["frontdoor_url"], "http://192.0.2.10:5000");
        assert!(document.get("access_token").is_none());
        assert_eq!(
            fs::read_dir(&directory)
                .unwrap()
                .filter_map(Result::ok)
                .count(),
            1
        );

        fs::remove_dir_all(directory).unwrap();
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn applies_user_and_system_acl_to_a_temporary_directory() {
        let directory = std::env::temp_dir().join(format!(
            "devin-desktop-acl-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&directory).unwrap();
        protect_config_directory(&directory).unwrap();
        fs::write(directory.join("owner-write-check"), b"ok").unwrap();
        fs::remove_dir_all(directory).unwrap();
    }
}
