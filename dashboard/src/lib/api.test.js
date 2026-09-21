import { test } from 'vitest';
import assert from 'node:assert/strict';
import { submitProcessJob, submitBatchJob } from './api.js';

const RECIPE = {
  mode: 'karaoke',
  preset: 'fire_impact',
  font_size: 54,
  font_color: '#FFFFFF',
  outline_color: '#000000',
  position: 'bottom',
  align: 'center',
  offset_y: -12,
};

test('URL Create submission sends selected subtitle recipe to backend', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.localStorage?.clear?.();
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    return { ok: true, json: async () => ({ job_id: 'job-1' }) };
  };
  try {
    await submitProcessJob({
      type: 'url',
      payload: 'https://example.com/video',
      preselections: { subtitles: RECIPE },
    }, 'gemini-key');

    assert.equal(calls.length, 1);
    const body = JSON.parse(calls[0].init.body);
    assert.deepEqual(body.create_subtitles, RECIPE);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Batch Create submission sends selected subtitle recipe to backend', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.localStorage?.clear?.();
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    return { ok: true, json: async () => ({ jobs: [], total: 0 }) };
  };
  try {
    await submitBatchJob({
      urls: ['https://example.com/video'],
      preselections: { subtitles: RECIPE },
    }, 'gemini-key');

    const body = JSON.parse(calls[0].init.body);
    assert.deepEqual(body.create_subtitles, RECIPE);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
