use std::collections::{HashMap, HashSet, VecDeque};
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use sha2::{Digest, Sha256};
use tauri::{State, WebviewWindow};
use uuid::Uuid;

use crate::local_workspace::{authorize_local_ui, direct_workspace_root};

const MAX_ARGS: usize = 64;
const MAX_ARG_CHARS: usize = 16_384;
const MAX_STREAM_BYTES: usize = 96 * 1024;
const MAX_HEAD_BYTES: usize = 72 * 1024;
const MAX_TAIL_BYTES: usize = 24 * 1024;
const MIN_TIMEOUT_SECONDS: u64 = 1;
const MAX_TIMEOUT_SECONDS: u64 = 900;

#[derive(Clone)]
struct RunningProcess {
    child: Arc<Mutex<Child>>,
    #[cfg(target_os = "windows")]
    job: Arc<WindowsJob>,
}

#[derive(Clone, Default)]
pub struct LocalExecutionState {
    running: Arc<Mutex<HashMap<String, RunningProcess>>>,
    cancelled: Arc<Mutex<HashSet<String>>>,
}

#[derive(Debug, Serialize)]
pub struct LocalCommandReceipt {
    schema: &'static str,
    command_id: String,
    command_digest: String,
    program: String,
    args: Vec<String>,
    cwd: String,
    policy: &'static str,
    exit_code: i32,
    success: bool,
    timed_out: bool,
    cancelled: bool,
    duration_ms: u128,
    stdout: String,
    stderr: String,
    stdout_bytes: usize,
    stderr_bytes: usize,
    stdout_sha256: String,
    stderr_sha256: String,
    output_truncated: bool,
}

struct CapturedStream {
    text: String,
    bytes: usize,
    sha256: String,
    truncated: bool,
}

#[cfg(target_os = "windows")]
struct WindowsJob(isize);

#[cfg(target_os = "windows")]
unsafe impl Send for WindowsJob {}

#[cfg(target_os = "windows")]
unsafe impl Sync for WindowsJob {}

#[cfg(target_os = "windows")]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
        unsafe {
            CloseHandle(self.0 as HANDLE);
        }
    }
}

fn normalized_program(raw: &str) -> Result<String, String> {
    let program = raw.trim().to_ascii_lowercase();
    if program.is_empty()
        || program.contains('/')
        || program.contains('\\')
        || program.contains(':')
        || program.contains('\0')
    {
        return Err("Programma locale non valido".to_string());
    }
    let allowed = [
        "python", "python3", "py", "pytest", "node", "npm", "npm.cmd", "npx",
        "npx.cmd", "cargo", "git", "go", "dotnet", "java", "javac", "mvn",
        "mvn.cmd", "gradle", "gradlew", "gradlew.bat", "ruff", "mypy",
    ];
    if !allowed.contains(&program.as_str()) {
        return Err(format!(
            "Programma non consentito dalla policy locale: {program}. Shell e comandi arbitrari non sono esposti"
        ));
    }
    Ok(program)
}

fn validate_args(program: &str, args: &[String]) -> Result<(), String> {
    if args.len() > MAX_ARGS {
        return Err(format!("Comando oltre il limite di {MAX_ARGS} argomenti"));
    }
    let total_chars: usize = args.iter().map(String::len).sum();
    if total_chars > MAX_ARG_CHARS
        || args.iter().any(|arg| arg.contains('\0') || arg.contains(['\r', '\n']))
    {
        return Err("Argomenti del comando non validi o troppo lunghi".to_string());
    }
    for arg in args {
        let candidate = arg.split_once('=').map(|(_, value)| value).unwrap_or(arg);
        let normalized_path = candidate.replace('\\', "/");
        let drive_absolute = candidate.as_bytes().get(1) == Some(&b':');
        if candidate.starts_with('/')
            || candidate.starts_with("\\\\")
            || drive_absolute
            || normalized_path.split('/').any(|part| part == "..")
        {
            return Err("Argomento con path fuori dal workspace non consentito".to_string());
        }
    }
    let lower: Vec<String> = args.iter().map(|arg| arg.to_ascii_lowercase()).collect();
    if matches!(program, "python" | "python3" | "py")
        && lower.iter().any(|arg| matches!(arg.as_str(), "-c" | "-i"))
    {
        return Err("Python inline/interattivo non consentito; usa un file del workspace".to_string());
    }
    if program == "node"
        && lower
            .iter()
            .any(|arg| matches!(arg.as_str(), "-e" | "--eval" | "-p" | "--print" | "-i" | "--interactive"))
    {
        return Err("Node inline/interattivo non consentito; usa un file del workspace".to_string());
    }
    if program == "git" {
        let read_only = [
            "status", "diff", "log", "show", "rev-parse", "ls-files", "grep", "blame",
            "branch", "tag", "remote",
        ];
        let action = lower.first().map(String::as_str).unwrap_or("");
        if !read_only.contains(&action) {
            return Err("Git e' disponibile solo in lettura; le modifiche passano dal piano approvato".to_string());
        }
    }
    if program == "cargo" {
        let allowed = ["test", "check", "clippy", "fmt", "build", "metadata"];
        let action = lower.first().map(String::as_str).unwrap_or("");
        if !allowed.contains(&action) {
            return Err("Sottocomando Cargo non consentito dalla policy di verifica".to_string());
        }
    }
    if matches!(program, "npm" | "npm.cmd") {
        let action = lower.first().map(String::as_str).unwrap_or("");
        if !matches!(action, "test" | "run") {
            return Err("NPM e' limitato a test e script di verifica/build approvati".to_string());
        }
    }
    if matches!(program, "npx" | "npx.cmd") && !lower.iter().any(|arg| arg == "--no-install") {
        return Err("NPX richiede --no-install: l'agente non puo' scaricare pacchetti durante una verifica".to_string());
    }
    Ok(())
}

