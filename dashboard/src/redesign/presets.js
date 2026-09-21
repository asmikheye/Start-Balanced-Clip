// User-created Create-flow presets.
//
// Built-in presets were intentionally retired. v2 uses fresh localStorage keys
// so installations that previously had Viral/Talking/Podcast/Standard entries
// start with an empty preset shelf without deleting the old v1 data.
const PRESETS_KEY = 'clippyme_user_presets_v2';
const DEFAULT_KEY = 'clippyme_default_preset_v2';

// Presets describe how a clip is made, not what source is currently loaded.
// Everything else is captured automatically so new recipe controls cannot be
// silently omitted from "Save current".
const SOURCE_ONLY_KEYS = new Set([
  'mode', 'source', 'url', 'file', 'fileName', 'batch', 'batchFiles', 'preset',
]);

function clonePresetValue(value) {
  if (value === undefined) return undefined;
  if (value === null || typeof value !== 'object') return value;
  return JSON.parse(JSON.stringify(value));
}

export function captureOpts(opts = {}) {
  const captured = {};
  for (const [key, value] of Object.entries(opts)) {
    if (SOURCE_ONLY_KEYS.has(key) || value === undefined) continue;
    try {
      captured[key] = clonePresetValue(value);
    } catch {
      // A future non-serializable UI-only value must not prevent saving the
      // rest of the recipe.
    }
  }
  return captured;
}

export function loadUserPresets() {
  try { return JSON.parse(localStorage.getItem(PRESETS_KEY)) || []; } catch { return []; }
}

export function allPresets() {
  return loadUserPresets();
}

export function saveUserPreset(name, opts) {
  const list = loadUserPresets();
  const preset = {
    id: 'u_' + Date.now().toString(36),
    title: (name || 'My preset').slice(0, 40),
    desc: 'Saved preset',
    icon: 'sliders-horizontal',
    user: true,
    opts: captureOpts(opts),
  };
  list.push(preset);
  try { localStorage.setItem(PRESETS_KEY, JSON.stringify(list)); } catch { /* quota */ }
  return preset;
}

export function deleteUserPreset(id) {
  const list = loadUserPresets().filter((p) => p.id !== id);
  try { localStorage.setItem(PRESETS_KEY, JSON.stringify(list)); } catch { /* */ }
  if (getDefaultPresetId() === id) setDefaultPreset(null);
}

export function getDefaultPresetId() {
  try { return localStorage.getItem(DEFAULT_KEY) || null; } catch { return null; }
}

export function setDefaultPreset(id) {
  try {
    if (id) localStorage.setItem(DEFAULT_KEY, id);
    else localStorage.removeItem(DEFAULT_KEY);
  } catch { /* */ }
}

// Options of the user's default preset (or null) — used to seed Create on load.
export function getDefaultPresetOpts() {
  const id = getDefaultPresetId();
  if (!id) return null;
  const p = allPresets().find((x) => x.id === id);
  return p ? p.opts : null;
}
