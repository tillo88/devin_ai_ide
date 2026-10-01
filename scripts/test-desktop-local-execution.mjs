// Operator-authorized smoke for the real Tauri local command executor.
// It runs only `python --version`, then proves that a shell is rejected.
// Project names, bridge ids, local paths and the configured rig origin are
// deliberately never printed.

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
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
      return url.hostname === 'tauri.localhost' || url.protocol === 'tauri:';
    } catch {
      return false;
    }
  });
  assert.ok(appPage, 'Cockpit DEVIN locale non trovato nel WebView2');

  const probe = await appPage.evaluate(async ({ commandId, shellCommandId }) => {
    const response = await window.__DEVIN_TRANSPORT__.fetch('/api/workspace/projects', {
      headers: { Accept: 'application/json' },
    });
    const payload = await response.json();
    const direct = (payload.projects || []).find(
      project => project.local_workspace?.mode === 'direct' && project.local_workspace?.bridge_id,
    );
    if (!direct) return { skipped: true };

    const invoke = window.__TAURI__.core.invoke;
    const receipt = await invoke('run_local_workspace_command', {
      bridgeId: direct.local_workspace.bridge_id,
      commandId,
      program: 'python',
      args: ['--version'],
      cwd: '.',
      timeoutSeconds: 30,
    });
    let shellRejected = false;
    let shellError = '';
    try {
      await invoke('run_local_workspace_command', {
        bridgeId: direct.local_workspace.bridge_id,
        commandId: shellCommandId,
        program: 'powershell',
        args: ['-NoProfile'],
        cwd: '.',
        timeoutSeconds: 30,
      });
    } catch (error) {
      shellError = typeof error === 'string'
        ? error
        : String(error?.message || error?.error || error?.detail || JSON.stringify(error));
      shellRejected = /non consentito|not allowed|policy locale/i.test(shellError);
    }
    return {
      skipped: false,
      schema: receipt.schema,
      exitCode: receipt.exit_code,
      success: receipt.success,
      policy: receipt.policy,
      stdoutBytes: receipt.stdout_bytes,
      stderrBytes: receipt.stderr_bytes,
      stdoutHash: receipt.stdout_sha256,
      stderrHash: receipt.stderr_sha256,
      versionSeen: /Python\s+\d/i.test(`${receipt.stdout}\n${receipt.stderr}`),
      shellRejected,
      shellError,
    };
  }, { commandId: crypto.randomUUID(), shellCommandId: crypto.randomUUID() });

  assert.equal(probe.skipped, false, 'nessun workspace Windows direct registrato');
  assert.equal(probe.schema, 'devin_local_command_receipt_v1');
  assert.equal(probe.exitCode, 0);
  assert.equal(probe.success, true);
  assert.equal(probe.policy, 'approval_gated_no_shell_v1');
  assert.equal(probe.versionSeen, true);
  assert.match(probe.stdoutHash, /^[0-9a-f]{64}$/);
  assert.match(probe.stderrHash, /^[0-9a-f]{64}$/);
  assert.equal(probe.shellRejected, true, `rifiuto shell inatteso: ${probe.shellError}`);

  console.log('PASS:', JSON.stringify({
    schema: probe.schema,
    exit_code: probe.exitCode,
    policy: probe.policy,
    stdout_bytes: probe.stdoutBytes,
    stderr_bytes: probe.stderrBytes,
    shell_rejected: probe.shellRejected,
  }));
} finally {
  await browser.close();
}
