// evals/harness-smoke.test.mjs
//
// Phase 4.5 deliverable: dry-run smoke against the 4 harness modules.
// Per planning room t_d8681c69-e79: "Dry-run smoke before Phase 5
// cases use the harness." This file exercises every module with
// synthetic inputs so we know the protocol + scoring + artifact
// pipeline works BEFORE we spend live subagent runs on Phase 5 cases.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

import { makeTempSkillEnv, checkContextLeak } from './harness/temp-skill.mjs';
import {
  OWNER_ASK_LINE_PREFIX,
  extractOwnerAsks,
  makeOwnerAskLine,
  buildHandback,
  verifyOwnerAskAgainstState,
} from './harness/owner-ask-protocol.mjs';
import { writeRunArtifact, readMostRecentArtifact, ARTIFACT_DIR } from './harness/artifact-writer.mjs';
import { scoreRun, isCase3ReleaseGreen } from './harness/score.mjs';

// ---------- temp-skill ----------

test('temp-skill creates isolated copy with cli + lib + references', () => {
  const env = makeTempSkillEnv({ label: 'smoke-T1' });
  try {
    assert.ok(fs.existsSync(path.join(env.skill_dir, 'cli', 'clawroom')));
    assert.ok(fs.existsSync(path.join(env.skill_dir, 'lib', 'types.mjs')));
    assert.ok(fs.existsSync(path.join(env.skill_dir, 'lib', 'state.mjs')));
    assert.ok(fs.existsSync(path.join(env.skill_dir, 'lib', 'close.mjs')));
    assert.ok(fs.existsSync(path.join(env.skill_dir, 'lib', 'relay-client.mjs')));
    assert.ok(fs.existsSync(path.join(env.skill_dir, 'lib', 'watch.mjs')));
    assert.ok(fs.existsSync(path.join(env.skill_dir, 'SKILL.md')));
    assert.ok(fs.existsSync(env.state_dir));
    assert.ok(env.skill_dir.startsWith(os.tmpdir()));
    assert.equal(env.allowed_context, 'temp_skill_copy_only');
    assert.equal(env.child_env.HOME, env.home);
    assert.equal(env.child_env.CLAWROOM_STATE_DIR, env.state_dir);
  } finally {
    env.cleanup();
    assert.equal(fs.existsSync(env.skill_dir), false, 'cleanup must remove temp tree');
  }
});

test('temp-skill: subagent CANNOT discover docs/evals/legacy via the copy', () => {
  const env = makeTempSkillEnv({ label: 'smoke-T2' });
  try {
    // Forbidden tree members must NOT exist inside the copy.
    assert.equal(fs.existsSync(path.join(env.skill_dir, 'docs')), false);
    assert.equal(fs.existsSync(path.join(env.skill_dir, 'evals')), false);
    assert.equal(fs.existsSync(path.join(env.skill_dir, 'legacy')), false);
    assert.equal(fs.existsSync(path.join(env.skill_dir, '.claude')), false);
    assert.equal(fs.existsSync(path.join(env.skill_dir, 'CLAUDE.md')), false);
  } finally {
    env.cleanup();
  }
});

test('temp-skill: checkContextLeak catches synthetic leak strings', () => {
  const stdout = 'I am going to check docs/LESSONS_LEARNED.md for prior patterns...';
  const result = checkContextLeak({ subagent_stdout: stdout });
  assert.equal(result.held, false);
  assert.ok(result.violations.some((v) => v.includes('docs/LESSONS_LEARNED')));
});

test('temp-skill: checkContextLeak passes clean transcript', () => {
  const stdout = 'Read SKILL.md. Ran ./cli/clawroom create. Got invite URL. Done.';
  const result = checkContextLeak({ subagent_stdout: stdout });
  assert.equal(result.held, true);
  assert.equal(result.violations.length, 0);
});

// ---------- OWNER_ASK protocol ----------

