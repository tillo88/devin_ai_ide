// Offline Playwright smoke for the packaged local Tauri frontend transport.
// It proves that the WebView keeps the local cockpit, streams API/SSE through
// native IPC and never navigates to the rig frontend.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const configuredPlaywright = process.env.PLAYWRIGHT_MODULE || 'playwright';
const playwrightModule = /^[A-Za-z]:[\\/]/.test(configuredPlaywright)
  ? pathToFileURL(configuredPlaywright).href
  : configuredPlaywright;
const { chromium } = await import(playwrightModule);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const bundle = path.join(root, 'src-tauri', 'frontend');
const rigOrigin = 'http://rig.test:5000';
const localOrigin = 'http://tauri.localhost';
const errors = [];
const browserExecutable = process.env.PLAYWRIGHT_EXECUTABLE_PATH || '';

await fs.access(path.join(bundle, 'index.html'));
const browser = await chromium.launch({
  headless: true,
  args: ['--disable-gpu'],
  ...(browserExecutable ? { executablePath: browserExecutable } : {}),
});
const page = await browser.newPage({ viewport: { width: 1440, height: 920 }, serviceWorkers: 'block' });
page.on('pageerror', error => errors.push(error.message));

await page.addInitScript(({ rigOrigin }) => {
  const encode = value => {
    const bytes = new TextEncoder().encode(value);
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  };
  class MockChannel {
    constructor() { this.onmessage = () => {}; }
  }
  window.__desktopInvocations = [];
  window.__nativeRequests = [];
  window.__TAURI_INTERNALS__ = { postMessage() {} };
  window.__TAURI__ = { core: {
    Channel: MockChannel,
    invoke: async (command, args) => {
      window.__desktopInvocations.push({ command, args: command === 'desktop_http_stream' ? undefined : args });
      if (command === 'desktop_config_status') {
        return { schema: 'devin_desktop_config_status_v1', configured: true, frontdoor_url: rigOrigin };
      }
      if (command === 'connect_frontdoor') {
        return { schema: 'devin_desktop_connection_v1', api_base: rigOrigin };
      }
      if (command === 'select_and_sync_local_workspace') {
        throw 'Backend registrazione: HTTP 503';
      }
      if (command === 'desktop_http_cancel') return true;
      if (command !== 'desktop_http_stream') throw new Error(`unexpected desktop command: ${command}`);

      const { request, onEvent } = args;
      window.__nativeRequests.push(request);
      let status = 200;
      let contentType = 'application/json';
      let body = '{}';
      if (request.path === '/control/activate') {
        status = 202;
        body = JSON.stringify({ ready: true, state: 'ready', phase: 'ready', eta_seconds: 0, units: { session: 'active' } });
      } else if (request.path === '/api/workspace/projects') {
        body = JSON.stringify({ projects: [] });
      } else if (request.path === '/api/runs') {
        body = '[]';
      } else if (request.path === '/api/operations/active') {
        body = JSON.stringify({ operations: [] });
      } else if (request.path === '/events') {
        contentType = 'text/event-stream';
        body = 'data: {"status":"ready"}\n\nevent: done\ndata: {"status":"complete"}\n\n';
      }
      queueMicrotask(() => {
        onEvent.onmessage({ kind: 'head', status, status_text: 'OK', headers: [['content-type', contentType]] });
        onEvent.onmessage({ kind: 'chunk', data: encode(body) });
        onEvent.onmessage({ kind: 'done' });
      });
    },
  } };
}, { rigOrigin });

await page.route('**/*', async route => {
  const url = new URL(route.request().url());
  if (url.origin !== localOrigin) return route.abort();
  const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\//, '');
  const candidate = path.resolve(bundle, relative);
  if (!candidate.startsWith(bundle + path.sep) && candidate !== path.join(bundle, 'index.html')) {
    return route.abort();
  }
  try {
    const body = await fs.readFile(candidate);
    const contentType = candidate.endsWith('.html') ? 'text/html'
      : candidate.endsWith('.js') ? 'text/javascript'
        : candidate.endsWith('.css') ? 'text/css' : 'application/octet-stream';
    return route.fulfill({ body, contentType });
  } catch {
    return route.fulfill({ status: 404, body: '' });
  }
});

try {
  await page.goto(`${localOrigin}/`);
  await page.waitForFunction(() => window.__desktopInvocations.some(call => call.command === 'connect_frontdoor'));
  await page.waitForFunction(() => {
    const overlay = document.querySelector('#devin-boot-overlay');
    return !overlay || overlay.textContent.includes("DEVIN non e' raggiungibile");
  });
  const startupFailure = await page.evaluate(() => document.querySelector('#devin-boot-overlay')?.textContent || '');
  assert.equal(startupFailure, '', `desktop startup failed: ${startupFailure}`);
  await page.locator('.topbar').waitFor({ state: 'visible' });
  assert.equal(new URL(page.url()).origin, localOrigin, 'desktop navigated away from its local bundle');
  assert.equal(await page.locator('body').getByText('Agent Swarm', { exact: true }).count(), 0);
  assert.ok(await page.locator('body').getByText('Ruoli del rig', { exact: true }).count() > 0);

  await page.locator('#link-folder-button').click();
  await page.waitForFunction(
    () => document.querySelector('#chat-thread')?.textContent.includes('[error] Backend registrazione: HTTP 503'),
    undefined,
    { timeout: 5000 },
  );
  const folderError = await page.locator('#chat-thread').textContent();
  assert.match(folderError, /\[error\] Backend registrazione: HTTP 503/);
  assert.doesNotMatch(folderError, /\[error\] undefined/);

  await page.waitForFunction(() => window.__DEVIN_TRANSPORT__?.schema === 'devin_desktop_transport_v1');
  assert.equal(await page.evaluate(() => JSON.stringify(window.__DEVIN_TRANSPORT__).includes('Bearer ')), false);
  assert.equal(await page.evaluate(() => JSON.stringify(window.__DEVIN_TRANSPORT__).includes('token')), false);

  const done = await page.evaluate(() => new Promise((resolve, reject) => {
    const stream = window.__DEVIN_TRANSPORT__.createEventSource('/events');
    stream.addEventListener('done', event => {
      stream.close();
      resolve(event.data);
    });
    stream.onerror = event => reject(event.error || new Error('SSE transport failed'));
  }));
  assert.equal(JSON.parse(done).status, 'complete');

  const actual = await page.evaluate(() => window.__nativeRequests);
  assert.ok(actual.length > 1, 'no native API traffic observed');
  assert.ok(actual.some(request => request.path === '/control/activate'), 'lifecycle activation was not requested');
  assert.ok(actual.some(request => request.path === '/events'), 'native SSE was not exercised');
  for (const request of actual) {
    assert.equal(request.path.startsWith('/'), true, `non-relative backend path: ${request.path}`);
    assert.equal(new URL(request.path, rigOrigin).searchParams.has('token'), false, `token query used for ${request.path}`);
    assert.equal(Object.hasOwn(request, 'authorization'), false, `credential crossed into JS for ${request.path}`);
  }
  assert.deepEqual(errors, []);
  console.log('PASS: local cockpit retained; API/SSE stream through native Tauri transport; no remote frontend navigation');
} finally {
  await browser.close();
}