fn safe_cwd(root: &Path, raw: &str) -> Result<(PathBuf, String), String> {
    let value = raw.trim();
    if value.is_empty() || value == "." {
        return Ok((root.to_path_buf(), ".".to_string()));
    }
    if value.contains('\\') || value.contains(':') || value.contains('\0') {
        return Err("Directory di esecuzione non valida".to_string());
    }
    let mut relative = PathBuf::new();
    for component in Path::new(value).components() {
        let Component::Normal(part) = component else {
            return Err("Directory di esecuzione non sicura".to_string());
        };
        let name = part.to_string_lossy();
        if name.is_empty() || name == "." || name == ".." {
            return Err("Directory di esecuzione non sicura".to_string());
        }
        relative.push(part);
    }
    let target = root
        .join(&relative)
        .canonicalize()
        .map_err(|err| format!("Directory di esecuzione non disponibile: {err}"))?;
    if target == root || !target.starts_with(root) || !target.is_dir() {
        return Err("Directory di esecuzione fuori dal workspace autorizzato".to_string());
    }
    Ok((target, value.replace('\\', "/")))
}

fn resolved_command(program: &str, args: &[String]) -> (String, Vec<String>) {
    match program {
        "python" | "python3" => {
            let mut resolved = vec!["-3".to_string()];
            resolved.extend(args.iter().cloned());
            ("py.exe".to_string(), resolved)
        }
        "py" => ("py.exe".to_string(), args.to_vec()),
        "pytest" => {
            let mut resolved = vec!["-3".to_string(), "-m".to_string(), "pytest".to_string()];
            resolved.extend(args.iter().cloned());
            ("py.exe".to_string(), resolved)
        }
        "npm" | "npm.cmd" => ("npm.cmd".to_string(), args.to_vec()),
        "npx" | "npx.cmd" => ("npx.cmd".to_string(), args.to_vec()),
        "mvn" | "mvn.cmd" => ("mvn.cmd".to_string(), args.to_vec()),
        other => (other.to_string(), args.to_vec()),
    }
}

fn configure_environment(command: &mut Command) {
    const ALLOWED_ENV: &[&str] = &[
        "PATH", "PATHEXT", "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE",
        "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA", "CARGO_HOME", "RUSTUP_HOME",
    ];
    let inherited: Vec<(String, String)> = ALLOWED_ENV
        .iter()
        .filter_map(|name| std::env::var(name).ok().map(|value| ((*name).to_string(), value)))
        .collect();
    command.env_clear();
    command.envs(inherited);
    command.env("CI", "1");
    command.env("NO_COLOR", "1");
    command.env("PYTHONDONTWRITEBYTECODE", "1");
    command.env("PYTHONUNBUFFERED", "1");
    command.env("GIT_TERMINAL_PROMPT", "0");
    command.env("DEVIN_LOCAL_AGENT", "1");
}

#[cfg(target_os = "windows")]
fn hide_command_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x0800_0000);
}

#[cfg(not(target_os = "windows"))]
fn hide_command_window(_command: &mut Command) {}

