import { beforeEach, test, expect } from 'vitest';
import {
  allPresets, captureOpts, getDefaultPresetId, loadUserPresets,
  saveUserPreset, setDefaultPreset,
} from './presets.js';

beforeEach(() => {
  localStorage.clear();
});

test('v2 starts empty and ignores legacy preset storage', () => {
  localStorage.setItem('clippyme_user_presets_v1', JSON.stringify([
    { id: 'standard', title: 'Standart', opts: { smartcut: false } },
  ]));
  localStorage.setItem('clippyme_default_preset_v1', 'viral');
  expect(allPresets()).toEqual([]);
  expect(loadUserPresets()).toEqual([]);
  expect(getDefaultPresetId()).toBeNull();
});

test('captureOpts keeps the complete recipe and excludes only source/session fields', () => {
  const hookStyle = { font: 'Anton', color: '#ff00aa', shadow: true };
  const platforms = { tiktok: false, ig: false, yt: true };
  const opts = {
    mode: 'batch', source: 'upload', url: 'https://example.test/video',
    file: { fake: true }, fileName: 'video.mp4', batch: 'a\nb',
    batchFiles: [{ fake: true }], preset: 'old',
    instructions: 'Prefer technical explanations',
    clipsAuto: false, clips: 4, aspect: '9:16',
    detect: true, reframeMode: 'director', letterboxZoom: 10,
    smartcut: true, zoom: false, model: 'gemini-3.5-flash',
    subtitles: true, subMode: 'karaoke', subPreset: 'hormozi_bold',
    subPosition: 'top', subAlign: 'left', subFont: 'Montserrat-Black',
    subColor: '#ffffff', subStroke: '#111111', subFontSize: 44,
    subOutlineW: 5, subBg: true, subOffsetY: -12,
    hooks: true, hookPos: 'top', hookSize: 'L', hookStyle,
    logo: true, logoPos: 'bottom-center', logoSize: 'L',
    gradePreset: 'warm_cinematic', language: 'ru', platforms,
    futureRecipeField: { enabled: true, strength: 0.75 },
  };

  const captured = captureOpts(opts);
  expect(captured).toMatchObject({
    instructions: 'Prefer technical explanations',
    clipsAuto: false, clips: 4, reframeMode: 'director',
    subAlign: 'left', subStroke: '#111111', subFontSize: 44,
    subOutlineW: 5, subBg: true, subOffsetY: -12,
    logo: true, logoPos: 'bottom-center', logoSize: 'L',
    gradePreset: 'warm_cinematic', platforms,
    futureRecipeField: { enabled: true, strength: 0.75 },
  });
  for (const key of ['mode', 'source', 'url', 'file', 'fileName', 'batch', 'batchFiles', 'preset']) {
    expect(captured).not.toHaveProperty(key);
  }

  hookStyle.color = '#000000';
  platforms.yt = false;
  expect(captured.hookStyle.color).toBe('#ff00aa');
  expect(captured.platforms.yt).toBe(true);
});

test('Save current persists the full recipe and default selection uses v2 storage', () => {
  const preset = saveUserPreset('My recipe', {
    subtitles: true, subAlign: 'left', subOffsetY: 18,
    logo: true, logoPos: 'top-left', logoSize: 'M',
    gradePreset: 'vivid_pop', smartcut: false,
  });
  expect(allPresets()).toHaveLength(1);
  expect(allPresets()[0].opts).toEqual({
    subtitles: true, subAlign: 'left', subOffsetY: 18,
    logo: true, logoPos: 'top-left', logoSize: 'M',
    gradePreset: 'vivid_pop', smartcut: false,
  });

  setDefaultPreset(preset.id);
  expect(getDefaultPresetId()).toBe(preset.id);
  expect(localStorage.getItem('clippyme_default_preset_v1')).toBeNull();
});
