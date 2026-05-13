// evals/create-opening.test.mjs
// Phase 5 case 3 finding ② regression guard. `clawroom create --opening "..."`
// must atomically create the room AND post the opening message. The output
// must surface opening_id so owner-facing summaries cannot claim a "live
// room" without a posted opening.
//
// Cold subagents in case 3 routinely created rooms but skipped the opening
// post and falsely reported "I told the room ...". The atomic flag makes
// the split structurally impossible.

process.env.CLAWROOM_STATE_DIR = `/tmp/clawroom-v4-create-opening-test-${process.pid}`;

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CLI = path.resolve(__dirname, '../skill/cli/clawroom');

const { STATE_DIR } = await import('../skill/lib/types.mjs');

function startMockRelay() {
  const messages = [];
  let nextThread = 1;
  let nextMessageId = 0;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    let body = '';
    req.on('data', (c) => body += c);
    req.on('end', () => {
      if (url.pathname === '/threads/new') {
        const id = `t_test_${nextThread++}`;
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({
          thread_id: id,
          host_token: `host_${id}_token`,
          guest_token: `guest_${id}_token`,
          public_invite_url: `${baseUrl}/i/${id}/CR-TESTCODE`,
          public_message: `Send this invite to the other person's agent: ${baseUrl}/i/${id}/CR-TESTCODE`,
        }));
        return;
      }
      if (/^\/threads\/[^/]+\/messages$/.test(url.pathname) && req.method === 'POST') {
        const payload = JSON.parse(body || '{}');
        const id = nextMessageId++;
        messages.push({ id, ...payload });
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ id }));
        return;
      }
      res.writeHead(404); res.end('{}');
    });
  });
  let baseUrl = '';
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const port = /** @type {any} */ (server.address()).port;
      baseUrl = `http://127.0.0.1:${port}`;
      resolve({ server, url: baseUrl, messages });
    });
  });
}

function runCli(args, env = {}) {
  return new Promise((resolve) => {
    const child = spawn(CLI, args, {
      env: { ...process.env, ...env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '', stderr = '';
    child.stdout.on('data', (d) => stdout += d);
    child.stderr.on('data', (d) => stderr += d);
    child.on('exit', (code) => resolve({ code, stdout, stderr }));
  });
}

test('T1: create --opening atomically posts the opening + reports opening_id', async () => {
  fs.mkdirSync(STATE_DIR, { recursive: true });
  const { server, url, messages } = await startMockRelay();
  try {
    const result = await runCli(
      ['create', '--topic', 'test topic', '--goal', 'test goal',
       '--opening', 'Hi peer — opening message from host.',
       '--create-key', 'k'],
      { CLAWROOM_RELAY: url }
    );
    assert.equal(result.code, 0, `expected exit 0; stderr=${result.stderr}`);
    const out = JSON.parse(result.stdout);
    assert.equal(out.ok, true);
    assert.equal(typeof out.invite_url, 'string');
    assert.equal(typeof out.public_message, 'string');
    assert.equal(out.opening_id, 0, 'opening_id must be in output when --opening passed');
    // Mock relay must have received exactly one POST /messages with the opening text.
    assert.equal(messages.length, 1, `expected 1 posted message; got ${messages.length}`);
    assert.equal(messages[0].text, 'Hi peer — opening message from host.');
    // State file must have last_event_cursor advanced to the opening id.
    const statePath = path.join(STATE_DIR, `${out.room_id}-host.state.json`);
    const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    assert.equal(state.last_event_cursor, 0, 'cursor must advance to opening id');
  } finally {
    server.close();
  }
});

test('T2: create WITHOUT --opening does NOT post + does NOT include opening_id', async () => {
  const { server, url, messages } = await startMockRelay();
  try {
    const result = await runCli(
      ['create', '--topic', 'test topic', '--goal', 'test goal', '--create-key', 'k'],
      { CLAWROOM_RELAY: url }
    );
    assert.equal(result.code, 0);
    const out = JSON.parse(result.stdout);
    assert.equal(out.opening_id, undefined, 'opening_id must NOT appear when --opening absent');
    assert.equal(messages.length, 0, 'no message should be posted without --opening');
  } finally {
    server.close();
  }
});

test('T3: create --opening rejects text exceeding MAX_MESSAGE_TEXT_CHARS', async () => {
  const { server, url, messages } = await startMockRelay();
  try {
    // Default MAX_MESSAGE_TEXT_CHARS = 8000.
    const huge = 'x'.repeat(8001);
    const result = await runCli(
      ['create', '--topic', 't', '--goal', 'g', '--opening', huge, '--create-key', 'k'],
      { CLAWROOM_RELAY: url }
    );
    assert.notEqual(result.code, 0, 'should fail');
    assert.match(result.stderr, /MAX_MESSAGE_TEXT_CHARS/);
    // No messages should have been posted; create may or may not have happened
    // depending on order, but the test above proves the validator catches the size.
    assert.equal(messages.length, 0);
  } finally {
    server.close();
  }
});
