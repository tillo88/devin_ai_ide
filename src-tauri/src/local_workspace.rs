use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{Cursor, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, WebviewWindow};
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;
use walkdir::{DirEntry, WalkDir};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipArchive, ZipWriter};

use super::{
    configured_path, load_config, protect_config_directory, reject_symlink, replace_file,
    DesktopConfig,
};

const REGISTRY_SCHEMA: &str = "devin_local_workspace_registry_v1";
const SNAPSHOT_SCHEMA: &str = "devin_local_workspace_snapshot_v1";
const EXPORT_SCHEMA: &str = "devin_local_workspace_export_v1";
const REGISTRY_FILE: &str = "local-workspaces.json";
const MAX_TOTAL_BYTES: u64 = 100 * 1024 * 1024;
const MAX_FILE_BYTES: u64 = 30 * 1024 * 1024;
const MAX_FILES: usize = 10_000;

#[derive(Debug, Default, Serialize, Deserialize)]
struct LocalRegistry {
    schema: String,
    workspaces: Vec<LocalWorkspaceRecord>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct LocalWorkspaceRecord {
    bridge_id: String,
    local_path: String,
    display_name: String,
    project_path: String,
    snapshot_digest: String,
    #[serde(default)]
    last_applied_run: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PublicLocalWorkspace {
    schema: String,
    bridge_id: String,
    display_name: String,
    snapshot_digest: String,
    files: u64,
    bytes: u64,
    #[serde(default)]
    excluded_entries: u64,
    updated_at: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SyncReceipt {
    status: String,
    project_path: String,
    local_workspace: PublicLocalWorkspace,
}

#[derive(Debug, Deserialize)]
struct ErrorReceipt {
    error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct Fingerprint {
    sha256: String,
    size: u64,
    #[serde(default)]
    mode: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ExportEntry {
    path: String,
    operation: String,
    before: Option<Fingerprint>,
    after: Option<Fingerprint>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ExportManifest {
    schema: String,
    bridge_id: String,
    run_id: String,
    entry_digest: String,
    #[serde(default)]
    decision_status: String,
    entries: Vec<ExportEntry>,
}

#[derive(Debug, Serialize)]
pub struct ApplyReceipt {
    schema: &'static str,
    status: &'static str,
    run_id: String,
    decision_status: String,
    files: usize,
    recovery_path: String,
}

fn registry_path() -> Result<PathBuf, String> {
    configured_path()?
        .parent()
        .map(|path| path.join(REGISTRY_FILE))
        .ok_or_else(|| "Directory configurazione DEVIN non valida".to_string())
}

fn read_registry() -> Result<LocalRegistry, String> {
    let path = registry_path()?;
    reject_symlink(&path, "registro workspace")?;
    if let Some(directory) = path.parent() {
        reject_symlink(directory, "directory registro workspace")?;
    }
    let raw = match fs::read_to_string(&path) {
        Ok(raw) => raw,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            return Ok(LocalRegistry {
                schema: REGISTRY_SCHEMA.to_string(),
                workspaces: Vec::new(),
            })
        }
        Err(err) => return Err(format!("Impossibile leggere il registro workspace: {err}")),
    };
    let registry: LocalRegistry = serde_json::from_str(&raw)
        .map_err(|err| format!("Registro workspace locale non valido: {err}"))?;
    if registry.schema != REGISTRY_SCHEMA {
        return Err("Schema registro workspace locale non supportato".to_string());
    }
    Ok(registry)
}

fn write_registry(registry: &LocalRegistry) -> Result<(), String> {
    let path = registry_path()?;
    reject_symlink(&path, "registro workspace")?;
    let directory = path
        .parent()
        .ok_or_else(|| "Directory registro workspace non valida".to_string())?;
    fs::create_dir_all(directory)
        .map_err(|err| format!("Impossibile creare il registro workspace: {err}"))?;
    reject_symlink(directory, "directory registro workspace")?;
    protect_config_directory(directory)?;
    let raw = serde_json::to_vec_pretty(registry)
        .map_err(|err| format!("Impossibile serializzare il registro workspace: {err}"))?;
    let temporary = directory.join(format!(".{REGISTRY_FILE}.{}.tmp", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|err| format!("Impossibile creare il registro temporaneo: {err}"))?;
    let result = file
        .write_all(&raw)
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.sync_all())
        .and_then(|_| replace_file(&temporary, &path));
    if let Err(err) = result {
        let _ = fs::remove_file(&temporary);
        return Err(format!("Impossibile salvare il registro workspace: {err}"));
    }
    Ok(())
}

fn authorize_remote(window: &WebviewWindow, config: &DesktopConfig) -> Result<(), String> {
    let current = window
        .url()
        .map_err(|err| format!("Origine webview non leggibile: {err}"))?;
    let expected = config.frontdoor_url.origin().ascii_serialization();
    if current.origin().ascii_serialization() != expected {
        return Err("Comando workspace rifiutato: origine webview non autorizzata".to_string());
    }
    Ok(())
}

fn is_excluded_component(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        ".git"
            | ".hg"
            | ".svn"
            | ".devin"
            | ".devin_state"
            | ".devin_chat"
            | ".devin_cache"
            | ".venv"
            | "venv"
            | "env"
            | "node_modules"
            | "target"
            | "dist"
            | "build"
            | "__pycache__"
            | ".pytest_cache"
            | ".mypy_cache"
            | ".ruff_cache"
            | "logs"
            | "models"
            | ".ssh"
            | ".aws"
            | ".azure"
            | ".gnupg"
    )
}

fn is_excluded_file(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|part| part.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if name == ".env"
        || name.starts_with(".env.")
        || matches!(
            name.as_str(),
            "id_rsa"
                | "id_ed25519"
                | ".npmrc"
                | ".pypirc"
                | ".netrc"
                | "credentials"
                | "credentials.json"
                | "secrets.json"
                | "secrets.yaml"
                | "secrets.yml"
                | "secrets.toml"
        )
    {
        return true;
    }
    matches!(
        path.extension()
            .and_then(|part| part.to_str())
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str(),
        "pyc"
            | "pyo"
            | "tmp"
            | "bak"
            | "orig"
            | "rej"
            | "gguf"
            | "pem"
            | "key"
            | "p12"
            | "pfx"
            | "kdbx"
    )
}

fn walk_allowed(entry: &DirEntry) -> bool {
    if entry.depth() == 0 {
        return true;
    }
    entry
        .file_name()
        .to_str()
        .map(|name| !is_excluded_component(name))
        .unwrap_or(false)
}

fn zip_name(root: &Path, path: &Path) -> Result<String, String> {
    let relative = path
        .strip_prefix(root)
        .map_err(|_| "File fuori dalla cartella selezionata".to_string())?;
    let mut parts = Vec::new();
    for component in relative.components() {
        match component {
            Component::Normal(value) => parts.push(
                value
                    .to_str()
                    .ok_or_else(|| "Nome file locale non rappresentabile in UTF-8".to_string())?,
            ),
            _ => return Err("Path locale non sicuro".to_string()),
        }
    }
    if parts.is_empty() {
        return Err("Path relativo vuoto".to_string());
    }
    Ok(parts.join("/"))
}

fn build_snapshot(root: &Path) -> Result<(Vec<u8>, usize, u64, usize), String> {
    let root = root
        .canonicalize()
        .map_err(|err| format!("Cartella locale non accessibile: {err}"))?;
    if !root.is_dir() {
        return Err("La selezione non e' una cartella".to_string());
    }
    let mut output = Cursor::new(Vec::new());
    let mut writer = ZipWriter::new(&mut output);
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    let mut count = 0usize;
    let mut total = 0u64;
    let excluded = std::cell::Cell::new(0usize);
    for item in WalkDir::new(&root)
        .follow_links(false)
        .into_iter()
        .filter_entry(|entry| {
            let allowed = walk_allowed(entry);
            if !allowed {
                excluded.set(excluded.get() + 1);
            }
            allowed
        })
    {
        let entry = item.map_err(|err| format!("Scansione workspace fallita: {err}"))?;
        if entry.depth() == 0 || entry.file_type().is_dir() {
            continue;
        }
        if is_excluded_file(entry.path()) {
            excluded.set(excluded.get() + 1);
            continue;
        }
        if entry.file_type().is_symlink() || !entry.file_type().is_file() {
            return Err(format!(
                "Collegamento o file speciale non consentito: {}",
                entry.path().display()
            ));
        }
        let metadata = fs::symlink_metadata(entry.path())
            .map_err(|err| format!("Metadati file non leggibili: {err}"))?;
        if metadata.file_type().is_symlink() {
            return Err(format!(
                "Symlink non consentito: {}",
                entry.path().display()
            ));
        }
        if metadata.len() > MAX_FILE_BYTES {
            return Err(format!("File oltre 30 MiB: {}", entry.path().display()));
        }
        total = total
            .checked_add(metadata.len())
            .ok_or_else(|| "Dimensione workspace non valida".to_string())?;
        count += 1;
        if total > MAX_TOTAL_BYTES || count > MAX_FILES {
            return Err("Workspace oltre i limiti (100 MiB / 10.000 file)".to_string());
        }
        writer
            .start_file(zip_name(&root, entry.path())?, options)
            .map_err(|err| format!("Creazione snapshot fallita: {err}"))?;
        let mut source =
            File::open(entry.path()).map_err(|err| format!("File locale non leggibile: {err}"))?;
        std::io::copy(&mut source, &mut writer)
            .map_err(|err| format!("Lettura file locale fallita: {err}"))?;
    }
    writer
        .finish()
        .map_err(|err| format!("Finalizzazione snapshot fallita: {err}"))?;
    Ok((output.into_inner(), count, total, excluded.get()))
}

fn http_client() -> Result<Client, String> {
    Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(180))
        .build()
        .map_err(|err| format!("Client bridge non disponibile: {err}"))
}

fn sync_root(
    config: &DesktopConfig,
    root: &Path,
    bridge_id: &str,
    display_name: &str,
    project_path: &str,
) -> Result<SyncReceipt, String> {
    let (snapshot, _, _, excluded_entries) = build_snapshot(root)?;
    let digest = format!("{:x}", Sha256::digest(&snapshot));
    let excluded_text = excluded_entries.to_string();
    let endpoint = config
        .frontdoor_url
        .join("api/local-workspace/snapshot")
        .map_err(|err| format!("Endpoint snapshot non valido: {err}"))?;
    let response = http_client()?
        .post(endpoint)
        .bearer_auth(&config.access_token)
        .query(&[
            ("bridge_id", bridge_id),
            ("display_name", display_name),
            ("snapshot_digest", digest.as_str()),
            ("project_path", project_path),
            ("excluded_entries", excluded_text.as_str()),
        ])
        .header("Content-Type", "application/zip")
        .body(snapshot)
        .send()
        .map_err(|err| format!("Upload snapshot fallito: {err}"))?;
    let status = response.status();
    let body = response
        .bytes()
        .map_err(|err| format!("Risposta snapshot non leggibile: {err}"))?;
    if !status.is_success() {
        let detail = serde_json::from_slice::<ErrorReceipt>(&body)
            .ok()
            .and_then(|payload| payload.error)
            .unwrap_or_else(|| format!("HTTP {status}"));
        return Err(format!("Backend snapshot: {detail}"));
    }
    if let Some(error) = serde_json::from_slice::<ErrorReceipt>(&body)
        .ok()
        .and_then(|payload| payload.error)
    {
        return Err(format!("Backend snapshot: {error}"));
    }
    let receipt: SyncReceipt = serde_json::from_slice(&body)
        .map_err(|err| format!("Ricevuta snapshot non valida: {err}"))?;
    if receipt.status != "synced"
        || receipt.local_workspace.schema != SNAPSHOT_SCHEMA
        || receipt.local_workspace.bridge_id != bridge_id
        || receipt.local_workspace.snapshot_digest != digest
    {
        return Err("Ricevuta snapshot non coerente".to_string());
    }
    Ok(receipt)
}

fn update_record(
    registry: &mut LocalRegistry,
    local_path: &Path,
    receipt: &SyncReceipt,
) -> Result<(), String> {
    let record = LocalWorkspaceRecord {
        bridge_id: receipt.local_workspace.bridge_id.clone(),
        local_path: local_path.to_string_lossy().into_owned(),
        display_name: receipt.local_workspace.display_name.clone(),
        project_path: receipt.project_path.clone(),
        snapshot_digest: receipt.local_workspace.snapshot_digest.clone(),
        last_applied_run: registry
            .workspaces
            .iter()
            .find(|record| record.bridge_id == receipt.local_workspace.bridge_id)
            .map(|record| record.last_applied_run.clone())
            .unwrap_or_default(),
    };
    registry
        .workspaces
        .retain(|item| item.bridge_id != record.bridge_id);
    registry.workspaces.push(record);
    write_registry(registry)
}

#[tauri::command]
pub async fn select_and_sync_local_workspace(
    app: AppHandle,
    window: WebviewWindow,
    project_path: Option<String>,
) -> Result<Option<SyncReceipt>, String> {
    let config = load_config()?;
    authorize_remote(&window, &config)?;
    let chosen = app
        .dialog()
        .file()
        .set_title("Seleziona la cartella locale per DEVIN")
        .blocking_pick_folder();
    let Some(chosen) = chosen else {
        return Ok(None);
    };
    let root = chosen
        .into_path()
        .map_err(|err| format!("Cartella selezionata non valida: {err}"))?
        .canonicalize()
        .map_err(|err| format!("Cartella selezionata non accessibile: {err}"))?;
    let display_name = root
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .unwrap_or("workspace-locale")
        .to_string();
    let mut registry = read_registry()?;
    let existing = registry.workspaces.iter().find(|record| {
        Path::new(&record.local_path)
            .canonicalize()
            .map(|path| path == root)
            .unwrap_or(false)
    });
    let bridge_id = existing
        .map(|record| record.bridge_id.clone())
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    let target_project = project_path
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_default();
    let receipt = sync_root(&config, &root, &bridge_id, &display_name, &target_project)?;
    update_record(&mut registry, &root, &receipt)?;
    Ok(Some(receipt))
}

#[tauri::command]
pub async fn sync_local_workspace(
    window: WebviewWindow,
    bridge_id: String,
) -> Result<SyncReceipt, String> {
    let config = load_config()?;
    authorize_remote(&window, &config)?;
    Uuid::parse_str(&bridge_id).map_err(|_| "bridge_id locale non valido".to_string())?;
    let mut registry = read_registry()?;
    let record = registry
        .workspaces
        .iter()
        .find(|record| record.bridge_id == bridge_id)
        .cloned()
        .ok_or_else(|| "Workspace locale non registrato su questo PC".to_string())?;
    let root = PathBuf::from(&record.local_path)
        .canonicalize()
        .map_err(|err| format!("Cartella locale non accessibile: {err}"))?;
    let receipt = sync_root(
        &config,
        &root,
        &record.bridge_id,
        &record.display_name,
        &record.project_path,
    )?;
    update_record(&mut registry, &root, &receipt)?;
    Ok(receipt)
}

fn safe_relative(raw: &str) -> Result<PathBuf, String> {
    if raw.is_empty() || raw.contains('\\') || raw.contains('\0') {
        return Err("Path export non valido".to_string());
    }
    let mut output = PathBuf::new();
    for part in raw.split('/') {
        if part.is_empty()
            || part == "."
            || part == ".."
            || part.contains(':')
            || part.ends_with([' ', '.'])
            || is_excluded_component(part)
        {
            return Err(format!("Path export non sicuro: {raw}"));
        }
        output.push(part);
    }
    if output.is_absolute() || is_excluded_file(&output) {
        return Err(format!("Path export escluso: {raw}"));
    }
    Ok(output)
}

fn local_fingerprint(path: &Path) -> Result<Option<Fingerprint>, String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(err) => return Err(format!("Metadati locali non leggibili: {err}")),
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!("Target locale non sicuro: {}", path.display()));
    }
    let mut input = File::open(path).map_err(|err| format!("File locale non leggibile: {err}"))?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let read = input
            .read(&mut buffer)
            .map_err(|err| format!("Hash locale fallito: {err}"))?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(Some(Fingerprint {
        sha256: format!("{:x}", digest.finalize()),
        size: metadata.len(),
        mode: 0,
    }))
}

