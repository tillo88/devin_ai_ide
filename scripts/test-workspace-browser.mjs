// Offline browser replay: the real shell and modules, with fixture API replies.
// Run with Node >=20 and PLAYWRIGHT_MODULE pointing to an installed playwright module.
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
const output = process.env.WORKSPACE_SCREENSHOTS;
const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
const errors = [];
const writes = [];
let delayAlpha = false;
let alphaFinished;
let runsUnavailable = false;
page.on('pageerror', error => errors.push(error.message));
async function assertActiveView(expected) {
  const active = await page.locator('.workspace-mode-button.active').evaluateAll(buttons => buttons.map(button => button.dataset.centerView));
  const pressed = await page.locator('.workspace-mode-button[aria-pressed="true"]').evaluateAll(buttons => buttons.map(button => button.dataset.centerView));
  assert.deepEqual(active, [expected], `active tab mismatch for ${expected}`);
  assert.deepEqual(pressed, [expected], `aria-pressed mismatch for ${expected}`);
}
await page.addInitScript(() => {
  window.EventSource = undefined;
  window.__bridgeCalls = [];
  window.__TAURI__ = {core: {invoke: async (command, args) => {
    window.__bridgeCalls.push({command, args});
    const bridgeId = '12345678-1234-4234-9234-123456789abc';
    return {
      status: 'synced', project_path: args?.projectPath || '/projects/beta',
      local_workspace: {schema:'devin_local_workspace_snapshot_v1',bridge_id:bridgeId,display_name:'Local fixture',snapshot_digest:'ab'.repeat(32),files:3,bytes:24,updated_at:'2026-09-29T10:00:00Z'},
    };
  }}};
});
const goal = project => ({ goal_run_id: `goal_${project}`, status: 'passed', objective: `Obiettivo ${project}`, acceptance: [{type: 'tests_pass', params: {}}], evaluation: {results: [{passed: true, detail: 'Fixture: test eseguiti'}]}, budget_steps: 12, budget_seconds: 1800, attempts: [], started_at: '2026-09-19T10:00:00Z', finished_at: '2026-09-19T10:02:00Z' });
await page.route('**/*', async route => {
  const u = new URL(route.request().url());
  if (u.pathname === '/app') {
    const html = (await fs.readFile(path.join(root, 'devin/ui/templates/codex_app.html'), 'utf8')).replaceAll('{{ shell_version }}', 'browser-test');
    return route.fulfill({ contentType: 'text/html', body: html });
  }
  if (u.pathname.startsWith('/static/')) {
    const f = path.resolve(root, 'devin/ui', '.' + u.pathname);
    if (!f.startsWith(path.join(root, 'devin/ui/static') + path.sep)) return route.abort();
    try {
      const body = await fs.readFile(f);
      return route.fulfill({ contentType: f.endsWith('.js') ? 'text/javascript' : f.endsWith('.css') ? 'text/css' : 'application/octet-stream', body });
    } catch { return route.fulfill({ status: 404, body: '' }); }
  }
  let data = {};
  if (route.request().method() !== 'GET') {
    writes.push({path: u.pathname, data: route.request().postDataJSON()});
    data = {goal_run_id:'goal_fixture'};
  } else if (u.pathname === '/api/workspace/projects') {
    data = {projects: [
      {name: 'Alpha', path: '/projects/alpha'},
      {name: 'Beta', path: '/projects/beta', work_dir:'/backend/opaque-digest', local_workspace:{schema:'devin_local_workspace_snapshot_v1',bridge_id:'12345678-1234-4234-9234-123456789abc',display_name:'Local fixture',snapshot_digest:'ab'.repeat(32),files:3,bytes:24}},
    ]};
  } else if (u.pathname === '/api/goal') {
    const project = u.searchParams.get('project_path');
    const alpha = project === '/projects/alpha';
    data = {goal_runs: project ? [goal(alpha ? 'alpha' : 'beta')] : [], any_active: false};
    if (alpha && delayAlpha) {
      delayAlpha = false;
      await new Promise(resolve => setTimeout(resolve, 500));
      await route.fulfill({json:data});
      alphaFinished?.();
      return;
    }
  } else if (u.pathname === '/api/runs') {
    if (runsUnavailable) return route.fulfill({status:503, json:{error:'fixture unavailable'}});
    data = [{run_id:'run_fixture',status:'failed',mtime:'2026-09-19T10:00:00Z'}];
  } else if (u.pathname === '/api/terminal/output') {
    data = {output:'[INFO] Starting bounded verification\n[WARNING] Retry budget at 50%\n[ERROR] Fixture failure\n[INFO] Evidence preserved',lines_returned:4,file_size:124};
  } else if (u.pathname.endsWith('/events')) {
    data = {events:[{seq:1,run_id:'run_fixture',type:'test_failure',level:'error',message:'La suite fixture non passa',timestamp:'2026-09-19T10:00:00Z',data:{status:'failed'}}]};
  } else if (u.pathname === '/api/project/overview') {
    const local = u.searchParams.get('project_path') === '/projects/beta';
    data = {chats:[],pins:[],knowledge:[],work_dir:local?'/backend/opaque-digest':'',description:'Progetto di prova',local_workspace:local?{schema:'devin_local_workspace_snapshot_v1',bridge_id:'12345678-1234-4234-9234-123456789abc',display_name:'Local fixture',snapshot_digest:'ab'.repeat(32),files:3,bytes:24}:null};
  } else if (u.pathname === '/api/project/tree') {
    const local = u.searchParams.get('project_path') === '/projects/beta';
    data = local
      ? {scope:'work_dir',count:3,files:[
          {name:'main.py',path:'src/main.py',is_text:true,size:108},
          {name:'theme.css',path:'src/theme.css',is_text:true,size:240},
          {name:'README.md',path:'README.md',is_text:true,size:96},
        ]}
      : {scope:'project',count:0,files:[]};
  } else if (u.pathname === '/api/project/file') {
    data = {language:'python',size:108,truncated:false,content:'from pathlib import Path\n\n\ndef workspace_name(root: Path) -> str:\n    return root.resolve().name\n'};
  } else if (u.pathname === '/api/project/last_run') {
    data = u.searchParams.get('project_path') === '/projects/beta'
      ? {run_id:'run_review',status:'awaiting_approval',change_manifest_status:'pending'}
      : {};
  } else if (u.pathname === '/api/run/changes/run_review') {
    data = {
      schema:'change_manifest_v1',status:'pending',entry_digest:'cd'.repeat(32),truncated:false,
      counts:{create:1,modify:1,delete:0},
      entries:[
        {path:'src/main.py',operation:'modify',before_size:74,after_size:108,binary:false},
        {path:'tests/test_workspace.py',operation:'create',before_size:0,after_size:132,binary:false},
      ],
      unified_diff:'--- a/src/main.py\n+++ b/src/main.py\n@@ -1,3 +1,6 @@\n from pathlib import Path\n \n-def workspace_name(root):\n+def workspace_name(root: Path) -> str:\n+    """Return the canonical workspace label."""\n     return root.resolve().name\n--- /dev/null\n+++ b/tests/test_workspace.py\n@@ -0,0 +1,4 @@\n+from pathlib import Path\n+from src.main import workspace_name\n+\n+assert workspace_name(Path("demo")) == "demo"\n',
    };
  } else if (u.pathname === '/api/knowledge-exchange/status') {
    data = {schema:'knowledge_exchange_v1',storage:'store revisionato separato',counts:{quarantine:2,promoted:8,rejected:1,revoked:0}};
  } else if (u.pathname === '/api/council/status') {
    data = {axes:['correctness','security','tests','maintainability'],covered_axes:['correctness','tests','maintainability'],missing_axes:['security']};
  } else if (u.pathname === '/api/routing/status') {
    data = {roles:{scaffolder:{enabled:true,capabilities:['coding','scaffold'],lifecycle_owner:'backend'},tester:{enabled:true,capabilities:['test','debug'],lifecycle_owner:'backend'},researcher:{enabled:false,future:true,capabilities:['research'],lifecycle_owner:'unassigned'}}};
  } else if (u.pathname === '/api/tools/status') {
    data = {policy:{default:'deny',scope:'project'},external_mcp:{status:'disabled',registered_count:0},tools:[
      {tool_id:'project_reader',access:'read',kind:'built-in',status:'enabled',endpoints:['/api/project/tree','/api/project/file'],guards:['project scope','bounded'],budgets:{max_files:10000,max_preview_bytes:262144}},
      {tool_id:'change_manifest',access:'review',kind:'built-in',status:'enabled',endpoints:['/api/run/changes'],guards:['digest required','manual approval'],budgets:{max_diff_chars:200000}},
    ]};
  } else if (u.pathname === '/api/operations/active') {
    data = {operations:[{operation_id:'goal_fixture_operation',kind:'goal',status:'running',requested_role:'tester',dispatch:{actor:'tester',phase:'verify',attempt_index:1}}]};
  } else if (/history|chats$/.test(u.pathname)) {
    data = {messages:[],chats:[]};
  }
  return route.fulfill({json:data});
});
try {
  await page.goto('http://workspace.test/app');
  await page.locator('[data-project-path="/projects/alpha"]').waitFor();
  await assertActiveView('chat');
  assert.equal(await page.locator('#goal-start-button').isDisabled(), true);
  await page.locator('[data-project-path="/projects/alpha"]').click();
  await page.waitForFunction(() => document.querySelector('#active-scope-label').textContent === 'alpha');
  if (output) { await fs.mkdir(output,{recursive:true}); await page.screenshot({path:path.join(output,'chat-desktop.png')}); }
  await page.locator('#show-goal-view').click();
  await assertActiveView('goal');
  assert.equal(await page.locator('#goal-start-button').isEnabled(), true);
  await page.waitForFunction(() => document.querySelector('#goal-objective').textContent === 'Obiettivo alpha');
  assert.equal(await page.locator('#chat-thread').isVisible(), false);
  assert.equal(await page.locator('#goal-panel').isVisible(), true);
  assert.equal(await page.locator('#show-goal-view').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#goal-start-button').isEnabled(), true);
  if (output) { await fs.mkdir(output,{recursive:true}); await page.screenshot({path:path.join(output,'goal-desktop.png')}); }
  // An old project's late response must not replace the new project's goal.
  delayAlpha = true;
  const finished = new Promise(resolve => { alphaFinished = resolve; });
  await page.locator('#show-goal-view').click();
  await page.locator('[data-project-path="/projects/beta"]').click();
  await page.locator('#show-goal-view').click();
  await page.waitForFunction(() => document.querySelector('#goal-objective').textContent === 'Obiettivo beta');
  await finished;
  assert.equal(await page.locator('#goal-objective').textContent(), 'Obiettivo beta');
  // Exercise the real form submission and preserve the selected project/policy.
  await page.locator('#goal-objective-input').fill('Implementa il parser');
  await page.locator('[data-goal-preset="tests"]').click();
  await page.locator('#goal-start-button').click();
  await page.waitForFunction(() => document.querySelector('#goal-form-feedback').textContent.includes('Goal avviato'));
  assert.equal(writes.length,1);
  assert.equal(writes[0].path,'/api/goal/run');
  assert.equal(writes[0].data.project_path,'/projects/beta');
  assert.equal(writes[0].data.approval_policy,'manual');
  assert.equal(writes[0].data.acceptance[0].type,'tests_pass');
  await page.locator('#show-runs-view').click();
  await assertActiveView('runs');
  await page.locator('[data-run-id="run_fixture"]').waitFor();
  await page.locator('#runs-search').fill('absent');
  assert.equal(await page.locator('#run-list button').count(),0);
  await page.locator('#runs-search').fill('failed');
  await page.locator('[data-run-id="run_fixture"]').click();
  await page.waitForFunction(() => document.querySelector('#run-log-output').textContent.includes('Fixture failure'));
  assert.equal(await page.locator('#run-log-workspace').isVisible(),true);
  await assertActiveView('log');
  assert.equal(await page.locator('#manifest-diff-apply').isDisabled(),true);
  if (output) await page.screenshot({path:path.join(output,'log-desktop.png')});
  await page.locator('#show-runs-view').click();
  await assertActiveView('runs');
  if (output) await page.screenshot({path:path.join(output,'runs-desktop.png')});
  await page.locator('#activity-run [data-review-change-run]').click();
  await page.waitForFunction(() => document.querySelector('#manifest-diff-workspace').hidden === false);
  assert.equal(await page.locator('#manifest-diff-apply').isEnabled(),true);
  await assertActiveView('diff');
  if (output) await page.screenshot({path:path.join(output,'diff-desktop.png')});
  await page.locator('#show-governance-view').click();
  await page.locator('#governance-agent-grid .governance-role-card').first().waitFor();
  await assertActiveView('governance');
  if (output) await page.screenshot({path:path.join(output,'governance-desktop.png')});
  await page.locator('[data-project-file="src/main.py"]').click();
  await page.waitForFunction(() => document.querySelector('#editor-content').textContent.includes('workspace_name'));
  await assertActiveView('editor');
  if (output) await page.screenshot({path:path.join(output,'editor-desktop.png')});
  runsUnavailable = true;
  await page.locator('#show-runs-view').click();
  await page.locator('#refresh-runs').click();
  await page.waitForFunction(() => document.querySelector('#runs-feedback').textContent.includes('non disponibile'));
  assert.equal(await page.locator('#run-list button').count(),0);
  // The remote page can invoke only the narrow Tauri bridge: JS passes an
  // opaque id/project, never an arbitrary Windows path.
  await page.locator('[data-project-path="/projects/beta"]').click();
  await page.waitForFunction(() => document.querySelector('#workdir-set-button').textContent === 'Sincronizza');
  await page.locator('#workdir-set-button').click();
  await page.waitForFunction(() => window.__bridgeCalls.length === 1);
  const bridgeCall = await page.evaluate(() => window.__bridgeCalls[0]);
  assert.equal(bridgeCall.command,'sync_local_workspace');
  assert.equal(bridgeCall.args.bridgeId,'12345678-1234-4234-9234-123456789abc');
  assert.equal(Object.hasOwn(bridgeCall.args,'path'),false);
  assert.equal(await page.locator('#workdir-box').textContent().then(text => text.includes('/backend/')),false);
  await page.locator('[data-project-file="src/main.py"]').click();
  await page.waitForFunction(() => document.querySelector('#editor-content').textContent.includes('workspace_name'));
  await page.locator('#activity-run [data-review-change-run]').click();
  await page.waitForFunction(() => document.querySelector('#manifest-diff-workspace').hidden === false);
  for (const width of [1000, 390]) {
    await page.setViewportSize({width,height:900});
    await page.locator('#show-chat-view').click();
    await assertActiveView('chat');
    assert.equal(await page.locator('#chat-thread').isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `chat overflow at ${width}`);
    if (output) await page.screenshot({path:path.join(output,`chat-${width}.png`)});
    await page.locator('#show-goal-view').click();
    await assertActiveView('goal');
    await page.locator('#goal-launcher').evaluate(el => { el.open = true; });
    assert.equal(await page.locator('#goal-panel').isVisible(),true);
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) {
      console.error(await page.evaluate(() => [...document.querySelectorAll('body *')].filter(el => { const b=el.getBoundingClientRect(); return b.width && b.right>innerWidth+1; }).map(el => ({tag:el.tagName,id:el.id,cls:el.className,right:el.getBoundingClientRect().right})).slice(0,20)));
      if (output) await page.screenshot({path:path.join(output,`overflow-${width}.png`)});
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,`overflow at ${width}`);
    await page.locator('#goal-start-button').scrollIntoViewIfNeeded();
    const box = await page.locator('#goal-start-button').boundingBox();
    assert.ok(box && box.x >= 0 && box.x+box.width <= width,`start button outside viewport at ${width}`);
    if (output) await page.screenshot({path:path.join(output,`goal-${width}.png`)});
    for (const [buttonId, panelId, name] of [
      ['show-editor-view','editor-workspace','editor'],
      ['show-diff-view','manifest-diff-workspace','diff'],
      ['show-log-view','run-log-workspace','log'],
      ['show-governance-view','governance-workspace','governance'],
    ]) {
      await page.locator(`#${buttonId}`).click();
      assert.equal(await page.locator(`#${panelId}`).isVisible(),true,`${name} hidden at ${width}`);
      await assertActiveView(name);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,`${name} overflow at ${width}`);
      if (name === 'diff' && width === 390) {
        assert.equal(await page.evaluate(() => {
          const row = document.querySelector('#manifest-diff-rows .diff-row');
          const viewport = document.querySelector('#manifest-diff-rows');
          return Boolean(row && viewport && row.scrollWidth <= viewport.clientWidth + 1);
        }),true,'mobile diff did not switch to the bounded stacked layout');
      }
      if (output) await page.screenshot({path:path.join(output,`${name}-${width}.png`)});
    }
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: Chat/Goal/Runs/Editor/Diff/Log/Governance, submission/race/error guards, local bridge opacity, responsive 1440/1000/390; no live APIs');
} finally { await browser.close(); }
