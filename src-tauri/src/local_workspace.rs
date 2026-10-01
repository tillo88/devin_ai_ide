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
use zip::ZipArchive;

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
    #[serde(default)]
    mode: String,
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

fn is_local_ui_url(url: &tauri::Url) -> bool {
    let packaged_origin = matches!(
        (url.scheme(), url.host_str()),
        ("tauri", Some("localhost"))
            | ("http", Some("tauri.localhost"))
            | ("https", Some("tauri.localhost"))
    );
    let development_origin = cfg!(debug_assertions)
        && url.scheme() == "http"
        && matches!(url.host_str(), Some("127.0.0.1") | Some("localhost"))
        && url.port() == Some(1430);
    packaged_origin || development_origin
}

pub(crate) fn authorize_local_ui(window: &WebviewWindow) -> Result<(), String> {
    let current = window
        .url()
        .map_err(|err| format!("Origine webview non leggibile: {err}"))?;
    if !is_local_ui_url(&current) {
        return Err(
            "Comando workspace rifiutato: richiesto dal bundle locale non autorizzato".to_string(),
        );
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
            | ".tox"
            | ".nox"
            | ".idea"
            | ".vscode"
            | ".next"
            | ".nuxt"
            | ".turbo"
            | ".gradle"
            | "logs"
            | "models"
            | "coverage"
            | "htmlcov"
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

fn http_client() -> Result<Client, String> {
    Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(180))
        .build()
        .map_err(|err| format!("Client bridge non disponibile: {err}"))
}

fn register_root(
    config: &DesktopConfig,
    bridge_id: &str,
    display_name: &str,
    project_path: &str,
) -> Result<SyncReceipt, String> {
    let endpoint = config
        .frontdoor_url
        .join("api/local-workspace/register")
        .map_err(|err| format!("Endpoint registrazione non valido: {err}"))?;
    let response = http_client()?
        .post(endpoint)
        .json(&serde_json::json!({
            "bridge_id": bridge_id,
            "display_name": display_name,
            "project_path": project_path,
        }))
        .send()
        .map_err(|err| format!("Registrazione workspace fallita: {err}"))?;
    let status = response.status();
    let body = response
        .bytes()
        .map_err(|err| format!("Risposta registrazione non leggibile: {err}"))?;
    if !status.is_success() {
        let detail = serde_json::from_slice::<ErrorReceipt>(&body)
            .ok()
            .and_then(|payload| payload.error)
            .unwrap_or_else(|| format!("HTTP {status}"));
        return Err(format!("Backend registrazione: {detail}"));
    }
    if let Some(error) = serde_json::from_slice::<ErrorReceipt>(&body)
        .ok()
        .and_then(|payload| payload.error)
    {
        return Err(format!("Backend registrazione: {error}"));
    }
    let receipt: SyncReceipt = serde_json::from_slice(&body)
        .map_err(|err| format!("Ricevuta registrazione non valida: {err}"))?;
    if receipt.status != "registered"
        || receipt.local_workspace.schema != SNAPSHOT_SCHEMA
        || receipt.local_workspace.mode != "direct"
        || receipt.local_workspace.bridge_id != bridge_id
    {
        return Err("Ricevuta registrazione non coerente".to_string());
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
    authorize_local_ui(&window)?;
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
    let receipt = register_root(&config, &bridge_id, &display_name, &target_project)?;
    update_record(&mut registry, &root, &receipt)?;
    Ok(Some(receipt))
}

#[tauri::command]
pub async fn sync_local_workspace(
    window: WebviewWindow,
    bridge_id: String,
) -> Result<SyncReceipt, String> {
    let config = load_config()?;
    authorize_local_ui(&window)?;
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
    let receipt = register_root(
        &config,
        &record.bridge_id,
        &record.display_name,
        &record.project_path,
    )?;
    update_record(&mut registry, &root, &receipt)?;
    Ok(receipt)
}

const DIRECT_TREE_MAX_FILES: usize = 1_500;
const DIRECT_TREE_MAX_WALK: usize = 30_000;
const DIRECT_TREE_MAX_DEPTH: usize = 12;
const DIRECT_READ_MAX_BYTES: u64 = 256 * 1024;
const DIRECT_CONTEXT_FILE_BYTES: u64 = 512 * 1024;
const DIRECT_CONTEXT_MAX_CHARS: usize = 16_000;
const DIRECT_CONTEXT_PER_FILE_CHARS: usize = 4_000;
const DIRECT_CONTEXT_MAX_SCAN_FILES: usize = 2_000;

#[derive(Debug, Serialize)]
pub struct DirectTreeEntry {
    name: String,
    path: String,
    size: u64,
    mtime: String,
    is_text: bool,
}

#[derive(Debug, Serialize)]
pub struct DirectTreePayload {
    scope: String,
    root_name: String,
    files: Vec<DirectTreeEntry>,
    count: usize,
    truncated: bool,
    limits: serde_json::Value,
}

#[derive(Debug, Serialize)]
pub struct DirectFilePayload {
    scope: String,
    path: String,
    content: String,
    language: String,
    size: u64,
    bytes_read: usize,
    sha256: String,
    truncated: bool,
    read_only: bool,
}

#[derive(Debug, Serialize)]
pub struct DirectContextPayload {
    schema: String,
    bridge_id: String,
    content: String,
    files: Vec<String>,
    truncated: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DirectPlanOperation {
    path: String,
    operation: String,
    #[serde(default)]
    content: Option<String>,
    #[serde(default)]
    expected_sha256: Option<String>,
}

fn is_context_stopword(value: &str) -> bool {
    matches!(
        value,
        "about"
            | "alla"
            | "alle"
            | "anche"
            | "cartella"
            | "che"
            | "ciao"
            | "come"
            | "con"
            | "cosa"
            | "da"
            | "dal"
            | "dalla"
            | "delle"
            | "del"
            | "dei"
            | "file"
            | "folder"
            | "from"
            | "hai"
            | "have"
            | "nella"
            | "nelle"
            | "per"
            | "please"
            | "progetto"
            | "puoi"
            | "questa"
            | "questo"
            | "riesci"
            | "that"
            | "the"
            | "this"
            | "una"
            | "uno"
            | "vorrei"
            | "what"
            | "with"
            | "workspace"
    )
}

fn local_context_terms(query: &str) -> Vec<String> {
    let mut terms: Vec<String> = query
        .split(|character: char| !character.is_alphanumeric() && character != '_')
        .map(|value| value.to_lowercase())
        .filter(|value| value.len() >= 3 && !is_context_stopword(value))
        .collect();
    terms.sort();
    terms.dedup();
    terms.truncate(20);
    terms
}

fn context_path_priority(relative: &str) -> i64 {
    let normalized = relative.replace('\\', "/").to_ascii_lowercase();
    let file = normalized.rsplit('/').next().unwrap_or(normalized.as_str());
    let extension = file.rsplit_once('.').map(|(_, value)| value).unwrap_or("");
    let mut score = 0i64;

    if file == "readme" || file.starts_with("readme.") {
        score += 120;
    }
    if matches!(
        file,
        "cargo.toml"
            | "composer.json"
            | "go.mod"
            | "package.json"
            | "pyproject.toml"
            | "requirements.txt"
            | "setup.cfg"
            | "setup.py"
    ) {
        score += 100;
    }
    if matches!(
        file,
        "app.py" | "cli.py" | "index.js" | "index.ts" | "main.py" | "main.rs" | "server.py"
    ) || file.contains("manager")
        || file.contains("gui")
        || file.contains("engine")
    {
        score += 70;
    }
    if matches!(
        extension,
        "c" | "cc"
            | "cpp"
            | "cs"
            | "go"
            | "h"
            | "hpp"
            | "java"
            | "js"
            | "jsx"
            | "kt"
            | "lua"
            | "py"
            | "pyi"
            | "rb"
            | "rs"
            | "svelte"
            | "ts"
            | "tsx"
            | "vue"
    ) {
        score += 35;
    } else if matches!(extension, "md" | "rst" | "toml" | "yaml" | "yml") {
        score += 20;
    }
    let components: Vec<&str> = normalized.split('/').collect();
    if components.len() == 1 {
        score += 15;
    }
    if components
        .iter()
        .any(|value| matches!(*value, "app" | "lib" | "src" | "source" | "spyengine"))
    {
        score += 25;
    }
    for component in &components[..components.len().saturating_sub(1)] {
        score -= match *component {
            "backup" | "backups" => 100,
            "cache" | "debug" | "generated" | "reports" => 70,
            "data" | "fixtures" | "scripts" => 25,
            _ => 0,
        };
    }
    score - i64::try_from(components.len().saturating_sub(1).min(8)).unwrap_or(0)
}

fn context_candidate_score(relative: &str, content: &str, terms: &[String]) -> i64 {
    let normalized_path = relative.replace('\\', "/").to_lowercase();
    let normalized_content = content.to_lowercase();
    let mut score = context_path_priority(relative);
    for term in terms {
        if normalized_path.contains(term) {
            score += 40;
        }
        score += i64::try_from(normalized_content.matches(term).take(5).count()).unwrap_or(0) * 4;
    }
    score
}

fn compose_direct_context(
    mut candidates: Vec<(i64, String, String)>,
    scan_truncated: bool,
) -> (String, Vec<String>, bool) {
    candidates.sort_by(|left, right| right.0.cmp(&left.0).then_with(|| left.1.cmp(&right.1)));
    let mut content = String::new();
    let mut selected = Vec::new();
    let mut truncated = scan_truncated;
    for (_, relative, file_content) in candidates.into_iter().take(8) {
        let digest = format!("{:x}", Sha256::digest(file_content.as_bytes()));
        let header = format!("\n\n--- FILE LOCALE: {relative} · SHA256: {digest} ---\n");
        let remaining = DIRECT_CONTEXT_MAX_CHARS.saturating_sub(content.len() + header.len());
        if remaining == 0 {
            truncated = true;
            break;
        }
        content.push_str(&header);
        let allowance = remaining.min(DIRECT_CONTEXT_PER_FILE_CHARS);
        if file_content.len() > allowance {
            let marker = "\n[ESTRATTO LOCALE TRONCATO: il file continua sul disco; usa una lettura locale mirata prima di concludere che il codice sia incompleto.]\n";
            let body_allowance = allowance.saturating_sub(marker.len());
            let mut end = body_allowance;
            while end > 0 && !file_content.is_char_boundary(end) {
                end -= 1;
            }
            content.push_str(&file_content[..end]);
            if marker.len() <= allowance.saturating_sub(end) {
                content.push_str(marker);
            }
            truncated = true;
        } else {
            content.push_str(&file_content);
        }
        selected.push(relative);
        if content.len() >= DIRECT_CONTEXT_MAX_CHARS {
            break;
        }
    }
    (content, selected, truncated)
}

pub(crate) fn direct_workspace_root(bridge_id: &str) -> Result<PathBuf, String> {
    Uuid::parse_str(bridge_id).map_err(|_| "bridge_id locale non valido".to_string())?;
    let registry = read_registry()?;
    let record = registry
        .workspaces
        .iter()
        .find(|record| record.bridge_id == bridge_id)
        .ok_or_else(|| "Workspace locale non registrato su questo PC".to_string())?;
    let root = PathBuf::from(&record.local_path)
        .canonicalize()
        .map_err(|err| format!("Cartella locale non accessibile: {err}"))?;
    if !root.is_dir() {
        return Err("La cartella locale registrata non e' disponibile".to_string());
    }
    Ok(root)
}

fn is_probably_text_path(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    matches!(
        extension.as_str(),
        "bat"
            | "c"
            | "cc"
            | "cfg"
            | "conf"
            | "cpp"
            | "cs"
            | "css"
            | "csv"
            | "go"
            | "h"
            | "hpp"
            | "html"
            | "ini"
            | "java"
            | "js"
            | "json"
            | "jsx"
            | "kt"
            | "less"
            | "lua"
            | "md"
            | "mjs"
            | "ps1"
            | "py"
            | "pyi"
            | "rb"
            | "rs"
            | "rst"
            | "sass"
            | "scss"
            | "sh"
            | "sql"
            | "svelte"
            | "toml"
            | "ts"
            | "tsx"
            | "txt"
            | "vue"
            | "xml"
            | "yaml"
            | "yml"
    ) || matches!(
        name.as_str(),
        "dockerfile" | "gemfile" | "license" | "makefile" | "procfile" | "readme"
    ) || [
        "dockerfile.",
        "gemfile.",
        "license.",
        "makefile.",
        "procfile.",
        "readme.",
    ]
    .iter()
    .any(|prefix| name.starts_with(prefix))
}

fn direct_relative(root: &Path, path: &Path) -> Result<String, String> {
    zip_name(root, path)
}

fn direct_file(root: &Path, raw: &str) -> Result<(PathBuf, String), String> {
    let relative = safe_relative(raw)?;
    let target = root
        .join(&relative)
        .canonicalize()
        .map_err(|err| format!("File locale non disponibile: {err}"))?;
    if target == root || !target.starts_with(root) || !target.is_file() {
        return Err("File fuori dalla cartella locale autorizzata".to_string());
    }
    Ok((target, raw.replace('\\', "/")))
}

fn read_direct_text(path: &Path, max_bytes: u64) -> Result<(String, bool, u64), String> {
    let metadata =
        fs::symlink_metadata(path).map_err(|err| format!("Metadati file non leggibili: {err}"))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("File locale non regolare".to_string());
    }
    let size = metadata.len();
    let mut source = File::open(path).map_err(|err| format!("File locale non leggibile: {err}"))?;
    let mut bytes = Vec::new();
    std::io::Read::by_ref(&mut source)
        .take(max_bytes + 1)
        .read_to_end(&mut bytes)
        .map_err(|err| format!("Lettura file locale fallita: {err}"))?;
    if bytes.contains(&0) {
        return Err("File binario non visualizzabile".to_string());
    }
    let truncated = bytes.len() as u64 > max_bytes;
    if truncated {
        bytes.truncate(max_bytes as usize);
        while std::str::from_utf8(&bytes).is_err() && !bytes.is_empty() {
            bytes.pop();
        }
    }
    let content =
        String::from_utf8(bytes).map_err(|_| "File non UTF-8 non visualizzabile".to_string())?;
    Ok((content, truncated, size))
}

#[tauri::command]
pub async fn local_workspace_tree(
    window: WebviewWindow,
    bridge_id: String,
) -> Result<DirectTreePayload, String> {
    authorize_local_ui(&window)?;
    let root = direct_workspace_root(&bridge_id)?;
    let mut files = Vec::new();
    let mut walked = 0usize;
    let mut truncated = false;
    for item in WalkDir::new(&root)
        .follow_links(false)
        .max_depth(DIRECT_TREE_MAX_DEPTH)
        .into_iter()
        .filter_entry(walk_allowed)
    {
        walked += 1;
        if walked > DIRECT_TREE_MAX_WALK {
            truncated = true;
            break;
        }
        let entry = match item {
            Ok(entry) => entry,
            Err(_) => continue,
        };
        if entry.depth() == 0 || entry.file_type().is_dir() || entry.file_type().is_symlink() {
            continue;
        }
        if !entry.file_type().is_file() || is_excluded_file(entry.path()) {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(metadata) => metadata,
            Err(_) => continue,
        };
        let relative = direct_relative(&root, entry.path())?;
        let modified = metadata
            .modified()
            .ok()
            .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|value| value.as_secs().to_string())
            .unwrap_or_default();
        files.push(DirectTreeEntry {
            name: entry.file_name().to_string_lossy().into_owned(),
            path: relative,
            size: metadata.len(),
            mtime: modified,
            is_text: is_probably_text_path(entry.path()),
        });
        if files.len() >= DIRECT_TREE_MAX_FILES {
            truncated = true;
            break;
        }
    }
    files.sort_by_key(|entry| entry.path.to_ascii_lowercase());
    Ok(DirectTreePayload {
        scope: "local_direct".to_string(),
        root_name: root
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("workspace-locale")
            .to_string(),
        count: files.len(),
        files,
        truncated,
        limits: serde_json::json!({
            "max_files": DIRECT_TREE_MAX_FILES,
            "max_depth": DIRECT_TREE_MAX_DEPTH,
        }),
    })
}

#[tauri::command]
pub async fn local_workspace_read(
    window: WebviewWindow,
    bridge_id: String,
    path: String,
) -> Result<DirectFilePayload, String> {
    authorize_local_ui(&window)?;
    let root = direct_workspace_root(&bridge_id)?;
    let (target, relative) = direct_file(&root, &path)?;
    if !is_probably_text_path(&target) {
        return Err("File binario non visualizzabile".to_string());
    }
    let (content, truncated, size) = read_direct_text(&target, DIRECT_READ_MAX_BYTES)?;
    let sha256 = local_fingerprint(&target)?
        .ok_or_else(|| "File locale scomparso durante la lettura".to_string())?
        .sha256;
    let language = target
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("text")
        .to_ascii_lowercase();
    let bytes_read = content.len();
    Ok(DirectFilePayload {
        scope: "local_direct".to_string(),
        path: relative,
        content,
        language,
        size,
        bytes_read,
        sha256,
        truncated,
        read_only: true,
    })
}

#[tauri::command]
pub async fn local_workspace_context(
    window: WebviewWindow,
    bridge_id: String,
    query: String,
) -> Result<DirectContextPayload, String> {
    authorize_local_ui(&window)?;
    let root = direct_workspace_root(&bridge_id)?;
    let terms = local_context_terms(&query);
    let mut candidates: Vec<(i64, String, String)> = Vec::new();
    let mut walked = 0usize;
    let mut scanned_files = 0usize;
    let mut scan_truncated = false;
    for item in WalkDir::new(&root)
        .follow_links(false)
        .max_depth(DIRECT_TREE_MAX_DEPTH)
        .into_iter()
        .filter_entry(walk_allowed)
    {
        walked += 1;
        if walked > DIRECT_TREE_MAX_WALK {
            scan_truncated = true;
            break;
        }
        let entry = match item {
            Ok(entry) => entry,
            Err(_) => continue,
        };
        if !entry.file_type().is_file()
            || entry.file_type().is_symlink()
            || is_excluded_file(entry.path())
            || !is_probably_text_path(entry.path())
        {
            continue;
        }
        let size = match entry.metadata() {
            Ok(metadata) => metadata.len(),
            Err(_) => continue,
        };
        if size > DIRECT_CONTEXT_FILE_BYTES {
            continue;
        }
        scanned_files += 1;
        if scanned_files > DIRECT_CONTEXT_MAX_SCAN_FILES {
            scan_truncated = true;
            break;
        }
        let relative = direct_relative(&root, entry.path())?;
        let (content, _, _) = match read_direct_text(entry.path(), DIRECT_CONTEXT_FILE_BYTES) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let score = context_candidate_score(&relative, &content, &terms);
        candidates.push((score, relative, content));
    }
    let (content, selected, truncated) = compose_direct_context(candidates, scan_truncated);
    Ok(DirectContextPayload {
        schema: "devin_local_context_v1".to_string(),
        bridge_id,
        content,
        files: selected,
        truncated,
    })
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

fn safe_local_target(root: &Path, relative: &Path) -> Result<PathBuf, String> {
    let root = root
        .canonicalize()
        .map_err(|err| format!("Root locale non accessibile: {err}"))?;
    let mut target = root.clone();
    for component in relative.components() {
        let Component::Normal(part) = component else {
            return Err("Path locale non sicuro".to_string());
        };
        target.push(part);
        match fs::symlink_metadata(&target) {
            Ok(metadata) => {
                if metadata.file_type().is_symlink() {
                    return Err(format!(
                        "Symlink locale non consentito: {}",
                        target.display()
                    ));
                }
                let resolved = target
                    .canonicalize()
                    .map_err(|err| format!("Path locale non risolvibile: {err}"))?;
                if !resolved.starts_with(&root) {
                    return Err("Path locale fuori dalla cartella autorizzata".to_string());
                }
            }
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
            Err(err) => return Err(format!("Path locale non verificabile: {err}")),
        }
    }
    Ok(target)
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
        let target = safe_local_target(root, &relative)?;
        if !same_content(&local_fingerprint(&target)?, &entry.before) {
            return Err(format!(
                "Conflitto locale: {} e' cambiato dopo l'ultima lettura verificata",
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

fn direct_plan_export(
    root: &Path,
    bridge_id: &str,
    operations: Vec<DirectPlanOperation>,
) -> Result<(ExportManifest, HashMap<String, Vec<u8>>), String> {
    if operations.is_empty() || operations.len() > 20 {
        return Err("Il piano locale deve contenere da 1 a 20 operazioni".to_string());
    }
    let mut entries = Vec::new();
    let mut files = HashMap::new();
    let mut seen = std::collections::HashSet::new();
    let mut total = 0usize;
    for operation in operations {
        let relative = safe_relative(&operation.path)?;
        let normalized = relative.to_string_lossy().replace('\\', "/");
        if !seen.insert(normalized.clone()) {
            return Err(format!("Operazione locale duplicata: {normalized}"));
        }
        let target = safe_local_target(root, &relative)?;
        let before = local_fingerprint(&target)?;
        let expected = operation.expected_sha256.as_deref().unwrap_or("").trim();
        match &before {
            Some(actual) => {
                if expected.len() != 64
                    || !expected.chars().all(|value| value.is_ascii_hexdigit())
                    || !actual.sha256.eq_ignore_ascii_case(expected)
                {
                    return Err(format!(
                        "Conflitto locale: fingerprint mancante o cambiato per {normalized}"
                    ));
                }
            }
            None if !expected.is_empty() => {
                return Err(format!("Conflitto locale: {normalized} non esiste piu'"));
            }
            None => {}
        }
        let (export_operation, after) = match operation.operation.as_str() {
            "write" => {
                let body = operation
                    .content
                    .ok_or_else(|| format!("Contenuto mancante: {normalized}"))?
                    .into_bytes();
                total = total
                    .checked_add(body.len())
                    .ok_or_else(|| "Piano locale troppo grande".to_string())?;
                if total > 2 * 1024 * 1024 {
                    return Err("Piano locale oltre 2 MiB".to_string());
                }
                let fingerprint = Fingerprint {
                    sha256: format!("{:x}", Sha256::digest(&body)),
                    size: body.len() as u64,
                    mode: 0,
                };
                files.insert(normalized.clone(), body);
                (
                    if before.is_some() { "modify" } else { "create" }.to_string(),
                    Some(fingerprint),
                )
            }
            "delete" => {
                if before.is_none() {
                    return Err(format!("File da eliminare non presente: {normalized}"));
                }
                if operation.content.is_some() {
                    return Err(format!("Delete con contenuto inatteso: {normalized}"));
                }
                ("delete".to_string(), None)
            }
            _ => {
                return Err(format!(
                    "Operazione locale non supportata: {}",
                    operation.operation
                ))
            }
        };
        entries.push(ExportEntry {
            path: normalized,
            operation: export_operation,
            before,
            after,
        });
    }
    let run_id = format!(
        "local_{}_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| "Orologio locale non valido".to_string())?
            .as_secs(),
        &Uuid::new_v4().simple().to_string()[..8]
    );
    let entry_digest = format!(
        "{:x}",
        Sha256::digest(
            serde_json::to_vec(&entries)
                .map_err(|err| format!("Piano locale non serializzabile: {err}"))?
        )
    );
    Ok((
        ExportManifest {
            schema: EXPORT_SCHEMA.to_string(),
            bridge_id: bridge_id.to_string(),
            run_id,
            entry_digest,
            decision_status: "approved".to_string(),
            entries,
        },
        files,
    ))
}

#[tauri::command]
pub async fn apply_local_workspace_plan(
    window: WebviewWindow,
    bridge_id: String,
    operations: Vec<DirectPlanOperation>,
) -> Result<ApplyReceipt, String> {
    authorize_local_ui(&window)?;
    Uuid::parse_str(&bridge_id).map_err(|_| "bridge_id locale non valido".to_string())?;
    let root = direct_workspace_root(&bridge_id)?;
    let (manifest, files) = direct_plan_export(&root, &bridge_id, operations)?;
    let recovery = apply_export(&root, &manifest, &files)?;
    let mut registry = read_registry()?;
    if let Some(current) = registry
        .workspaces
        .iter_mut()
        .find(|item| item.bridge_id == bridge_id)
    {
        current.last_applied_run = format!("{}:approved", manifest.run_id);
    }
    write_registry(&registry)?;
    Ok(ApplyReceipt {
        schema: "devin_local_workspace_apply_v1",
        status: "applied_local",
        run_id: manifest.run_id,
        decision_status: manifest.decision_status,
        files: manifest.entries.len(),
        recovery_path: recovery.to_string_lossy().into_owned(),
    })
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
    authorize_local_ui(&window)?;
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
        assert!(is_excluded_component(".next"));
        assert!(is_excluded_component("coverage"));
        assert!(is_excluded_file(Path::new(".env.local")));
        assert!(is_excluded_file(Path::new("private.key")));
        assert!(!is_excluded_file(Path::new("src/main.rs")));
    }

    #[test]
    fn direct_reader_only_indexes_known_text_formats() {
        assert!(is_probably_text_path(Path::new("src/main.tsx")));
        assert!(is_probably_text_path(Path::new("README")));
        assert!(!is_probably_text_path(Path::new("assets/logo.png")));
        assert!(!is_probably_text_path(Path::new("archive.zip")));
    }

    #[test]
    fn local_context_ignores_chat_noise_and_prefers_project_sources() {
        let terms = local_context_terms(
            "Ciao, riesci a capire cosa fa e cosa c'e da debuggare nella cartella che ti ho condiviso?",
        );
        assert_eq!(terms, vec!["capire", "condiviso", "debuggare"]);
        let source_score = context_candidate_score(
            "main.py",
            "from spyengine.core.manager import SpyManager\n",
            &terms,
        );
        let generated_score = context_candidate_score(
            "data/debug/20260705_wallapop.html",
            &"ciao che cosa ti ho condiviso ".repeat(2_000),
            &terms,
        );
        assert!(
            source_score > generated_score,
            "generated debug artifact outranked the project entrypoint"
        );
    }

    #[test]
    fn local_context_caps_each_file_so_one_dump_cannot_hide_the_project() {
        let candidates = vec![
            (200, "README.md".to_string(), "R".repeat(20_000)),
            (
                190,
                "main.py".to_string(),
                "print('project entrypoint')\n".to_string(),
            ),
        ];
        let (content, selected, truncated) = compose_direct_context(candidates, false);
        assert_eq!(selected, vec!["README.md", "main.py"]);
        assert!(content.contains("print('project entrypoint')"));
        assert!(content.contains("ESTRATTO LOCALE TRONCATO"));
        assert!(content.len() <= DIRECT_CONTEXT_MAX_CHARS);
        assert!(truncated);
    }

    #[test]
    fn direct_plan_requires_current_fingerprint_and_builds_atomic_manifest() {
        let root = std::env::temp_dir().join(format!("devin-direct-plan-{}", Uuid::new_v4()));
        fs::create_dir_all(root.join("src")).unwrap();
        let existing = root.join("src/app.js");
        fs::write(&existing, b"console.log('old');\n").unwrap();
        let before = local_fingerprint(&existing).unwrap().unwrap();
        let operations = vec![
            DirectPlanOperation {
                path: "src/app.js".to_string(),
                operation: "write".to_string(),
                content: Some("console.log('new');\n".to_string()),
                expected_sha256: Some(before.sha256.clone()),
            },
            DirectPlanOperation {
                path: "src/new.js".to_string(),
                operation: "write".to_string(),
                content: Some("export const ready = true;\n".to_string()),
                expected_sha256: None,
            },
        ];
        let (manifest, files) = direct_plan_export(
            &root,
            "12345678-1234-4234-9234-123456789abc",
            operations.clone(),
        )
        .unwrap();
        assert_eq!(manifest.entries.len(), 2);
        assert_eq!(manifest.entries[0].operation, "modify");
        assert_eq!(manifest.entries[1].operation, "create");
        assert_eq!(files["src/new.js"], b"export const ready = true;\n");

        fs::write(&existing, b"changed concurrently\n").unwrap();
        let conflict =
            direct_plan_export(&root, "12345678-1234-4234-9234-123456789abc", operations)
                .unwrap_err();
        assert!(conflict.contains("fingerprint"));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn workspace_bridge_accepts_only_local_tauri_origins() {
        for value in [
            "tauri://localhost/",
            "http://tauri.localhost/",
            "https://tauri.localhost/",
        ] {
            assert!(
                is_local_ui_url(&tauri::Url::parse(value).unwrap()),
                "rejected {value}"
            );
        }
        let development_url = tauri::Url::parse("http://127.0.0.1:1430/").unwrap();
        assert_eq!(is_local_ui_url(&development_url), cfg!(debug_assertions));
        for value in [
            "http://127.0.0.1:1431/",
            "http://192.0.2.10:5000/app",
            "https://example.test/",
            "file:///tmp/index.html",
        ] {
            assert!(
                !is_local_ui_url(&tauri::Url::parse(value).unwrap()),
                "accepted {value}"
            );
        }
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