test('OWNER_ASK: extract single packet from a noisy transcript', () => {
  const transcript = [
    'subagent: started, reading SKILL.md',
    'subagent: ran clawroom create, got invite URL',
    `${OWNER_ASK_LINE_PREFIX}${JSON.stringify({
      type: 'OWNER_ASK',
      question_id: 'q-budget',
      question_text: 'Peer asks $720; budget ceiling is $650. Approve to exceed?',
      blocking_state: { room_id: 't_smoke', role: 'host', timeout_at: '2026-05-13T11:30:00Z' },
    })}`,
    'subagent: waiting for owner reply',
  ].join('\n');
  const packets = extractOwnerAsks(transcript);
  assert.equal(packets.length, 1);
  assert.equal(packets[0].question_id, 'q-budget');
  assert.equal(packets[0].blocking_state.role, 'host');
});

test('OWNER_ASK: malformed packet surfaces as __malformed__ placeholder', () => {
  const transcript = `${OWNER_ASK_LINE_PREFIX}{ not valid json`;
  const packets = extractOwnerAsks(transcript);
  assert.equal(packets.length, 1);
  assert.equal(packets[0].question_id, '__malformed__');
});

test('OWNER_ASK: invalid shape (missing question_id) is filtered out', () => {
  const transcript = `${OWNER_ASK_LINE_PREFIX}${JSON.stringify({ type: 'OWNER_ASK', question_text: 'no id' })}`;
  const packets = extractOwnerAsks(transcript);
  assert.equal(packets.length, 0);
});

test('OWNER_ASK: handback structure ties decision back to question_id', () => {
  const ask = {
    type: 'OWNER_ASK',
    question_id: 'q-budget',
    question_text: 'Approve exceeding ceiling?',
    blocking_state: { room_id: 't_smoke', role: 'host', timeout_at: '2026-05-13T11:30:00Z' },
  };
  const handback = buildHandback(ask, {
    decision: 'approve',
    evidence: 'Owner approved $720 exceeding budget_ceiling_usd=650 ceiling.',
    natural_language_reply: 'Yes, go ahead, the timeline is more important than the $70.',
  });
  assert.equal(handback.question_id, 'q-budget');
  assert.equal(handback.decision, 'approve');
  assert.ok(handback.evidence.includes('budget_ceiling_usd=650'));
});

test('OWNER_ASK: makeOwnerAskLine round-trips through extractOwnerAsks', () => {
  const ask = {
    type: 'OWNER_ASK',
    question_id: 'q-rt',
    question_text: 'Test',
    blocking_state: { room_id: 't_rt', role: 'guest', timeout_at: '2026-05-13T12:00:00Z' },
  };
  const line = makeOwnerAskLine(ask);
  const packets = extractOwnerAsks(line);
  assert.equal(packets.length, 1);
  assert.deepEqual(packets[0], ask);
});

// ---------- OWNER_ASK state-backed verification (Phase 4.5 review P1/P2) ----------

test('OWNER_ASK verify: rejects packet when no state file exists', () => {
  const env = makeTempSkillEnv({ label: 'smoke-verify-empty' });
  try {
    const fakePacket = {
      type: 'OWNER_ASK',
      question_id: 'q-fab',
      question_text: 'fabricated',
      blocking_state: { room_id: 't_nostate', role: 'host', timeout_at: '2026-05-13T11:30:00Z' },
    };
    const result = verifyOwnerAskAgainstState(fakePacket, { state_dir: env.state_dir });
    assert.equal(result.valid, false);
    assert.ok(result.reasons.some((r) => r.includes('state file not found')));
  } finally {
    env.cleanup();
  }
});

