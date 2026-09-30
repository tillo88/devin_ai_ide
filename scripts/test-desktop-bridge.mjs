// Read-only smoke for the real Tauri -> remote-page IPC bridge.
// Start DEVIN with a temporary WebView2 remote-debugging port, then run with:
//   DEVIN_WEBVIEW_DEBUG_PORT=<port>
//   PLAYWRIGHT_MODULE=<absolute path to playwright/index.mjs>
// The script never prints the page URL (which can briefly contain a token).

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
      return new URL(page.url()).pathname === '/app';
    } catch {
      return false;
    }
  });
  assert.ok(appPage, 'Pagina DEVIN /app non trovata nel WebView2');

  const result = await appPage.evaluate(async () => {
    const hasIsTauri = window.isTauri === true;
    const hasInternals = typeof window.__TAURI_INTERNALS__?.postMessage === 'function';
    const hasGlobalInvoke = typeof window.__TAURI__?.core?.invoke === 'function';
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

    return { hasIsTauri, hasInternals, hasGlobalInvoke, commandReachedRust };
  });

  assert.deepEqual(result, {
    hasIsTauri: true,
    hasInternals: true,
    hasGlobalInvoke: true,
    commandReachedRust: true,
  });
  console.log('PASS: remote page -> Tauri IPC -> Rust ACL/validation; no URL or token emitted');
} finally {
  await browser.close();
}