fn same_content(actual: &Option<Fingerprint>, expected: &Option<Fingerprint>) -> bool {
    match (actual, expected) {
        (None, None) => true,
        (Some(actual), Some(expected)) => {
            actual.sha256 == expected.sha256 && actual.size == expected.size
        }
        _ => false,
    }
}

fn read_export(raw: &[u8]) -> Result<(ExportManifest, HashMap<String, Vec<u8>>), String> {
    if raw.len() as u64 > MAX_TOTAL_BYTES {
        return Err("Export remoto oltre 100 MiB".to_string());
    }
    let mut archive =
        ZipArchive::new(Cursor::new(raw)).map_err(|err| format!("Export ZIP non valido: {err}"))?;
    let manifest: ExportManifest = {
        let mut entry = archive
            .by_name("bridge-manifest.json")
            .map_err(|_| "Manifest bridge mancante".to_string())?;
        if entry.size() > 2 * 1024 * 1024 {
            return Err("Manifest bridge oltre il limite".to_string());
        }
        let mut body = Vec::new();
        entry
            .read_to_end(&mut body)
            .map_err(|err| format!("Manifest bridge non leggibile: {err}"))?;
        serde_json::from_slice(&body).map_err(|err| format!("Manifest bridge non valido: {err}"))?
    };
    let mut files = HashMap::new();
    let mut total = 0u64;
    for entry in &manifest.entries {
        if entry.operation == "delete" {
            continue;
        }
        let relative = safe_relative(&entry.path)?;
        let name = format!("files/{}", entry.path);
        let mut source = archive
            .by_name(&name)
            .map_err(|_| format!("File export mancante: {}", entry.path))?;
        if source.size() > MAX_FILE_BYTES {
            return Err(format!("File export oltre 30 MiB: {}", entry.path));
        }
        total += source.size();
        if total > MAX_TOTAL_BYTES {
            return Err("Export espanso oltre 100 MiB".to_string());
        }
        let mut body = Vec::new();
        source
            .read_to_end(&mut body)
            .map_err(|err| format!("File export non leggibile: {err}"))?;
        let actual = Fingerprint {
            sha256: format!("{:x}", Sha256::digest(&body)),
            size: body.len() as u64,
            mode: 0,
        };
        if !same_content(&Some(actual), &entry.after) {
            return Err(format!("Digest export non corrispondente: {}", entry.path));
        }
        files.insert(relative.to_string_lossy().replace('\\', "/"), body);
    }
    Ok((manifest, files))
}