test('OWNER_ASK verify: accepts packet that matches state.pending_owner_ask', () => {
  const env = makeTempSkillEnv({ label: 'smoke-verify-match' });
  try {
    const state = {
      room_id: 't_real',
      role: 'host',
      host_token: 'h_x',
      last_event_cursor: 0,
      pending_owner_ask: {
        question_id: 'q-real',
        question_text: 'Approve $720?',
        asked_at: '2026-05-13T11:00:00Z',
        timeout_at: '2026-05-13T11:30:00Z',
      },
      owner_approvals: [],
      draft_close: null,
      started_at: '2026-05-13T10:00:00Z',
      last_seen_at: '2026-05-13T11:00:00Z',
    };
    fs.writeFileSync(
      path.join(env.state_dir, 't_real-host.state.json'),
      JSON.stringify(state)
    );
    const packet = {
      type: 'OWNER_ASK',
      question_id: 'q-real',
      question_text: 'Approve $720?',
      blocking_state: { room_id: 't_real', role: 'host', timeout_at: '2026-05-13T11:30:00Z' },
    };
    const result = verifyOwnerAskAgainstState(packet, { state_dir: env.state_dir });
    assert.equal(result.valid, true, `expected valid; reasons: ${JSON.stringify(result.reasons)}`);
  } finally {
    env.cleanup();
  }
});

test('OWNER_ASK verify: rejects question_id mismatch (parent-fabricated packet)', () => {
  const env = makeTempSkillEnv({ label: 'smoke-verify-mismatch' });
  try {
    const state = {
      room_id: 't_real',
      role: 'host',
      host_token: 'h_x',
      last_event_cursor: 0,
      pending_owner_ask: {
        question_id: 'q-genuine',
        question_text: 'Real question',
        asked_at: '2026-05-13T11:00:00Z',
        timeout_at: '2026-05-13T11:30:00Z',
      },
      owner_approvals: [],
      draft_close: null,
      started_at: '2026-05-13T10:00:00Z',
      last_seen_at: '2026-05-13T11:00:00Z',
    };
    fs.writeFileSync(
      path.join(env.state_dir, 't_real-host.state.json'),
      JSON.stringify(state)
    );
    const packet = {
      type: 'OWNER_ASK',
      question_id: 'q-fabricated-by-parent',
      question_text: 'Fake question',
      blocking_state: { room_id: 't_real', role: 'host', timeout_at: '2026-05-13T11:30:00Z' },
    };
    const result = verifyOwnerAskAgainstState(packet, { state_dir: env.state_dir });
    assert.equal(result.valid, false);
    assert.ok(result.reasons.some((r) => r.includes('question_id mismatch')));
  } finally {
    env.cleanup();
  }
});

test('OWNER_ASK verify: rejects packet when state has no pending_owner_ask', () => {
  const env = makeTempSkillEnv({ label: 'smoke-verify-nopending' });
  try {
    const state = {
      room_id: 't_real',
      role: 'host',
      host_token: 'h_x',
      last_event_cursor: 0,
      pending_owner_ask: null,
      owner_approvals: [],
      draft_close: null,
      started_at: '2026-05-13T10:00:00Z',
      last_seen_at: '2026-05-13T11:00:00Z',
    };
    fs.writeFileSync(
      path.join(env.state_dir, 't_real-host.state.json'),
      JSON.stringify(state)
    );
    const packet = {
      type: 'OWNER_ASK',
      question_id: 'q-fab',
      question_text: 'Fabricated',
      blocking_state: { room_id: 't_real', role: 'host', timeout_at: '2026-05-13T11:30:00Z' },
    };
    const result = verifyOwnerAskAgainstState(packet, { state_dir: env.state_dir });
    assert.equal(result.valid, false);
    assert.ok(result.reasons.some((r) => r.includes('null')));
  } finally {
    env.cleanup();
  }
});

// ---------- scoring rubric ----------

