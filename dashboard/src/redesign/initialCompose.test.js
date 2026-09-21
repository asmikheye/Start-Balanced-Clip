import { test, expect } from 'vitest';
import { optsToPreselections } from './realApi.js';

test('Create selections build the initial compose recipe', () => {
  const pre = optsToPreselections({
    smartcut: true,
    subtitles: true,
    subMode: 'karaoke',
    subPreset: 'hormozi_bold',
    subPosition: 'bottom',
    subAlign: 'left',
    subColor: '#ffee00',
    subStroke: '#111111',
    hooks: true,
    hookPos: 'top',
    hookSize: 'L',
    hookStyle: { font: 'Anton-Regular', text_color: '#ffffff' },
    logo: true,
    logoPos: 'bottom-center',
    logoSize: 'M',
    gradePreset: 'warm_cinematic',
  });

  expect(pre.compose.toggles).toEqual({
    smartcut: true,
    subtitles: true,
    hook: true,
    logo: true,
    grade: true,
    banner: false,
  });
  expect(pre.compose.subtitle_params).toMatchObject({
    mode: 'karaoke',
    preset: 'hormozi_bold',
    align: 'left',
    font_color: '#ffee00',
    outline_color: '#111111',
  });
  expect(pre.compose.hook_params).toMatchObject({ position: 'top', size: 'L' });
  expect(pre.compose.logo_params).toEqual({ position: 'bottom-center', size: 'M' });
  expect(pre.compose.grade_params).toEqual({ preset: 'warm_cinematic' });
});