fn recovery_root(bridge_id: &str, run_id: &str, decision_status: &str) -> Result<PathBuf, String> {
    if !run_id
        .chars()
        .all(|char| char.is_ascii_alphanumeric() || matches!(char, '_' | '-'))
    {
        return Err("run_id export non sicuro".to_string());
    }
    let directory = registry_path()?
        .parent()
        .ok_or_else(|| "Directory DEVIN non valida".to_string())?
        .join("local-workspace-recovery")
        .join(bridge_id)
        .join(format!("{run_id}-{decision_status}"));
    fs::create_dir_all(&directory)
        .map_err(|err| format!("Impossibile creare il recovery locale: {err}"))?;
    Ok(directory)
}

fn apply_export(
    root: &Path,
    manifest: &ExportManifest,
    files: &HashMap<String, Vec<u8>>,
) -> Result<PathBuf, String> {
    if manifest.entries.len() > MAX_FILES {
        return Err("Manifest con troppi file".to_string());
    }
    let recovery = recovery_root(
        &manifest.bridge_id,
        &manifest.run_id,
        &manifest.decision_status,
    )?;
    let mut validated = Vec::new();
    for entry in &manifest.entries {
        if !matches!(entry.operation.as_str(), "create" | "modify" | "delete") {
            return Err(format!(
                "Operazione export sconosciuta: {}",
                entry.operation
            ));
        }
        let relative = safe_relative(&entry.path)?;
        let target = root.join(&relative);
        if !same_content(&local_fingerprint(&target)?, &entry.before) {
            return Err(format!(
                "Conflitto locale: {} e' cambiato dopo l'ultimo snapshot",
                entry.path
            ));
        }
        validated.push((entry.clone(), relative, target));
    }

    let mut completed: Vec<(ExportEntry, PathBuf, PathBuf)> = Vec::new();
    for (entry, relative, target) in validated {
        let backup = recovery.join(&relative);
        let operation = (|| -> Result<(), String> {
            if let Some(parent) = backup.parent() {
                fs::create_dir_all(parent)
                    .map_err(|err| format!("Recovery locale non creabile: {err}"))?;
            }
            match entry.operation.as_str() {
                "delete" => fs::rename(&target, &backup)
                    .map_err(|err| format!("Spostamento nel recovery fallito: {err}"))?,
                "modify" => {
                    fs::copy(&target, &backup)
                        .map_err(|err| format!("Backup locale fallito: {err}"))?;
                    write_local_file(
                        &target,
                        files
                            .get(&entry.path)
                            .ok_or_else(|| format!("Payload mancante: {}", entry.path))?,
                    )?;
                }
                "create" => write_local_file(
                    &target,
                    files
                        .get(&entry.path)
                        .ok_or_else(|| format!("Payload mancante: {}", entry.path))?,
                )?,
                _ => unreachable!(),
            }
            if !same_content(&local_fingerprint(&target)?, &entry.after) {
                return Err(format!("Verifica post-write fallita: {}", entry.path));
            }
            Ok(())
        })();
        if let Err(error) = operation {
            match entry.operation.as_str() {
                "create" => {
                    if target.exists() {
                        let failed = recovery.join("failed-created").join(&entry.path);
                        if let Some(parent) = failed.parent() {
                            let _ = fs::create_dir_all(parent);
                        }
                        let _ = fs::rename(&target, failed);
                    }
                }
                "modify" | "delete" => {
                    if backup.exists() {
                        if let Some(parent) = target.parent() {
                            let _ = fs::create_dir_all(parent);
                        }
                        let _ = replace_file(&backup, &target);
                    }
                }
                _ => {}
            }
            for (done, done_target, done_backup) in completed.iter().rev() {
                match done.operation.as_str() {
                    "create" => {
                        if done_target.exists() {
                            let failed = recovery.join("failed-created").join(&done.path);
                            if let Some(parent) = failed.parent() {
                                let _ = fs::create_dir_all(parent);
                            }
                            let _ = fs::rename(done_target, failed);
                        }
                    }
                    "modify" | "delete" => {
                        if done_backup.exists() {
                            if let Some(parent) = done_target.parent() {
                                let _ = fs::create_dir_all(parent);
                            }
                            let _ = replace_file(done_backup, done_target);
                        }
                    }
                    _ => {}
                }
            }
            return Err(format!("Apply locale annullato e ripristinato: {error}"));
        }
        completed.push((entry, target, backup));
    }
    Ok(recovery)
}