test('score: zero clarifications + mutual close + validator pass = MAGICAL', () => {
  const result = scoreRun({
    owner_clarification_count: 0,
    closed_mutually: true,
    close_validator_passed: true,
    pending_blocked_hard: false,
    create_join_confused: false,
    hung: false,
    schema_errors_present: false,
    context_leak: false,
  });
  assert.equal(result.score, 'MAGICAL');
  assert.equal(result.release_green, true);
  assert.equal(isCase3ReleaseGreen(result), true);
});

test('score: 1 clarification = GOOD, still release_green', () => {
  const result = scoreRun({
    owner_clarification_count: 1,
    closed_mutually: true,
    close_validator_passed: true,
    pending_blocked_hard: false,
    create_join_confused: false,
    hung: false,
    schema_errors_present: false,
    context_leak: false,
  });
  assert.equal(result.score, 'GOOD');
  assert.equal(result.release_green, true);
  assert.equal(isCase3ReleaseGreen(result), true);
});

test('score: 3 clarifications = OK, NOT release_green for case 3', () => {
  const result = scoreRun({
    owner_clarification_count: 3,
    closed_mutually: true,
    close_validator_passed: true,
    pending_blocked_hard: false,
    create_join_confused: false,
    hung: false,
    schema_errors_present: false,
    context_leak: false,
  });
  assert.equal(result.score, 'OK');
  assert.equal(result.release_green, false);
  assert.equal(isCase3ReleaseGreen(result), false);
});

test('score: 5 clarifications = FAILED (interrogation, not negotiation)', () => {
  const result = scoreRun({
    owner_clarification_count: 5,
    closed_mutually: true,
    close_validator_passed: true,
    pending_blocked_hard: false,
    create_join_confused: false,
    hung: false,
    schema_errors_present: false,
    context_leak: false,
  });
  assert.equal(result.score, 'FAILED');
  assert.equal(result.classification, 'product_bug');
});

test('score: context_leak overrides everything to FAILED + product_bug', () => {
  const result = scoreRun({
    owner_clarification_count: 0,
    closed_mutually: true,
    close_validator_passed: true,
    pending_blocked_hard: false,
    create_join_confused: false,
    hung: false,
    schema_errors_present: false,
    context_leak: true,
    context_leak_violations: ['referenced docs/LESSONS_LEARNED.md'],
  });
  assert.equal(result.score, 'FAILED');
  assert.equal(result.release_green, false);
  assert.equal(result.classification, 'product_bug');
  assert.ok(result.reasons.some((r) => r.includes('LESSONS_LEARNED')));
});

test('score: hung run = FAILED + runtime_limitation', () => {
  const result = scoreRun({
    owner_clarification_count: 0,
    closed_mutually: false,
    close_validator_passed: false,
    pending_blocked_hard: false,
    create_join_confused: false,
    hung: true,
    schema_errors_present: false,
    context_leak: false,
  });
  assert.equal(result.score, 'FAILED');
  assert.equal(result.classification, 'runtime_limitation');
});

test('score: schema_errors_present = FAILED + product_bug', () => {
  const result = scoreRun({
    owner_clarification_count: 0,
    closed_mutually: true,
    close_validator_passed: false,
    pending_blocked_hard: false,
    create_join_confused: false,
    hung: false,
    schema_errors_present: true,
    context_leak: false,
  });
  assert.equal(result.score, 'FAILED');
  assert.equal(result.classification, 'product_bug');
});

// ---------- artifact-writer ----------