#[cfg(target_os = "windows")]
fn assign_kill_on_close_job(child: &Child) -> Result<Arc<WindowsJob>, String> {
    use std::mem::size_of;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    unsafe {
        let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if handle.is_null() {
            return Err("Impossibile creare il job Windows per il comando locale".to_string());
        }
        let job = Arc::new(WindowsJob(handle as isize));
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const std::ffi::c_void,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == 0
        {
            return Err("Impossibile configurare il job Windows del comando locale".to_string());
        }
        if AssignProcessToJobObject(handle, child.as_raw_handle() as _) == 0 {
            return Err("Impossibile contenere il processo locale nel job Windows".to_string());
        }
        Ok(job)
    }
}

#[cfg(target_os = "windows")]
fn terminate_job(job: &WindowsJob) {
    use windows_sys::Win32::System::JobObjects::TerminateJobObject;
    unsafe {
        TerminateJobObject(job.0 as _, 1);
    }
}

fn capture_stream<R: Read>(mut reader: R) -> CapturedStream {
    let mut digest = Sha256::new();
    let mut total = 0usize;
    let mut head = Vec::new();
    let mut tail = VecDeque::new();
    let mut buffer = [0u8; 8192];
    loop {
        let count = match reader.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => count,
        };
        let chunk = &buffer[..count];
        total += count;
        digest.update(chunk);
        let head_room = MAX_HEAD_BYTES.saturating_sub(head.len());
        let head_count = head_room.min(chunk.len());
        head.extend_from_slice(&chunk[..head_count]);
        for byte in &chunk[head_count..] {
            tail.push_back(*byte);
            if tail.len() > MAX_TAIL_BYTES {
                tail.pop_front();
            }
        }
    }
    let truncated = total > MAX_STREAM_BYTES;
    let bytes = if truncated {
        let mut output = head;
        output.extend_from_slice(
            format!("\n[DEVIN: omessi {} byte centrali]\n", total.saturating_sub(MAX_HEAD_BYTES + MAX_TAIL_BYTES)).as_bytes(),
        );
        output.extend(tail);
        output
    } else {
        let mut output = head;
        output.extend(tail);
        output
    };
    CapturedStream {
        text: String::from_utf8_lossy(&bytes).into_owned(),
        bytes: total,
        sha256: format!("{:x}", digest.finalize()),
        truncated,
    }
}

fn terminate_running(running: &RunningProcess) {
    #[cfg(target_os = "windows")]
    terminate_job(&running.job);
    if let Ok(mut child) = running.child.lock() {
        let _ = child.kill();
    }
}

