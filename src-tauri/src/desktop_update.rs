//! Avviso di aggiornamento dell'app desktop.
//!
//! L'interfaccia e' compilata dentro l'app (non si carica codice remoto dal
//! rig), quindi ogni modifica alla UI arriva solo con una nuova build. Le build
//! le fa GitHub Actions a ogni merge su main e le pubblica come release
//! `desktop-vX.Y.Z` del repo. Qui l'app confronta la propria versione con
//! l'ultima release e, se e' indietro, apre il download dell'installer nel
//! browser predefinito: nessun eseguibile scaricato o lanciato dall'app.
//!
//! Gli URL sono costanti: dalla risposta di GitHub si accetta solo un asset
//! sotto il prefisso di download di questo repo.

use serde::Serialize;
use serde_json::Value;
use std::time::Duration;

const RELEASES_API: &str = "https://api.github.com/repos/tillo88/devin_ai_ide/releases?per_page=20";
const DOWNLOAD_PREFIX: &str = "https://github.com/tillo88/devin_ai_ide/releases/download/desktop-v";
const TAG_PREFIX: &str = "desktop-v";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AvailableUpdate {
    pub version: String,
    pub installer_url: String,
    pub notes: String,
}

#[derive(Debug, Serialize)]
pub struct UpdateStatus {
    schema: &'static str,
    current: String,
    available: Option<AvailableUpdate>,
    error: Option<String>,
}

fn parse_version(text: &str) -> Option<(u64, u64, u64)> {
    let mut parts = text.trim().split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next()?.parse().ok()?;
    let patch = parts.next()?.parse().ok()?;
    if parts.next().is_some() {
        return None;
    }
    Some((major, minor, patch))
}

/// La release piu' nuova di quella in uso, se c'e'. Pura: i test la provano
/// con risposte di GitHub finte.
pub fn pick_update(current: &str, releases: &Value) -> Option<AvailableUpdate> {
    let current = parse_version(current)?;
    let mut best: Option<((u64, u64, u64), AvailableUpdate)> = None;
    for release in releases.as_array()? {
        if release["draft"].as_bool().unwrap_or(true)
            || release["prerelease"].as_bool().unwrap_or(true)
        {
            continue;
        }
        let Some(tag) = release["tag_name"].as_str() else {
            continue;
        };
        let Some(version_text) = tag.strip_prefix(TAG_PREFIX) else {
            continue;
        };
        let Some(version) = parse_version(version_text) else {
            continue;
        };
        if version <= current || best.as_ref().is_some_and(|(seen, _)| version <= *seen) {
            continue;
        }
        let expected_prefix = format!("{DOWNLOAD_PREFIX}{version_text}/");
        let installer = release["assets"].as_array().and_then(|assets| {
            assets.iter().find_map(|asset| {
                let name = asset["name"].as_str()?;
                let url = asset["browser_download_url"].as_str()?;
                (name.ends_with("-setup.exe")
                    && url.starts_with(&expected_prefix)
                    && !url.contains(['"', '\'', ' ', '&', '^', '|', '<', '>']))
                .then(|| url.to_string())
            })
        });
        let Some(installer_url) = installer else {
            continue;
        };
        let notes: String = release["body"]
            .as_str()
            .unwrap_or("")
            .chars()
            .take(600)
            .collect();
        best = Some((
            version,
            AvailableUpdate {
                version: version_text.to_string(),
                installer_url,
                notes,
            },
        ));
    }
    best.map(|(_, update)| update)
}

fn fetch_releases() -> Result<Value, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(10))
        .user_agent("devin-ai-ide-desktop")
        .build()
        .map_err(|err| format!("client HTTP non disponibile: {err}"))?;
    let response = client
        .get(RELEASES_API)
        .header("Accept", "application/vnd.github+json")
        .send()
        .map_err(|err| format!("GitHub non raggiungibile: {err}"))?;
    if !response.status().is_success() {
        return Err(format!("GitHub ha risposto {}", response.status()));
    }
    response
        .json()
        .map_err(|err| format!("risposta GitHub non valida: {err}"))
}

fn current_version(app: &tauri::AppHandle) -> String {
    app.package_info().version.to_string()
}

#[tauri::command]
pub async fn desktop_update_check(app: tauri::AppHandle) -> UpdateStatus {
    let current = current_version(&app);
    let probe = current.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        fetch_releases().map(|releases| pick_update(&probe, &releases))
    })
    .await
    .unwrap_or_else(|err| Err(format!("controllo interrotto: {err}")));
    match result {
        Ok(available) => UpdateStatus {
            schema: "devin_desktop_update_v1",
            current,
            available,
            error: None,
        },
        Err(error) => UpdateStatus {
            schema: "devin_desktop_update_v1",
            current,
            available: None,
            error: Some(error),
        },
    }
}