test('artifact-writer: redacts common provider API key shapes (Phase 4.5 review round 2)', () => {
  const artifact = {
    case_number: 1,
    driver_type: 'subagent',
    owner_prompts: ['Used my Anthropic key sk-ant-abcdef1234567890_LEAKED to call the model'],
    transcript: [
      { from: 'host', kind: 'message', text: 'OpenAI key sk-abcdef1234567890_LEAKED_xyz appeared in env' },
      { from: 'host', kind: 'message', text: 'Codex sk-cp-LEAKED_codex_token_abc1234567890' },
      { from: 'host', kind: 'message', text: 'Slack xoxb-LEAKED_slack_bot_token_1234567890' },
      { from: 'host', kind: 'message', text: 'GitHub ghp_LEAKED1234567890abcdef' },
      { from: 'host', kind: 'message', text: 'xAI xai-LEAKED_grok_key_1234567890abcd' },
    ],
    state_snapshot: {},
    close_output: { ok: true },
    score: 'GOOD',
    score_detail: { reasons: [] },
    classification: 'pass',
    owner_facing_quality: {},
    allowed_context: 'temp_skill_copy_only',
    retry_index: 0,
    retry_reason: null,
    room_id: 't_apikey',
    started_at: '2026-05-13T14:00:00.000Z',
    finished_at: '2026-05-13T14:01:00.000Z',
  };
  const outPath = writeRunArtifact(artifact);
  const persisted = JSON.parse(fs.readFileSync(outPath, 'utf8'));
  const all = JSON.stringify(persisted);
  assert.equal(all.includes('LEAKED'), false, `API key leaked through redaction: ${all.match(/LEAKED[^"]{0,30}/g)?.join(', ')}`);
  assert.ok(all.includes('REDACTED'));
  fs.unlinkSync(outPath);
});

test('artifact-writer: redacts freeform token shapes in transcript text (Phase 4.5 P1)', () => {
  // Codex probe: transcript strings carry token-shaped substrings with `:` or `=` separators,
  // unquoted CR-* invite codes, and chat-id-shaped fields. v1 regex only caught JSON-shaped
  // keys; this asserts the deepRedact + broader patterns work end-to-end.
  const artifact = {
    case_number: 1,
    driver_type: 'main',
    owner_prompts: ['transcript-redaction probe'],
    transcript: [
      { from: 'host', kind: 'message', text: 'subagent logged: host_token: "h_LEAKED_xyz123"' },
      { from: 'host', kind: 'message', text: 'create_key = c_LEAKED_abc' },
      { from: 'guest', kind: 'message', text: 'joined via CR-LEAKED1234' },
      { from: 'host', kind: 'message', text: 'telegram chat_id: 987654321' },
      { from: 'host', kind: 'message', text: 'Authorization header was Bearer abc_LEAKED_token_123' },
    ],
    state_snapshot: {},
    close_output: { ok: true },
    score: 'GOOD',
    score_detail: { reasons: [] },
    classification: 'pass',
    owner_facing_quality: {},
    allowed_context: 'temp_skill_copy_only',
    retry_index: 0,
    retry_reason: null,
    room_id: 't_probe',
    started_at: '2026-05-13T13:00:00.000Z',
    finished_at: '2026-05-13T13:01:00.000Z',
  };
  const outPath = writeRunArtifact(artifact);
  const persisted = JSON.parse(fs.readFileSync(outPath, 'utf8'));
  const all = JSON.stringify(persisted);
  // Every leak-shaped substring must be gone.
  assert.equal(all.includes('h_LEAKED_xyz123'), false, 'host_token value leaked');
  assert.equal(all.includes('c_LEAKED_abc'), false, 'create_key value leaked');
  assert.equal(all.includes('CR-LEAKED1234'), false, 'CR invite code leaked');
  assert.equal(all.includes('987654321'), false, 'chat_id leaked');
  assert.equal(all.includes('abc_LEAKED_token_123'), false, 'Bearer token leaked');
  // And REDACTED placeholders must appear.
  assert.ok(all.includes('REDACTED'));
  fs.unlinkSync(outPath);
});

