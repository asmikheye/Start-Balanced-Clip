import { test } from 'vitest';
import assert from 'node:assert/strict';

import { userLogTone, compactUserLogLine } from './processing.jsx';

test('transient Gemini failures are warnings, not fatal red errors', () => {
  const line = "⚠️ Gemini gemini-3.5-flash transient error (attempt 1/3): 503 UNAVAILABLE. {'error': {'message': 'high demand'}}. Retrying in 2s...";
  assert.equal(userLogTone(line), 'warning');
  assert.equal(compactUserLogLine(line), '⚠️ Gemini gemini-3.5-flash temporarily unavailable · 503 · attempt 1/3 · retry 2s');
});

test('real pipeline failures stay fatal', () => {
  assert.equal(userLogTone('❌ Pipeline failed: ffmpeg exploded'), 'error');
  assert.equal(userLogTone('Initial subtitle compose failed: bad filter'), 'error');
});

test('ordinary validation notes are not painted red just because they contain error', () => {
  assert.equal(userLogTone('validate_and_dedupe: dropping clip #1 — 1 validation error for ViralClip'), 'normal');
});
