#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// DEVIN Desktop is a thin client for the authenticated, always-on front door
// on the rig. The backend, workspaces and model lifecycle all remain on the
// rig; this process only validates local connection settings and navigates the
// native webview. No local backend or model is spawned, and closing the window
// does not stop a remote session (the front door owns its idle policy).

use std::fs;
use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::time::Duration;

use serde_json::Value;
use tauri::Manager;

const CONFIG_SCHEMA: &str = "devin_desktop_frontdoor_v1";
const CONFIG_FILE: &str = "desktop.json";
const CONNECT_TIMEOUT: Duration = Duration::from_millis(1200);

#[derive(Debug)]
struct DesktopConfig {
    frontdoor_url: tauri::Url,
    access_token: String,
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

fn validate_token(raw: &str) -> Result<String, String> {
    if !(32..=256).contains(&raw.len()) {
        return Err("access_token deve contenere da 32 a 256 caratteri".to_string());
    }
    if raw
        .chars()
        .any(|character| character.is_control() || character.is_whitespace())
    {
        return Err("access_token contiene spazi o caratteri di controllo".to_string());
    }
    Ok(raw.to_string())
}

fn load_config() -> Result<DesktopConfig, String> {
    let path = configured_path()?;
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
    let token = std::env::var("DEVIN_FRONTDOOR_TOKEN")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| optional_string(&document, "access_token"))
        .ok_or_else(|| format!("access_token mancante in {}", path.display()))?;

    Ok(DesktopConfig {
        frontdoor_url: validate_frontdoor_url(&frontdoor)?,
        access_token: validate_token(&token)?,
    })
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

fn access_url(config: &DesktopConfig) -> tauri::Url {
    let mut url = config.frontdoor_url.clone();
    url.set_path("/app");
    url.query_pairs_mut()
        .append_pair("token", &config.access_token);
    url
}

#[tauri::command]
fn connect_frontdoor(app: tauri::AppHandle) -> Result<(), String> {
    let config = load_config()?;
    if !frontdoor_reachable(&config.frontdoor_url) {
        return Err(format!(
            "Frontdoor DEVIN non raggiungibile su {}. Controlla rete, rig e configurazione.",
            config.frontdoor_url.origin().ascii_serialization()
        ));
    }
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "Finestra DEVIN non disponibile".to_string())?;
    window
        .navigate(access_url(&config))
        .map_err(|err| format!("Navigazione verso DEVIN fallita: {err}"))
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![connect_frontdoor])
        .run(tauri::generate_context!())
        .expect("error while running DEVIN AI IDE desktop shell");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn token() -> String {
        "0123456789abcdef0123456789abcdef".to_string()
    }

    #[test]
    fn builds_bootstrap_url_without_string_concatenation() {
        let config = DesktopConfig {
            frontdoor_url: validate_frontdoor_url("http://192.0.2.10:5000").unwrap(),
            access_token: format!("{}+/=", token()),
        };
        let target = access_url(&config);
        assert_eq!(target.path(), "/app");
        assert_eq!(
            target
                .query_pairs()
                .find(|(key, _)| key == "token")
                .map(|(_, value)| value.into_owned()),
            Some(config.access_token)
        );
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
    fn token_rules_match_frontdoor_contract() {
        assert!(validate_token(&token()).is_ok());
        assert!(validate_token("short").is_err());
        assert!(validate_token(&format!("{} bad", token())).is_err());
    }
}
