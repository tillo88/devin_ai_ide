use std::collections::HashMap;
use std::io::Read;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use reqwest::blocking::Client;
use reqwest::header::{HeaderName, HeaderValue};
use reqwest::redirect::Policy;
use serde::{Deserialize, Serialize};
use tauri::ipc::Channel;
use tauri::{State, WebviewWindow};
use uuid::Uuid;

use super::load_config;
use super::local_workspace::authorize_local_ui;

const MAX_REQUEST_BODY: usize = 120 * 1024 * 1024;
const MAX_PATH_LENGTH: usize = 4096;
const RESPONSE_CHUNK: usize = 32 * 1024;

#[derive(Clone)]
pub struct DesktopTransportState {
    client: Client,
    requests: Arc<Mutex<HashMap<Uuid, Arc<AtomicBool>>>>,
}

impl DesktopTransportState {
    pub fn new() -> Result<Self, String> {
        let client = Client::builder()
            .redirect(Policy::none())
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(900))
            .build()
            .map_err(|err| format!("Trasporto HTTP desktop non inizializzabile: {err}"))?;
        Ok(Self {
            client,
            requests: Arc::new(Mutex::new(HashMap::new())),
        })
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopHttpRequest {
    request_id: String,
    method: String,
    path: String,
    #[serde(default)]
    headers: Vec<(String, String)>,
    body_text: Option<String>,
    body_base64: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DesktopHttpEvent {
    Head {
        status: u16,
        status_text: String,
        headers: Vec<(String, String)>,
    },
    Chunk {
        data: String,
    },
    Done,
    Error {
        message: String,
    },
}

fn request_id(value: &str) -> Result<Uuid, String> {
    Uuid::parse_str(value).map_err(|_| "request_id trasporto non valido".to_string())
}

fn validated_body(request: &DesktopHttpRequest) -> Result<Option<Vec<u8>>, String> {
    if request.body_text.is_some() && request.body_base64.is_some() {
        return Err("Body desktop ambiguo".to_string());
    }
    let body = if let Some(text) = &request.body_text {
        text.as_bytes().to_vec()
    } else if let Some(encoded) = &request.body_base64 {
        let encoded_limit = (MAX_REQUEST_BODY * 4 / 3) + 8;
        if encoded.len() > encoded_limit {
            return Err("Body desktop oltre il limite".to_string());
        }
        BASE64
            .decode(encoded)
            .map_err(|_| "Body desktop base64 non valido".to_string())?
    } else {
        return Ok(None);
    };
    if body.len() > MAX_REQUEST_BODY {
        return Err("Body desktop oltre il limite".to_string());
    }
    Ok(Some(body))
}

fn allowed_method(value: &str) -> Result<reqwest::Method, String> {
    match value.to_ascii_uppercase().as_str() {
        "GET" => Ok(reqwest::Method::GET),
        "POST" => Ok(reqwest::Method::POST),
        "PUT" => Ok(reqwest::Method::PUT),
        "PATCH" => Ok(reqwest::Method::PATCH),
        "DELETE" => Ok(reqwest::Method::DELETE),
        "HEAD" => Ok(reqwest::Method::HEAD),
        _ => Err("Metodo HTTP desktop non consentito".to_string()),
    }
}

fn allowed_header(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "accept" | "content-type" | "cache-control"
    )
}

fn send_error(channel: &Channel<DesktopHttpEvent>, message: impl Into<String>) {
    let _ = channel.send(DesktopHttpEvent::Error {
        message: message.into(),
    });
}

fn run_request(
    state: &DesktopTransportState,
    request: DesktopHttpRequest,
    cancel: &AtomicBool,
    channel: &Channel<DesktopHttpEvent>,
) -> Result<(), String> {
    let config = load_config()?;
    if request.path.len() > MAX_PATH_LENGTH || !request.path.starts_with('/') {
        return Err("Percorso API desktop non valido".to_string());
    }
    let target = config
        .frontdoor_url
        .join(&request.path)
        .map_err(|_| "Percorso API desktop non valido".to_string())?;
    if target.origin() != config.frontdoor_url.origin()
        || target
            .query_pairs()
            .any(|(key, _)| key.eq_ignore_ascii_case("token"))
    {
        return Err("Richiesta desktop fuori dal frontdoor configurato".to_string());
    }
    let method = allowed_method(&request.method)?;
    let body = validated_body(&request)?;
    if cancel.load(Ordering::Relaxed) {
        return Err("Richiesta desktop annullata".to_string());
    }

    let mut outbound = state.client.request(method, target);
    for (name, value) in &request.headers {
        if !allowed_header(name) {
            continue;
        }
        let name = HeaderName::from_bytes(name.as_bytes())
            .map_err(|_| "Header desktop non valido".to_string())?;
        let value = HeaderValue::from_str(value)
            .map_err(|_| "Valore header desktop non valido".to_string())?;
        outbound = outbound.header(name, value);
    }
    if let Some(body) = body {
        outbound = outbound.body(body);
    }

    let mut response = outbound
        .send()
        .map_err(|err| format!("Richiesta al frontdoor fallita: {err}"))?;
    let headers = response
        .headers()
        .iter()
        .filter(|(name, _)| {
            !matches!(
                name.as_str(),
                "set-cookie" | "connection" | "transfer-encoding"
            )
        })
        .filter_map(|(name, value)| {
            value
                .to_str()
                .ok()
                .map(|value| (name.as_str().to_string(), value.to_string()))
        })
        .collect();
    channel
        .send(DesktopHttpEvent::Head {
            status: response.status().as_u16(),
            status_text: response
                .status()
                .canonical_reason()
                .unwrap_or("")
                .to_string(),
            headers,
        })
        .map_err(|err| format!("Canale risposta desktop chiuso: {err}"))?;

    let mut buffer = vec![0_u8; RESPONSE_CHUNK];
    loop {
        if cancel.load(Ordering::Relaxed) {
            return Err("Richiesta desktop annullata".to_string());
        }
        let count = response
            .read(&mut buffer)
            .map_err(|err| format!("Lettura risposta frontdoor fallita: {err}"))?;
        if count == 0 {
            break;
        }
        channel
            .send(DesktopHttpEvent::Chunk {
                data: BASE64.encode(&buffer[..count]),
            })
            .map_err(|err| format!("Canale risposta desktop chiuso: {err}"))?;
    }
    let _ = channel.send(DesktopHttpEvent::Done);
    Ok(())
}

#[tauri::command]
pub async fn desktop_http_stream(
    window: WebviewWindow,
    state: State<'_, DesktopTransportState>,
    request: DesktopHttpRequest,
    on_event: Channel<DesktopHttpEvent>,
) -> Result<(), String> {
    authorize_local_ui(&window)?;
    let id = request_id(&request.request_id)?;
    let cancel = Arc::new(AtomicBool::new(false));
    {
        let mut active = state
            .requests
            .lock()
            .map_err(|_| "Registro richieste desktop non disponibile".to_string())?;
        if active.insert(id, cancel.clone()).is_some() {
            return Err("request_id trasporto duplicato".to_string());
        }
    }

    let owned_state = state.inner().clone();
    let channel = on_event.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        run_request(&owned_state, request, &cancel, &channel)
    })
    .await
    .map_err(|err| format!("Worker trasporto desktop fallito: {err}"))?;

    if let Ok(mut active) = state.requests.lock() {
        active.remove(&id);
    }
    if let Err(message) = &result {
        send_error(&on_event, message.clone());
    }
    result
}

