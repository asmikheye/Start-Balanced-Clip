// ClippyMe redesign — PublishModal: publish selected clips to Zernio.
import { useState, useEffect, useRef } from 'react';
import { Icon, Social, Btn, PlatPill, PLATFORMS } from './primitives';
import { LazyVideo } from './LazyVideo';
import { clipVideoSrc } from './realApi';
import { publishClip, getZernio, planQueue, applyQueueMoves } from './realApi';
import { seedToggles, seedHookParams, seedSubtitleParams, seedLogoParams, seedBannerParams } from '../lib/seedClipParams';
import { useModalA11y } from './useModalA11y';

// redesign plat id → backend platform + account key. Exported so other
// surfaces publishing to Zernio (live.jsx) don't re-derive this mapping.
export const PLAT = {
  tiktok: { platform: 'tiktok', acct: 'tiktok', icon: 'tiktok', label: 'TikTok' },
  ig: { platform: 'instagram', acct: 'instagram', icon: 'instagram', label: 'Reels' },
  yt: { platform: 'youtube', acct: 'youtube', icon: 'youtube', label: 'Shorts' },
};

function PubRow({ clip, idx, st, plats, mode }) {
  // `st` is either a status string or { state, error } so we can surface the
  // real failure reason instead of a bare "failed".
  const status = typeof st === 'object' && st ? st.state : st;
  const errMsg = typeof st === 'object' && st ? st.error : null;
  const tasks = Object.keys(plats).filter((k) => plats[k]);
  const done = status === 'done';
  const error = status === 'error';
  const paused = status === 'paused';
  return (
    <div className={'pubrow' + (done ? ' done' : '')}>
      <div className="pthumb" style={{ background: '#000', overflow: 'hidden' }}>
        {/* LazyVideo, not a bare <video>: a 20-clip batch publish must not fire
            20 concurrent video fetches the moment the modal opens. */}
        <LazyVideo src={clipVideoSrc(clip)} muted playsInline rootMargin="120px"
          style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      </div>
      <div className="pinfo">
        <div className="pttl">{clip.video_title_for_youtube_short || `Clip ${idx + 1}`}</div>
        <div className="pplats">
          {tasks.map((p) => (
            <div className="pp" key={p}>
              <Social n={PLAT[p].icon} color={done ? '02C5BF' : '7E7E8F'} size={13} />
              <div className="ptrack"><i className={p} style={{ width: done ? '100%' : status === 'uploading' ? '70%' : '0%', transition: 'width .4s' }}></i></div>
            </div>
          ))}
          <span className={'pstat' + (done ? ' done' : status === 'uploading' ? '' : ' wait')}
            style={error ? { color: 'var(--danger)' } : undefined}
            title={error && errMsg ? errMsg : undefined}>
            {error ? 'failed' : paused ? 'paused' : done ? (mode === 'queue' ? 'scheduled' : 'sent') : status === 'uploading' ? 'uploading' : 'waiting'}
          </span>
        </div>
        {(error || paused) && errMsg && <div role="alert" style={{ color: 'var(--danger)', fontSize: 12, overflowWrap: 'anywhere', marginTop: 5 }}>{errMsg}</div>}
      </div>
      <div className="pcheck"><Icon n={done ? 'check' : error ? 'x' : 'loader'} /></div>
    </div>
  );
}

function automaticShowId(sourceInfo, jobId) {
  const sourceUrl = sourceInfo?.webpage_url || '';
  try {
    const parsed = new URL(sourceUrl);
    const videoId = parsed.searchParams.get('v') || parsed.pathname.split('/').filter(Boolean).pop();
    if (videoId) return `source-${videoId}`.slice(0, 64).toLowerCase();
  } catch { /* Local uploads do not have a URL. */ }
  const source = sourceInfo?.title || sourceInfo?.uploader_id || sourceInfo?.banner?.handle || jobId;
  return String(source || jobId).trim().toLowerCase().replace(/[^\p{L}\p{N}._-]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 64) || jobId;
}