#[allow(clippy::too_many_arguments)]
fn run_command_blocking(
    state: LocalExecutionState,
    root: PathBuf,
    command_id: String,
    display_program: String,
    args: Vec<String>,
    cwd: String,
    timeout_seconds: u64,
) -> Result<LocalCommandReceipt, String> {
    let (working_directory, display_cwd) = safe_cwd(&root, &cwd)?;
    let normalized = normalized_program(&display_program)?;
    validate_args(&normalized, &args)?;
    let (program, resolved_args) = resolved_command(&normalized, &args);
    let digest_payload = serde_json::json!({
        "program": normalized,
        "args": args,
        "cwd": display_cwd,
        "timeout_seconds": timeout_seconds,
    });
    let command_digest = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&digest_payload).map_err(|err| err.to_string())?)
    );
    let mut command = Command::new(program);
    command
        .args(&resolved_args)
        .current_dir(&working_directory)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_environment(&mut command);
    hide_command_window(&mut command);
    let mut child = command
        .spawn()
        .map_err(|err| format!("Avvio comando locale fallito: {err}"))?;
    let stdout = child.stdout.take().ok_or_else(|| "stdout locale non disponibile".to_string())?;
    let stderr = child.stderr.take().ok_or_else(|| "stderr locale non disponibile".to_string())?;
    #[cfg(target_os = "windows")]
    let job = match assign_kill_on_close_job(&child) {
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
    {
        let mut processes = state.running.lock().map_err(|_| "Registro processi non disponibile")?;
        if processes.contains_key(&command_id) {
            terminate_running(&running);
            return Err("command_id locale gia' attivo".to_string());
        }
        processes.insert(command_id.clone(), running.clone());
    }
    let stdout_thread = std::thread::spawn(move || capture_stream(stdout));
    let stderr_thread = std::thread::spawn(move || capture_stream(stderr));
    let started = Instant::now();
    let mut timed_out = false;
    let status = loop {
        let current = running
            .child
            .lock()
            .map_err(|_| "Processo locale non disponibile")?
            .try_wait()
            .map_err(|err| format!("Stato comando locale non leggibile: {err}"))?;
        if let Some(status) = current {
            break status;
        }
        if started.elapsed() >= Duration::from_secs(timeout_seconds) {
            timed_out = true;
            terminate_running(&running);
        }
        if timed_out {
            let status = running
                .child
                .lock()
                .map_err(|_| "Processo locale non disponibile")?
                .wait()
                .map_err(|err| format!("Terminazione comando locale fallita: {err}"))?;
            break status;
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    state
        .running
        .lock()
        .map_err(|_| "Registro processi non disponibile")?
        .remove(&command_id);
    let cancelled = state
        .cancelled
        .lock()
        .map_err(|_| "Registro cancellazioni non disponibile")?
        .remove(&command_id);
    let stdout = stdout_thread.join().map_err(|_| "Cattura stdout fallita")?;
    let stderr = stderr_thread.join().map_err(|_| "Cattura stderr fallita")?;
    let exit_code = status.code().unwrap_or(-1);
    Ok(LocalCommandReceipt {
        schema: "devin_local_command_receipt_v1",
        command_id,
        command_digest,
        program: display_program,
        args,
        cwd: display_cwd,
        policy: "approval_gated_no_shell_v1",
        exit_code,
        success: status.success() && !timed_out && !cancelled,
        timed_out,
        cancelled,
        duration_ms: started.elapsed().as_millis(),
        stdout: stdout.text,
        stderr: stderr.text,
        stdout_bytes: stdout.bytes,
        stderr_bytes: stderr.bytes,
        stdout_sha256: stdout.sha256,
        stderr_sha256: stderr.sha256,
        output_truncated: stdout.truncated || stderr.truncated,
    })
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub async fn run_local_workspace_command(
    window: WebviewWindow,
    state: State<'_, LocalExecutionState>,
    bridge_id: String,
    command_id: String,
    program: String,
    args: Vec<String>,
    cwd: Option<String>,
    timeout_seconds: Option<u64>,
) -> Result<LocalCommandReceipt, String> {
    authorize_local_ui(&window)?;
    Uuid::parse_str(&command_id).map_err(|_| "command_id locale non valido".to_string())?;
    let root = direct_workspace_root(&bridge_id)?;
    let timeout = timeout_seconds
        .unwrap_or(180)
        .clamp(MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS);
    let execution_state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        run_command_blocking(
            execution_state,
            root,
            command_id,
            program,
            args,
            cwd.unwrap_or_default(),
            timeout,
        )
    })
    .await
    .map_err(|err| format!("Worker comando locale fallito: {err}"))?
}

#[tauri::command]
pub async fn cancel_local_workspace_command(
    window: WebviewWindow,
    state: State<'_, LocalExecutionState>,
    command_id: String,
) -> Result<serde_json::Value, String> {
    authorize_local_ui(&window)?;
    Uuid::parse_str(&command_id).map_err(|_| "command_id locale non valido".to_string())?;
    let running = state
        .running
        .lock()
        .map_err(|_| "Registro processi non disponibile")?
        .get(&command_id)
        .cloned();
    if let Some(running) = running {
        state
            .cancelled
            .lock()
            .map_err(|_| "Registro cancellazioni non disponibile")?
            .insert(command_id.clone());
        terminate_running(&running);
        return Ok(serde_json::json!({"status": "cancelling", "command_id": command_id}));
    }
    Ok(serde_json::json!({"status": "not_running", "command_id": command_id}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn execution_policy_rejects_shells_inline_code_and_mutating_git() {
        assert!(normalized_program("powershell").is_err());
        assert!(normalized_program("cmd.exe").is_err());
        assert!(validate_args("python", &["-c".into(), "print(1)".into()]).is_err());
        assert!(validate_args("node", &["--eval".into(), "1+1".into()]).is_err());
        assert!(validate_args("git", &["reset".into(), "--hard".into()]).is_err());
        assert!(validate_args("python", &["C:\\outside.py".into()]).is_err());
        assert!(validate_args("python", &["../outside.py".into()]).is_err());
        assert!(validate_args("git", &["status".into(), "--short".into()]).is_ok());
    }

    #[test]
    fn bounded_capture_preserves_head_tail_and_hashes_full_stream() {
        let input = vec![b'x'; MAX_STREAM_BYTES + 4096];
        let expected = format!("{:x}", Sha256::digest(&input));
        let captured = capture_stream(std::io::Cursor::new(input));
        assert!(captured.truncated);
        assert!(captured.text.contains("DEVIN: omessi"));
        assert_eq!(captured.sha256, expected);
        assert_eq!(captured.bytes, MAX_STREAM_BYTES + 4096);
    }
}
