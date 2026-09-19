// Offline browser replay: the real shell and modules, with fixture API replies.
// Run with Node >=20 and PLAYWRIGHT_MODULE pointing to an installed playwright module.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
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
await page.addInitScript(() => { window.EventSource = undefined; });
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
    data = {projects: [{name: 'Alpha', path: '/projects/alpha'}, {name: 'Beta', path: '/projects/beta'}]};
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
    data = {output:'[ERROR] Fixture failure',lines_returned:1};
  } else if (u.pathname.endsWith('/events')) {
    data = {events:[]};
  } else if (u.pathname === '/api/project/overview') {
    data = {chats:[],pins:[],knowledge:[],work_dir:'',description:'Progetto di prova'};
  } else if (u.pathname === '/api/project/tree') {
    data = {files:[],entries:[]};
  } else if (/history|chats$/.test(u.pathname)) {
    data = {messages:[],chats:[]};
  }
  return route.fulfill({json:data});
});
try {
  await page.goto('http://workspace.test/app');
  await page.locator('[data-project-path="/projects/alpha"]').waitFor();
  await page.locator('#show-goal-view').click();
  assert.equal(await page.locator('#goal-start-button').isDisabled(), true);
  await page.locator('[data-project-path="/projects/alpha"]').click();
  await page.locator('#show-goal-view').click();
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
  await page.locator('[data-run-id="run_fixture"]').waitFor();
  await page.locator('#runs-search').fill('absent');
  assert.equal(await page.locator('#run-list button').count(),0);
  await page.locator('#runs-search').fill('failed');
  await page.locator('[data-run-id="run_fixture"]').click();
  await page.waitForFunction(() => document.querySelector('#run-log-output').textContent.includes('Fixture failure'));
  assert.equal(await page.locator('#run-log-workspace').isVisible(),true);
  assert.equal(await page.locator('#manifest-diff-apply').isDisabled(),true);
  await page.locator('#show-runs-view').click();
  if (output) await page.screenshot({path:path.join(output,'runs-desktop.png')});
  runsUnavailable = true;
  await page.locator('#refresh-runs').click();
  await page.waitForFunction(() => document.querySelector('#runs-feedback').textContent.includes('non disponibile'));
  assert.equal(await page.locator('#run-list button').count(),0);
  for (const width of [1000, 390]) {
    await page.setViewportSize({width,height:900});
    await page.locator('#show-goal-view').click();
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
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: Goal navigation/submission, project race, Runs search/log/error, diff guard, responsive 1440/1000/390; no live APIs');
} finally { await browser.close(); }