function publishError(error) {
  const raw = error?.message || 'Publish failed';
  if (!/(?:http\s*)?429|rate[- ]?limit/i.test(raw)) return { message: raw, rateLimited: false };
  const wait = raw.match(/wait\s+(\d+)m/i)?.[1];
  return {
    message: wait
      ? `Zernio rate limit. Try again in ${wait} minutes.`
      : 'Zernio rate limit. Wait a little, then retry the remaining clips.',
    rateLimited: true,
  };
}

export function PublishModal({ clips, jobId, clipStates = {}, preselections, sourceInfo, onClose, onPublished, pushToast }) {
  const all = clips.length > 1;
  const [zernio, setZernio] = useState(null);
  const [plats, setPlats] = useState({ tiktok: true, ig: true, yt: false });
  const [stage, setStage] = useState('setup'); // setup | uploading | done
  const [progress, setProgress] = useState({});
  const [runMode, setRunMode] = useState(null);
  const [outcome, setOutcome] = useState({ ok: 0, fail: 0 });
  const showId = automaticShowId(sourceInfo, jobId);

  useEffect(() => { getZernio().then(setZernio).catch(() => setZernio({ configured: false })); }, []);

  // Keep the batch visible until its in-flight request finishes. Closing it
  // mid-request makes it easy to start the same publish again.
  const close = () => { if (stage !== 'uploading') onClose(); };
  const panelRef = useModalA11y(close);

  // Guard the post-publish setTimeout so it never calls setState after the
  // modal has been unmounted (e.g. parent closes it while the delay is in flight).
  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);

  const accounts = zernio?.accounts || {};
  const toggle = (k) => setPlats((p) => ({ ...p, [k]: !p[k] }));
  const platTargets = () => Object.keys(plats)
    .filter((k) => plats[k] && accounts[PLAT[k].acct])
    .map((k) => ({ platform: PLAT[k].platform, accountId: accounts[PLAT[k].acct] }));
  const targets = platTargets();
  const ready = zernio?.configured && targets.length > 0;
  const accountIds = targets.map((target) => target.accountId);

  const buildBody = (clip, idx, mode = 'now', assignment = null) => {
    const cs = clipStates[idx] || {};
    const toggles = cs.toggles ?? seedToggles(preselections);
    const any = Object.values(toggles).some(Boolean);
    const hookParams = cs.hookParams ?? seedHookParams(clip, preselections);
    const subtitleParams = cs.subtitleParams ?? seedSubtitleParams(preselections);
    const logoParams = cs.logoParams ?? seedLogoParams(preselections);
    const gradeParams = cs.gradeParams ?? { preset: preselections?.grade?.preset || 'none' };
    const bannerParams = cs.bannerParams ?? seedBannerParams(preselections);
    const title = (clip.video_title_for_youtube_short || `Clip ${idx + 1}`).slice(0, 100);
    return {
      title,
      caption: title,
      platforms: targets,
      schedule_mode: mode === 'queue' ? 'manual' : 'now',
      ...(mode === 'queue' ? {
        scheduled_for: assignment.scheduled_for,
        queue_show_id: showId,
        queue_item_id: assignment.id,
      } : {}),
      timezone: zernio?.timezone || 'Europe/Istanbul',
      tiktok_settings: plats.tiktok && accounts.tiktok ? {
        privacy_level: 'PUBLIC_TO_EVERYONE', allow_comment: true, allow_duet: true,
        allow_stitch: true, content_preview_confirmed: true, express_consent_given: true,
      } : undefined,
      ...(any ? { compose_first: true, toggles, hook_params: toggles.hook ? hookParams : {}, subtitle_params: toggles.subtitles ? subtitleParams : {}, logo_params: toggles.logo ? logoParams : {}, grade_params: toggles.grade ? gradeParams : {}, banner_params: toggles.banner ? bannerParams : {}, drop_ranges: toggles.smartcut ? (cs.dropRanges || []) : [] } : {}),
    };
  };

  const run = async (mode, retryFailed = false) => {
    if (!ready) return;
    const pending = clips.map((clip) => ({ clip }))
      .filter(({ clip }) => !retryFailed || ['error', 'paused'].includes(progress[clip._idx]?.state));
    if (!pending.length) return;
    setRunMode(mode);
    setStage('uploading');
    if (!retryFailed) setProgress({});
    let ok = retryFailed ? outcome.ok : 0;
    let fail = 0;
    let assignmentById = new Map();
    let duplicateIds = new Set();
    if (mode === 'queue') {
      try {
        const incoming = pending.map(({ clip }) => ({
          id: `${jobId}:${clip._apiIdx ?? clip._idx}`,
          show_id: showId,
        }));
        const plan = await planQueue(accountIds, incoming);
        duplicateIds = new Set(plan.duplicates || []);
        assignmentById = new Map(plan.assignments.filter((item) => item.new).map((item) => [item.id, item]));
        const existing = new Map(plan.posts.map((post) => [post.id, post]));
        const moves = plan.assignments.filter((item) => !item.new).map((item) => ({
          post_id: item.id,
          expected_scheduled_for: existing.get(item.id)?.scheduled_for,
          scheduled_for: item.scheduled_for,
        })).filter((move) => move.expected_scheduled_for
          && new Date(move.expected_scheduled_for).getTime() !== new Date(move.scheduled_for).getTime());
        await applyQueueMoves(accountIds, moves);
        for (const duplicate of duplicateIds) {
          const clip = pending.find(({ clip: value }) => `${jobId}:${value._apiIdx ?? value._idx}` === duplicate)?.clip;
          if (clip) {
            setProgress((p) => ({ ...p, [clip._idx]: { state: 'done' } }));
            ok += 1;
            try { onPublished?.(clip._idx, 'queue'); } catch { /* Existing remote post is authoritative. */ }
          }
        }
      } catch (error) {
        const message = error?.message || 'Could not build the queue';
        pending.forEach(({ clip }) => setProgress((p) => ({ ...p, [clip._idx]: { state: 'error', error: message } })));
        if (mountedRef.current) { setOutcome({ ok, fail: pending.length }); setStage('done'); }
        return;
      }
    }
    // Compose and upload one clip at a time. A batch can contain large videos;
    // concurrent ffmpeg renders and uploads compete for the same resources.
    for (let pendingIndex = 0; pendingIndex < pending.length; pendingIndex += 1) {
      const { clip } = pending[pendingIndex];
      const idx = clip._idx;
      // Resolve to the backend's ABSOLUTE `shorts` position for the actual
      // publish call — `idx` (array position) stays the key into local
      // clipStates/progress, which are unaffected by a manual-publish gap.
      const apiIdx = clip._apiIdx ?? idx;
      const itemId = `${jobId}:${apiIdx}`;
      const assignment = assignmentById.get(itemId);
      if (mode === 'queue' && !assignment) {
        if (duplicateIds.has(itemId)) continue;
        setProgress((p) => ({ ...p, [idx]: { state: 'error', error: 'Queue did not assign a slot' } }));
        fail += 1;
        continue;
      }
      setProgress((p) => ({ ...p, [idx]: { state: 'uploading' } }));
      try {
        const result = await publishClip(jobId, apiIdx, buildBody(clip, idx, mode, assignment));
        if (result?.success === false) throw new Error('Zernio did not accept this post');
        setProgress((p) => ({ ...p, [idx]: { state: 'done' } }));
        ok += 1;
        try { onPublished?.(idx, mode); } catch { /* The post was accepted; local state is best-effort. */ }
      } catch (e) {
        const failure = publishError(e);
        setProgress((p) => ({ ...p, [idx]: { state: 'error', error: failure.message } }));
        fail += 1;
        // A 429 applies to the account, so sending every following clip only
        // produces the same failure and can extend the provider cooldown.
        if (failure.rateLimited) {
          const remaining = pending.slice(pendingIndex + 1).filter(({ clip: value }) => {
            const id = `${jobId}:${value._apiIdx ?? value._idx}`;
            return assignmentById.has(id) && !duplicateIds.has(id);
          });
          setProgress((p) => {
            const next = { ...p };
            remaining.forEach(({ clip: value }) => { next[value._idx] = { state: 'paused', error: failure.message }; });
            return next;
          });
          fail += remaining.length;
          break;
        }
      }
    }
    if (!mountedRef.current) return;
    setOutcome({ ok, fail });
    setStage('done');
    pushToast?.(fail === 0 ? 'success' : 'warn', `${mode === 'queue' ? 'Queued' : 'Sent'} ${ok}/${clips.length}${fail ? `, ${fail} failed` : ''}`);
  };

  const title = stage === 'done' ? (outcome.fail ? 'Publishing incomplete' : runMode === 'queue' ? 'Queue updated' : 'Sent to Zernio')
    : all ? `Queue ${clips.length} clips` : `Queue · ${clips[0]?.video_title_for_youtube_short || ''}`;

  return (
    // Backdrop click is a mouse-only convenience; keyboard users close via
    // Esc (useModalA11y). currentTarget guard replaces stopPropagation.
    <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className={'modal' + (all ? ' wide' : '')} ref={panelRef}
        role="dialog" aria-modal="true" aria-labelledby="publish-modal-title">
        <div className="modal-head">
          <div>
            <h3 id="publish-modal-title">{title}</h3>
            {stage === 'uploading' && <div className="mh-sub">Preparing and uploading clips one at a time</div>}
          </div>
          <button className="x" onClick={close} disabled={stage === 'uploading'} aria-label="Close"><Icon n="x" /></button>
        </div>

        {stage === 'setup' && (
          <>
            <div className="modal-body">
              {!zernio ? <div className="cm-small">Loading Zernio…</div> : !zernio.configured ? (
                <div className="empty" style={{ padding: '24px 12px' }}>
                  <div className="ei"><Icon n="rss" /></div>
                  <h3>Zernio not connected</h3>
                  <p>Add your Zernio API key + account IDs in Settings to publish.</p>
                </div>
              ) : (
                <>
                  <div className="field">
                    <span className="field-label">Platforms</span>
                    <div className="plats">
                      {PLATFORMS.map((p) => {
                        const has = !!accounts[PLAT[p.id].acct];
                        return <PlatPill key={p.id} {...p} on={plats[p.id] && has}
                          onClick={() => has ? toggle(p.id) : pushToast?.('warn', `No ${PLAT[p.id].label} account saved`)} />;
                      })}
                    </div>
                  </div>
                </>
              )}
            </div>
            <div className="modal-foot">
              <div className="mf-right">
                <Btn variant="grad" icon="calendar-clock" disabled={!ready}
                  onClick={() => run('queue')}>Queue</Btn>
              </div>
            </div>
          </>
        )}

        {stage === 'uploading' && (
          <div className="modal-body">
            <div className="pubgrid">
              {clips.map((c) => <PubRow key={c._idx} clip={c} idx={c._idx} st={progress[c._idx]} plats={plats} mode={runMode} />)}
            </div>
          </div>
        )}

        {stage === 'done' && (
          <div className="modal-body" style={{ textAlign: 'center', padding: '36px 24px' }}>
            <div style={{ width: 60, height: 60, borderRadius: '50%', background: 'var(--success-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
              <Icon n={outcome.fail ? 'x' : runMode === 'queue' ? 'calendar-check' : 'check'} style={{ width: 28, height: 28, color: outcome.fail ? 'var(--danger)' : 'var(--brand-teal)' }} />
            </div>
            <div style={{ fontWeight: 700, fontSize: 18 }}>{outcome.ok}/{clips.length} clips {runMode === 'queue' ? 'scheduled' : 'sent'}</div>
            <p style={{ color: 'var(--fg-3)', fontSize: 13.5, marginTop: 8, lineHeight: 1.5 }}>
              {outcome.fail ? `${outcome.fail} clips were not published. Wait if Zernio imposed a cooldown, then retry them.` : runMode === 'queue' ? 'The fixed-slot schedule is saved in Zernio. This computer can now be turned off.' : 'Sent to Zernio for immediate publishing.'}
            </p>
            {outcome.fail > 0 && <div className="pubgrid" style={{ marginTop: 18, textAlign: 'left' }}>{clips.filter((c) => ['error', 'paused'].includes(progress[c._idx]?.state)).map((c) => <PubRow key={c._idx} clip={c} idx={c._idx} st={progress[c._idx]} plats={plats} mode={runMode} />)}</div>}
            <div style={{ marginTop: 22, display: 'flex', justifyContent: 'center', gap: 10 }}>
              {outcome.fail > 0 && <Btn variant="grad" onClick={() => run(runMode, true)}>Retry failed</Btn>}
              <Btn variant="secondary" onClick={onClose}>Done</Btn>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