fn write_local_file(path: &Path, body: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "Destinazione locale non valida".to_string())?;
    fs::create_dir_all(parent).map_err(|err| format!("Directory locale non creabile: {err}"))?;
    let temporary = parent.join(format!(
        ".{}.devin-{}.tmp",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("file"),
        Uuid::new_v4()
    ));
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|err| format!("File locale temporaneo non creabile: {err}"))?;
    let result = output
        .write_all(body)
        .and_then(|_| output.sync_all())
        .and_then(|_| replace_file(&temporary, path));
    if let Err(err) = result {
        let _ = fs::remove_file(&temporary);
        return Err(format!("Scrittura locale atomica fallita: {err}"));
    }
    Ok(())
}

#[tauri::command]
pub async fn apply_local_workspace_changes(
    window: WebviewWindow,
    bridge_id: String,
    project_path: String,
    run_id: String,
    entry_digest: String,
) -> Result<ApplyReceipt, String> {
    let config = load_config()?;
    authorize_remote(&window, &config)?;
    Uuid::parse_str(&bridge_id).map_err(|_| "bridge_id locale non valido".to_string())?;
    if entry_digest.len() != 64 || !entry_digest.chars().all(|char| char.is_ascii_hexdigit()) {
        return Err("entry_digest non valido".to_string());
    }
    let mut registry = read_registry()?;
    let record = registry
        .workspaces
        .iter()
        .find(|record| record.bridge_id == bridge_id && record.project_path == project_path)
        .cloned()
        .ok_or_else(|| "Workspace locale non registrato su questo PC".to_string())?;
    let root = PathBuf::from(&record.local_path)
        .canonicalize()
        .map_err(|err| format!("Cartella locale non accessibile: {err}"))?;
    let endpoint = config
        .frontdoor_url
        .join(&format!("api/local-workspace/export/{run_id}"))
        .map_err(|err| format!("Endpoint export non valido: {err}"))?;
    let response = http_client()?
        .get(endpoint)
        .bearer_auth(&config.access_token)
        .query(&[
            ("project_path", project_path.as_str()),
            ("entry_digest", entry_digest.as_str()),
        ])
        .send()
        .map_err(|err| format!("Download modifiche fallito: {err}"))?;
    let status = response.status();
    let body = response
        .bytes()
        .map_err(|err| format!("Export modifiche non leggibile: {err}"))?;
    if !status.is_success() {
        let detail = serde_json::from_slice::<ErrorReceipt>(&body)
            .ok()
            .and_then(|payload| payload.error)
            .unwrap_or_else(|| format!("HTTP {status}"));
        return Err(format!("Backend export: {detail}"));
    }
    let (manifest, files) = read_export(&body)?;
    if manifest.schema != EXPORT_SCHEMA
        || manifest.bridge_id != bridge_id
        || manifest.run_id != run_id
        || !manifest.entry_digest.eq_ignore_ascii_case(&entry_digest)
    {
        return Err("Identita' export non coerente".to_string());
    }
    let recovery = apply_export(&root, &manifest, &files)?;
    if let Some(current) = registry
        .workspaces
        .iter_mut()
        .find(|item| item.bridge_id == bridge_id)
    {
        current.last_applied_run = format!("{}:{}", run_id, manifest.decision_status);
    }
    write_registry(&registry)?;
    Ok(ApplyReceipt {
        schema: "devin_local_workspace_apply_v1",
        status: "applied_local",
        run_id,
        decision_status: manifest.decision_status,
        files: manifest.entries.len(),
        recovery_path: recovery.to_string_lossy().into_owned(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exclusion_rules_block_secrets_and_generated_trees() {
        assert!(is_excluded_component(".git"));
        assert!(is_excluded_component("node_modules"));
        assert!(is_excluded_file(Path::new(".env.local")));
        assert!(is_excluded_file(Path::new("private.key")));
        assert!(!is_excluded_file(Path::new("src/main.rs")));
    }

    #[test]
    fn safe_relative_rejects_escape_and_backslashes() {
        assert!(safe_relative("src/main.rs").is_ok());
        for value in [
            "../secret",
            "/absolute",
            "src\\main.rs",
            ".git/config",
            ".env",
        ] {
            assert!(safe_relative(value).is_err(), "accepted {value}");
        }
    }

    #[test]
    fn content_comparison_intentionally_ignores_cross_platform_mode() {
        let local = Some(Fingerprint {
            sha256: "a".repeat(64),
            size: 7,
            mode: 0,
        });
        let rig = Some(Fingerprint {
            sha256: "a".repeat(64),
            size: 7,
            mode: 0o644,
        });
        assert!(same_content(&local, &rig));
    }
}
