import { test } from 'vitest';
import assert from 'node:assert/strict';
import { captureOpts } from './presets.js';

test('saved presets retain the full Create subtitle recipe', () => {
  const captured = captureOpts({
    subtitles: true,
    subMode: 'classic',
    subPreset: 'classic_white',
    subPosition: 'top',
    subAlign: 'left',
    subOffsetY: -18,
    subFont: 'Anton-Regular',
    subColor: '#FDE700',
    subStroke: '#123456',
    subFontSize: 46,
    subOutlineW: 5,
    subBg: true,
    url: 'https://example.invalid/source',
    file: { name: 'must-not-be-captured.mp4' },
  });

  assert.deepEqual(captured, {
    subtitles: true,
    subMode: 'classic',
    subPreset: 'classic_white',
    subPosition: 'top',
    subAlign: 'left',
    subOffsetY: -18,
    subFont: 'Anton-Regular',
    subColor: '#FDE700',
    subStroke: '#123456',
    subFontSize: 46,
    subOutlineW: 5,
    subBg: true,
  });
});
