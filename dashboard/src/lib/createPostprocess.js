// Create-time post-processing helpers.
//
// The main pipeline intentionally produces a clean/reframed base clip. Layers
// such as subtitles are composed by the existing /api/compose endpoint. Create
// previously stopped after the base render, so an enabled subtitle recipe was
// only remembered for Edit/Export and the first result preview was still raw.
//
// Keep this helper deliberately narrow: for now Create auto-applies SUBTITLES
// only. Hook/smartcut/logo/grade remain untouched until their own Create-time
// behaviour is explicitly implemented.
import { seedSubtitleParams } from './seedClipParams.js';

const SUBTITLE_ONLY_TOGGLES = Object.freeze({
  smartcut: false,
  hook: false,
  subtitles: true,
  logo: false,
  grade: false,
  banner: false,
});

export async function applyInitialSubtitles({
  jobId,
  clips,
  preselections,
  composeClip,
  updateClipState,
  now = Date.now,
}) {
  if (!jobId || !Array.isArray(clips) || clips.length === 0 || !preselections?.subtitles) {
    return { applied: 0, failed: 0 };
  }

  const subtitleParams = seedSubtitleParams(preselections);
  let applied = 0;
  let failed = 0;

  // Compose sequentially. ffmpeg subtitle burns are CPU-heavy and firing every
  // clip at once on a small self-hosted machine can make an otherwise-finished
  // Create job look hung.
  for (let idx = 0; idx < clips.length; idx += 1) {
    const clip = clips[idx] || {};
    const apiIdx = clip.original_index ?? idx;

    updateClipState?.(idx, {
      subtitleParams,
      processing: true,
    });

    try {
      const result = await composeClip(jobId, apiIdx, {
        toggles: { ...SUBTITLE_ONLY_TOGGLES },
        hook_params: {},
        subtitle_params: subtitleParams,
        logo_params: {},
        grade_params: {},
        banner_params: {},
        drop_ranges: [],
      });

      updateClipState?.(idx, {
        subtitleParams,
        previewUrl: result?.composed_url,
        previewBust: now(),
        processing: false,
      });
      applied += 1;
    } catch {
      // A subtitle failure must not hide or destroy the perfectly usable raw
      // clip. Leave it visible and continue with the remaining clips.
      updateClipState?.(idx, {
        subtitleParams,
        processing: false,
      });
      failed += 1;
    }
  }

  return { applied, failed };
}