/// Apre nel browser il download dell'installer dell'ultima versione. L'URL
/// viene ricalcolato qui, non arriva dalla pagina.
#[tauri::command]
pub async fn desktop_update_open(app: tauri::AppHandle) -> Result<String, String> {
    let current = current_version(&app);
    let update = tauri::async_runtime::spawn_blocking(move || {
        fetch_releases().map(|releases| pick_update(&current, &releases))
    })
    .await
    .map_err(|err| format!("controllo interrotto: {err}"))??
    .ok_or_else(|| "Nessuna versione piu' nuova disponibile".to_string())?;
    open_in_browser(&update.installer_url)?;
    Ok(update.version)
}

fn open_in_browser(url: &str) -> Result<(), String> {
    if !url.starts_with(DOWNLOAD_PREFIX) {
        return Err("URL di aggiornamento rifiutato".to_string());
    }
    #[cfg(target_os = "windows")]
    let spawned = std::process::Command::new("explorer.exe").arg(url).spawn();
    #[cfg(not(target_os = "windows"))]
    let spawned = std::process::Command::new("xdg-open").arg(url).spawn();
    spawned
        .map(|_| ())
        .map_err(|err| format!("browser non avviato: {err}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn release(tag: &str, asset: &str, url: &str) -> Value {
        json!({"tag_name": tag, "draft": false, "prerelease": false, "body": "note",
               "assets": [{"name": asset, "browser_download_url": url}]})
    }

    fn ok(v: &str) -> Value {
        release(
            &format!("desktop-v{v}"),
            &format!("DEVIN AI IDE_{v}_x64-setup.exe"),
            &format!("{DOWNLOAD_PREFIX}{v}/DEVIN.AI.IDE_{v}_x64-setup.exe"),
        )
    }

    #[test]
    fn sceglie_la_piu_nuova_e_ignora_le_vecchie() {
        let releases = json!([ok("0.2.3"), ok("0.2.12"), ok("0.2.9"), ok("0.1.99")]);
        let update = pick_update("0.2.5", &releases).expect("update");
        assert_eq!(update.version, "0.2.12");
        assert!(update.installer_url.ends_with("0.2.12_x64-setup.exe"));
    }

    #[test]
    fn nessun_aggiornamento_se_gia_alla_pari() {
        assert_eq!(
            pick_update("0.2.12", &json!([ok("0.2.12"), ok("0.2.3")])),
            None
        );
    }

    #[test]
    fn bozze_prerelease_e_altri_tag_scartati() {
        let mut draft = ok("0.3.0");
        draft["draft"] = json!(true);
        let mut pre = ok("0.3.1");
        pre["prerelease"] = json!(true);
        let other = release(
            "clippy-memory-images-v1",
            "x-setup.exe",
            &format!("{DOWNLOAD_PREFIX}0.9.0/x-setup.exe"),
        );
        assert_eq!(pick_update("0.2.0", &json!([draft, pre, other])), None);
    }

    #[test]
    fn asset_fuori_dal_repo_o_con_versione_diversa_rifiutato() {
        let foreign = release(
            "desktop-v0.4.0",
            "evil-setup.exe",
            "https://evil.example/desktop-v0.4.0/evil-setup.exe",
        );
        let mismatched = release(
            "desktop-v0.4.1",
            "a-setup.exe",
            &format!("{DOWNLOAD_PREFIX}0.9.9/a-setup.exe"),
        );
        let injected = release(
            "desktop-v0.4.2",
            "a-setup.exe",
            &format!("{DOWNLOAD_PREFIX}0.4.2/a\"&calc-setup.exe"),
        );
        let msi_only = release(
            "desktop-v0.4.3",
            "a.msi",
            &format!("{DOWNLOAD_PREFIX}0.4.3/a.msi"),
        );
        assert_eq!(
            pick_update("0.2.0", &json!([foreign, mismatched, injected, msi_only])),
            None
        );
    }

    #[test]
    fn versione_corrente_illeggibile_non_propone_nulla() {
        assert_eq!(pick_update("dev", &json!([ok("0.2.1")])), None);
        assert_eq!(parse_version("0.2.1.4"), None);
        assert_eq!(parse_version("0.2.10"), Some((0, 2, 10)));
    }

    #[test]
    fn apertura_rifiuta_url_estranei() {
        assert!(open_in_browser("https://evil.example/x-setup.exe").is_err());
    }
}
