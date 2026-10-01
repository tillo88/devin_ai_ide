// Read-only smoke for the real local Tauri cockpit -> IPC bridge.
// Start DEVIN with a temporary WebView2 remote-debugging port, then run with:
//   DEVIN_WEBVIEW_DEBUG_PORT=<port>
//   PLAYWRIGHT_MODULE=<absolute path to playwright/index.mjs>
// The script never prints the configured rig origin.

import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const configuredPlaywright = process.env.PLAYWRIGHT_MODULE || 'playwright';
const playwrightModule = /^[A-Za-z]:[\\/]/.test(configuredPlaywright)
  ? pathToFileURL(configuredPlaywright).href
  : configuredPlaywright;
const port = Number(process.env.DEVIN_WEBVIEW_DEBUG_PORT || 0);

assert.ok(Number.isInteger(port) && port >= 1024 && port <= 65535,
  'DEVIN_WEBVIEW_DEBUG_PORT deve essere una porta locale valida');

const { chromium } = await import(playwrightModule);
const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);

try {
  const pages = browser.contexts().flatMap(context => context.pages());
  const appPage = pages.find(page => {
    try {
      const url = new URL(page.url());
      const isPackagedOrigin = url.hostname === 'tauri.localhost' || url.protocol === 'tauri:';
      const isDevelopmentOrigin = url.protocol === 'http:'
        && ['127.0.0.1', 'localhost'].includes(url.hostname)
        && url.port === '1430';
      return isPackagedOrigin || isDevelopmentOrigin;
    } catch {
      return false;
    }
  });
  assert.ok(appPage, 'Cockpit DEVIN locale non trovato nel WebView2');

  const result = await appPage.evaluate(async () => {
    const hasIsTauri = window.isTauri === true;
    const hasInternals = typeof window.__TAURI_INTERNALS__?.postMessage === 'function';
    const hasGlobalInvoke = typeof window.__TAURI__?.core?.invoke === 'function';
    const hasLocalTransport = window.__DEVIN_TRANSPORT__?.schema === 'devin_desktop_transport_v1';
    const failureDetail = document.querySelector('.devin-boot-detail')?.textContent?.trim() || null;
    const uiState = {
      overlayTitle: document.getElementById('devin-boot-title')?.textContent?.trim() || null,
      phase: document.getElementById('devin-boot-phase')?.textContent?.trim() || null,
      cockpitVisible: !document.getElementById('devin-boot-overlay'),
      failureDetail: failureDetail?.replace(/https?:\/\/\S+/g, '<frontdoor>') || null,
    };
    let commandReachedRust = false;

    if (hasGlobalInvoke) {
      try {
        await window.__TAURI__.core.invoke('sync_local_workspace', {
          bridgeId: 'bridge-probe-invalid',
        });
      } catch (error) {
        commandReachedRust = String(error).includes('bridge_id locale non valido');
      }
    }

    return { hasIsTauri, hasInternals, hasGlobalInvoke, hasLocalTransport, commandReachedRust, uiState };
  });

  assert.deepEqual({
    hasIsTauri: result.hasIsTauri,
    hasInternals: result.hasInternals,
    hasGlobalInvoke: result.hasGlobalInvoke,
    hasLocalTransport: result.hasLocalTransport,
    commandReachedRust: result.commandReachedRust,
  }, {
    hasIsTauri: true,
    hasInternals: true,
    hasGlobalInvoke: true,
    hasLocalTransport: true,
    commandReachedRust: true,
  });
  console.log(`STATE: ${JSON.stringify(result.uiState)}`);
  console.log('PASS: local cockpit -> trusted-LAN API transport + Tauri IPC -> Rust validation; no endpoint emitted');
} finally {
  await browser.close();
}
