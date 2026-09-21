import { test } from 'vitest';
import assert from 'node:assert/strict';
import { applyInitialSubtitles } from './createPostprocess.js';

test('Create postprocess is a no-op when subtitles are disabled', async () => {
  let calls = 0;
  const result = await applyInitialSubtitles({
    jobId: 'job-1',
    clips: [{ original_index: 0 }],
    preselections: { subtitles: false },
    composeClip: async () => { calls += 1; },
    updateClipState: () => {},
  });
  assert.deepEqual(result, { applied: 0, failed: 0 });
  assert.equal(calls, 0);
});

test('Create postprocess burns subtitles only and keeps hook untouched', async () => {
  const composeCalls = [];
  const states = [];
  let stamp = 100;
  const clips = [
    { original_index: 3 },
    { original_index: 7 },
  ];
  const preselections = {
    subtitles: {
      mode: 'karaoke',
      preset: 'hormozi_bold',
      position: 'bottom',
      align: 'left',
      font_color: '#FDE700',
      outline_color: '#111111',
      font_size: 44,
    },
    hook: { position: 'top', size: 'M' },
    smartcut: true,
  };

  const result = await applyInitialSubtitles({
    jobId: 'job-1',
    clips,
    preselections,
    composeClip: async (jobId, apiIdx, body) => {
      composeCalls.push({ jobId, apiIdx, body });
      return { composed_url: `/videos/${jobId}/composed_${apiIdx}.mp4` };
    },
    updateClipState: (idx, patch) => states.push({ idx, patch }),
    now: () => ++stamp,
  });

  assert.deepEqual(result, { applied: 2, failed: 0 });
  assert.deepEqual(composeCalls.map((c) => c.apiIdx), [3, 7]);
  for (const call of composeCalls) {
    assert.equal(call.body.toggles.subtitles, true);
    assert.equal(call.body.toggles.hook, false);
    assert.equal(call.body.toggles.smartcut, false);
    assert.equal(call.body.toggles.logo, false);
    assert.equal(call.body.toggles.grade, false);
    assert.deepEqual(call.body.hook_params, {});
    assert.equal(call.body.subtitle_params.mode, 'karaoke');
    assert.equal(call.body.subtitle_params.font_size, 44);
  }
  const finished = states.filter((entry) => entry.patch.processing === false);
  assert.equal(finished.length, 2);
  assert.match(finished[0].patch.previewUrl, /composed_3\.mp4$/);
  assert.match(finished[1].patch.previewUrl, /composed_7\.mp4$/);
});

test('one subtitle compose failure does not block later clips', async () => {
  const completed = [];
  let call = 0;
  const result = await applyInitialSubtitles({
    jobId: 'job-1',
    clips: [{ original_index: 0 }, { original_index: 1 }],
    preselections: { subtitles: { mode: 'classic' } },
    composeClip: async (_jobId, apiIdx) => {
      call += 1;
      if (call === 1) throw new Error('ffmpeg failed');
      return { composed_url: `/videos/job-1/composed_${apiIdx}.mp4` };
    },
    updateClipState: (idx, patch) => {
      if (patch.processing === false) completed.push({ idx, patch });
    },
  });

  assert.deepEqual(result, { applied: 1, failed: 1 });
  assert.equal(completed.length, 2);
  assert.equal(completed[0].patch.previewUrl, undefined);
  assert.match(completed[1].patch.previewUrl, /composed_1\.mp4$/);
});