#[tauri::command]
pub fn desktop_http_cancel(
    window: WebviewWindow,
    state: State<'_, DesktopTransportState>,
    request_id: String,
) -> Result<bool, String> {
    authorize_local_ui(&window)?;
    let id = self::request_id(&request_id)?;
    let active = state
        .requests
        .lock()
        .map_err(|_| "Registro richieste desktop non disponibile".to_string())?;
    if let Some(cancel) = active.get(&id) {
        cancel.store(true, Ordering::Relaxed);
        return Ok(true);
    }
    Ok(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> DesktopHttpRequest {
        DesktopHttpRequest {
            request_id: Uuid::new_v4().to_string(),
            method: "POST".to_string(),
            path: "/api/chat".to_string(),
            headers: vec![("Content-Type".to_string(), "application/json".to_string())],
            body_text: Some("{}".to_string()),
            body_base64: None,
        }
    }

    #[test]
    fn request_contract_rejects_ambiguous_or_oversized_body() {
        let mut value = request();
        value.body_base64 = Some(BASE64.encode(b"also-present"));
        assert!(validated_body(&value).is_err());

        value.body_text = None;
        value.body_base64 = Some("A".repeat((MAX_REQUEST_BODY * 4 / 3) + 9));
        assert!(validated_body(&value).is_err());
    }

    #[test]
    fn method_and_header_allowlists_fail_closed() {
        assert!(allowed_method("GET").is_ok());
        assert!(allowed_method("OPTIONS").is_err());
        assert!(allowed_header("Content-Type"));
        assert!(!allowed_header("Authorization"));
        assert!(!allowed_header("Host"));
    }
}