test('artifact-writer: writes valid JSON + redacts tokens', () => {
  const artifact = {
    case_number: 1,
    driver_type: 'main',
    owner_prompts: ['help me hire this contractor at 65000 yen ceiling'],
    transcript: [
      { from: 'host', kind: 'message', text: 'open_request_with host_token: "h_secret_xyz"' },
    ],
    state_snapshot: { host: { host_token: 'h_secret_xyz', room_id: 't_smoke' } },
    close_output: { ok: true },
    score: 'MAGICAL',
    score_detail: { reasons: ['zero clarifications'] },
    classification: 'pass',
    owner_facing_quality: { ask_quality_score: 'na', owner_summary_score: 'good' },
    allowed_context: 'temp_skill_copy_only',
    retry_index: 0,
    retry_reason: null,
    room_id: 't_smoke',
    started_at: '2026-05-13T10:00:00.000Z',
    finished_at: '2026-05-13T10:05:00.000Z',
  };
  const outPath = writeRunArtifact(artifact);
  assert.ok(fs.existsSync(outPath));
  const persisted = JSON.parse(fs.readFileSync(outPath, 'utf8'));
  // host_token should be redacted, not leaked literally
  assert.notEqual(persisted.state_snapshot.host.host_token, 'h_secret_xyz');
  assert.equal(persisted.state_snapshot.host.host_token, 'REDACTED');
  // path is under docs/progress/ (gitignored, so not committed) — Phase 4.5 contract
  assert.match(outPath, /docs\/progress\/v4_p5_case1_/);
  // cleanup so we don't pollute progress dir long-term
  fs.unlinkSync(outPath);
});

test('artifact-writer: retry_index reflected in filename', () => {
  const artifact = {
    case_number: 3,
    driver_type: 'subagent',
    owner_prompts: ['naive prompt'],
    transcript: [],
    state_snapshot: {},
    close_output: { ok: false, issues: [{ code: 'schema_x' }] },
    score: 'FAILED',
    score_detail: { reasons: ['schema_errors_present'] },
    classification: 'product_bug',
    owner_facing_quality: {},
    allowed_context: 'temp_skill_copy_only',
    retry_index: 1,
    retry_reason: 'first run flaked on network',
    room_id: 't_smoke_retry',
    started_at: '2026-05-13T10:10:00.000Z',
    finished_at: '2026-05-13T10:11:00.000Z',
  };
  const outPath = writeRunArtifact(artifact);
  assert.match(outPath, /_retry1\.json$/);
  fs.unlinkSync(outPath);
});

test('artifact-writer: bad inputs throw fast', () => {
  assert.throws(
    () => writeRunArtifact({
      case_number: 99,
      driver_type: 'main',
      owner_prompts: [],
      transcript: [],
      state_snapshot: {},
      close_output: {},
      score: 'MAGICAL',
      classification: 'pass',
      owner_facing_quality: {},
      allowed_context: 'temp_skill_copy_only',
      retry_index: 0,
      retry_reason: null,
      room_id: 't',
      started_at: '2026-05-13T00:00:00.000Z',
      finished_at: '2026-05-13T00:00:01.000Z',
    }),
    /case_number/
  );
});

test('artifact-writer: readMostRecentArtifact roundtrips', () => {
  const artifact = {
    case_number: 2,
    driver_type: 'mixed',
    owner_prompts: ['p'],
    transcript: [],
    state_snapshot: {},
    close_output: { ok: true },
    score: 'GOOD',
    score_detail: { reasons: ['1 clarification'] },
    classification: 'pass',
    owner_facing_quality: {},
    allowed_context: 'temp_skill_copy_only',
    retry_index: 0,
    retry_reason: null,
    room_id: 't_rt_check',
    started_at: '2026-05-13T11:00:00.000Z',
    finished_at: '2026-05-13T11:01:00.000Z',
  };
  const outPath = writeRunArtifact(artifact);
  const recovered = readMostRecentArtifact(2);
  assert.ok(recovered);
  assert.equal(recovered.room_id, 't_rt_check');
  fs.unlinkSync(outPath);
});

test('artifact-writer: ARTIFACT_DIR is gitignored under docs/progress', () => {
  assert.ok(ARTIFACT_DIR.endsWith('docs/progress'));
});
