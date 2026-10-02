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
const browserExecutable = process.env.PLAYWRIGHT_EXECUTABLE_PATH || '';
const browser = await chromium.launch({
  headless: true,
  args: ['--disable-gpu'],
  ...(browserExecutable ? { executablePath: browserExecutable } : {}),
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
const errors = [];
const writes = [];
let delayAlpha = false;
let alphaFinished;
let runsUnavailable = false;
let localAgentScenario = 'plan';
page.on('pageerror', error => errors.push(error.message));
page.on('dialog', dialog => dialog.accept());
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
    if (command === 'local_workspace_tree') return {
      scope:'local_direct',count:3,truncated:false,files:[
        {name:'main.py',path:'src/main.py',is_text:true,size:108},
        {name:'theme.css',path:'src/theme.css',is_text:true,size:240},
        {name:'README.md',path:'README.md',is_text:true,size:96},
      ],
    };
    if (command === 'local_workspace_read') return {
      scope:'local_direct',path:args.path,language:'python',size:108,truncated:false,read_only:true,
      sha256:'ab'.repeat(32),
      content:'from pathlib import Path\n\n\ndef workspace_name(root: Path) -> str:\n    return root.resolve().name\n',
    };
    if (command === 'local_workspace_context') return {
      schema:'devin_local_context_v1',bridge_id:bridgeId,content:'--- FILE LOCALE: src/main.py ---\nfixture',files:['src/main.py'],truncated:false,
    };
    if (command === 'local_workspace_evidence_v2') return {
      schema:'devin_local_evidence_v2',bridge_id:bridgeId,
      content:'SCHEMA devin_local_evidence_v2\n\nREPO MAP LOCALE BOUNDED\nsrc/main.py :: def workspace_name\n\n--- COMPLETO src/main.py · righe 1-4 · SHA256 ' + 'ab'.repeat(32) + ' ---\nfrom pathlib import Path\n\ndef workspace_name(root: Path) -> str:\n    return root.resolve().name',
      files:['src/main.py'],truncated:false,
      receipt:{schema:'devin_context_receipt_v2',max_chars:7500,used_chars:420,walked_entries:4,eligible_files:3,indexed_files:3,skipped_large_files:0,selected_files:1,selected_chunks:1,omitted_files:2,map_entries:3,map_omitted:0,deduplicated_chunks:0,scan_truncated:false,query_terms:['modifica']},
    };
    if (command === 'apply_local_workspace_plan') return {
      schema:'devin_local_workspace_apply_v1',status:'applied_local',run_id:'local_fixture',decision_status:'approved',files:1,recovery_path:'C:/recovery/local_fixture',
    };
    if (command === 'run_local_workspace_command') return {
      schema:'devin_local_command_receipt_v1',command_id:args.commandId,command_digest:'ef'.repeat(32),
      program:args.program,args:args.args,cwd:args.cwd,policy:'approval_gated_no_shell_v1',exit_code:0,success:true,
      timed_out:false,cancelled:false,duration_ms:42,stdout:'1 passed',stderr:'',stdout_bytes:8,stderr_bytes:0,
      stdout_sha256:'ab'.repeat(32),stderr_sha256:'cd'.repeat(32),output_truncated:false,
    };
    if (command === 'cancel_local_workspace_command') return {status:'cancelling',command_id:args.commandId};
    return {
      status: 'registered', project_path: args?.projectPath || '/projects/beta',
      local_workspace: {schema:'devin_local_workspace_snapshot_v1',mode:'direct',bridge_id:bridgeId,display_name:'Local fixture',snapshot_digest:'',files:0,bytes:0,updated_at:'2026-09-30T10:00:00Z'},
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
  if (u.pathname === '/app/diagnostics') {
    const html = await fs.readFile(path.join(root, 'devin/ui/templates/codex_diagnostics.html'), 'utf8');
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
    const posted = route.request().postDataJSON();
    writes.push({path: u.pathname, data: posted});
    if (u.pathname === '/api/chat') {
      data = {status:'local_agent_required',message:'fixture local agent'};
    } else if (u.pathname === '/api/local-workspace/agent-capabilities') {
      data = {schema:'devin_context_budget_v2',context_tokens:8192,context_source:'fixture',intent:'plan',safety_tokens:656,minimum_output_tokens:3072,preferred_output_tokens:3686,evidence_token_budget:2750,evidence_char_budget:8250,chars_per_token_estimate:3};
    } else if (u.pathname === '/api/local-workspace/agent-once') {
      data = {status:'plan',summary:'Aggiorna fixture',operations:[{
        path:'src/main.py',operation:'write',content:'print("updated")\n',expected_sha256:'ab'.repeat(32),
      }],verification:localAgentScenario === 'run' ? {
        program:'python',args:['-m','pytest','-q'],cwd:'.',timeout_seconds:180,reason:'verifica fixture',
      } : null};
    } else if (u.pathname === '/api/local-workspace/agent-complete') {
      data = {status:'persisted',chars:posted.response?.length || 0};
    } else if (u.pathname === '/api/training/local-agent/attempt') {
      data = {schema:'devin_local_agent_training_attempt_v1',attempt:{attempt_id:'attempt_local_fixture'},review_required:true,auto_promoted:false};
    } else if (u.pathname === '/api/council/manual/prepare') {
      data = posted.redaction_approved ? {
        schema:'devin_manual_council_preparation_v1',promotion_performed:false,
        bundle:{automatic_send:false,operator_copy_required:true,prompts:Array.from({length:5},(_,index)=>({
          packet_id:`crp_${index}`,axis:`axis_${index}`,reviewer_id:['manual-codex','manual-codex','manual-claude','manual-claude','manual-gemini'][index],family:['openai','openai','anthropic','anthropic','google'][index],prompt:`fixture prompt ${index}`,
        }))},
      } : {
        schema:'devin_manual_council_preview_v1',approval_required:true,promotion_performed:false,
        packet:{attempt_id:'attempt_council_fixture',prompt:'bounded fixture',response:'verified response',evidence:[{evidence_id:'sha256:'+'a'.repeat(64)}]},
        redaction_manifest:{approved:false,automatic_send:false,operator_review_required:true},
      };
    } else {
      data = {goal_run_id:'goal_fixture'};
    }
  } else if (u.pathname === '/api/workspace/projects') {
    data = {projects: [
      {name: 'Alpha', path: '/projects/alpha'},
      {name: 'Beta', path: '/projects/beta', work_dir:'', local_workspace:{schema:'devin_local_workspace_snapshot_v1',mode:'direct',bridge_id:'12345678-1234-4234-9234-123456789abc',display_name:'Local fixture',snapshot_digest:'',files:0,bytes:0}},
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
  } else if (u.pathname === '/api/training/overview') {
    const attempt = {attempt_id:'attempt_council_fixture',case_id:'case_fixture',status:'pending_review',prompt:'bounded fixture',response:'verified response',tests:{}};
    data = {summary:{cases:1,attempts:1},cases:[],attempts:[attempt],corrections:[],reviews:[],latest_reviews:{},review_queue:[{attempt_id:attempt.attempt_id,case_id:attempt.case_id,title:'Council fixture',status:'pending_review',gate:{status:'passed',tests_run:1},validators:{overall:'pass',signals:{}}}],lessons:[],benchmarks:[],jobs:[],memory_policy:{auto_promote:false,success_statuses:['verified_success'],failure_statuses:['verified_failure'],auto_statuses:['auto_success'],infra_statuses:['runner_error']}};
  } else if (u.pathname === '/api/training/exports') {
    data = {exports:[]};
  } else if (u.pathname === '/api/terminal/output') {
    data = {output:'[INFO] Starting bounded verification\n[WARNING] Retry budget at 50%\n[ERROR] Fixture failure\n[INFO] Evidence preserved',lines_returned:4,file_size:124};
  } else if (u.pathname.endsWith('/events')) {
    data = {events:[{seq:1,run_id:'run_fixture',type:'test_failure',level:'error',message:'La suite fixture non passa',timestamp:'2026-09-19T10:00:00Z',data:{status:'failed'}}]};
  } else if (u.pathname === '/api/project/overview') {
    const local = u.searchParams.get('project_path') === '/projects/beta';
    data = {chats:[],pins:[],knowledge:[],work_dir:'',description:'Progetto di prova',local_workspace:local?{schema:'devin_local_workspace_snapshot_v1',mode:'direct',bridge_id:'12345678-1234-4234-9234-123456789abc',display_name:'Local fixture',snapshot_digest:'',files:0,bytes:0}:null};
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
  // The cockpit can invoke only the narrow Tauri bridge: JS passes an opaque
  // id/project, never an arbitrary Windows path.
  await page.locator('[data-project-path="/projects/beta"]').click();
  await page.waitForFunction(() => document.querySelector('#workdir-set-button').textContent === 'Verifica');
  await page.locator('#workdir-set-button').click();
  await page.waitForFunction(() => window.__bridgeCalls.some(call => call.command === 'sync_local_workspace'));
  const bridgeCall = await page.evaluate(() => window.__bridgeCalls.find(call => call.command === 'sync_local_workspace'));
  assert.equal(bridgeCall.command,'sync_local_workspace');
  assert.equal(bridgeCall.args.bridgeId,'12345678-1234-4234-9234-123456789abc');
  assert.equal(Object.hasOwn(bridgeCall.args,'path'),false);
  assert.equal(await page.locator('#workdir-box').textContent().then(text => text.includes('/backend/')),false);
  await page.locator('#show-chat-view').click();
  await page.locator('#chat-input').fill('Modifica il file principale');
  await page.locator('#chat-send').click();
  const planPrompt = await Promise.race([
    page.locator('.app-modal-overlay').waitFor({state:'visible'}).then(() => 'modal'),
    page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('[error]')).then(() => 'error'),
  ]);
  assert.equal(planPrompt, 'modal', await page.locator('#chat-thread').textContent());
  await page.locator('.app-modal-ok').click();
  await page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('1 file applicati atomicamente'));
  const applyCall = await page.evaluate(() => window.__bridgeCalls.find(call => call.command === 'apply_local_workspace_plan'));
  assert.equal(applyCall.args.bridgeId,'12345678-1234-4234-9234-123456789abc');
  assert.equal(applyCall.args.operations[0].path,'src/main.py');
  assert.equal(Object.hasOwn(applyCall.args,'localPath'),false);
  localAgentScenario = 'run';
  const oneShotCallsBefore = writes.filter(write => write.path === '/api/local-workspace/agent-once').length;
  await page.locator('#chat-input').fill('Esegui i test reali del progetto');
  await page.locator('#chat-send').click();
  await page.locator('.app-modal-overlay').waitFor({state:'visible'});
  assert.ok((await page.locator('.app-modal-msg').textContent()).includes('Applicare queste modifiche'));
  await page.locator('.app-modal-ok').click();
  await page.locator('.app-modal-overlay').waitFor({state:'visible'});
  assert.ok((await page.locator('.app-modal-msg').textContent()).includes('python -m pytest -q'));
  await page.locator('.app-modal-ok').click();
  await page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('Verifica: python -m pytest -q'));
  assert.equal(writes.filter(write => write.path === '/api/local-workspace/agent-once').length,oneShotCallsBefore + 1);
  const evidenceCall = await page.evaluate(() => window.__bridgeCalls.find(call => call.command === 'local_workspace_evidence_v2'));
  assert.equal(evidenceCall.args.bridgeId,'12345678-1234-4234-9234-123456789abc');
  assert.equal(evidenceCall.args.maxChars,7550);
  const oneShotPayload = writes.filter(write => write.path === '/api/local-workspace/agent-once').at(-1).data;
  assert.ok(oneShotPayload.evidence_pack.includes('SCHEMA devin_local_evidence_v2'));
  assert.ok(oneShotPayload.evidence_pack.includes('CONTEXT RECEIPT LOCALE'));
  assert.equal(oneShotPayload.evidence_pack.includes('RETRIEVAL AGGIUNTIVO BOUNDED'),false);
  const runCall = await page.evaluate(() => window.__bridgeCalls.find(call => call.command === 'run_local_workspace_command'));
  assert.equal(runCall.args.bridgeId,'12345678-1234-4234-9234-123456789abc');
  assert.equal(runCall.args.program,'python');
  assert.deepEqual(runCall.args.args,['-m','pytest','-q']);
  assert.equal(Object.hasOwn(runCall.args,'localPath'),false);
  assert.equal(await page.locator('.local-training-review').last().textContent().then(text => text.includes('Training: in review')),true);
  await page.locator('#chat-send').waitFor({state:'visible'});
  await page.waitForFunction(() => !document.querySelector('#chat-send').disabled);
  assert.equal(await page.locator('#chat-stop').isHidden(),true);
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
  // A desktop runtime without its IPC global must fail closed. It must never
  // turn a request for a Windows folder into a backend-path prompt.
  const backendPickerWritesBefore = writes.filter(write => write.path === '/api/workspace/pick_folder').length;
  await page.evaluate(() => {
    delete window.__TAURI__;
    Object.defineProperty(window, 'isTauri', {value:true, configurable:true});
    document.querySelector('#link-folder-button').click();
  });
  const failClosedOutcome = await Promise.race([
    page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('nessun percorso backend')).then(() => 'error-message'),
    page.locator('.app-modal-overlay').waitFor({state:'attached'}).then(() => 'backend-modal'),
  ]);
  assert.equal(failClosedOutcome,'error-message','desktop bridge failure did not fail closed');
  assert.equal(await page.locator('.app-modal-overlay').count(),0,'desktop bridge failure opened a backend-path modal');
  assert.equal(writes.filter(write => write.path === '/api/workspace/pick_folder').length,backendPickerWritesBefore,'desktop bridge failure called the backend picker');
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('http://workspace.test/app/diagnostics#training');
  await page.locator('[data-council-attempt="attempt_council_fixture"]').first().waitFor();
  await page.locator('[data-council-attempt="attempt_council_fixture"]').first().click();
  await page.waitForFunction(() => !document.querySelector('#council-manual-approve').disabled);
  assert.ok((await page.locator('#council-manual-preview').textContent()).includes('attempt_council_fixture'));
  await page.locator('#council-manual-approve').click();
  await page.locator('[data-council-copy]').first().waitFor();
  assert.equal(await page.locator('[data-council-copy]').count(),5);
  const councilWrites = writes.filter(write => write.path === '/api/council/manual/prepare');
  assert.deepEqual(councilWrites.map(write => write.data.redaction_approved),[false,true]);
  assert.deepEqual(errors,[]);
  console.log('PASS: Chat/Goal/Runs/Editor/Diff/Log/Governance/Diagnostics Council, submission/race/error guards, local bridge opacity/fail-closed, responsive 1440/1000/390; no live APIs');
} finally { await browser.close(); }
