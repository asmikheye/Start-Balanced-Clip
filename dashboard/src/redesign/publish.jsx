// ClippyMe redesign — PublishModal: publish selected clips to Zernio.
import { useState, useEffect, useRef } from 'react';
import { Icon, Social, Btn, Switch, PlatPill, PLATFORMS } from './primitives';
import { LazyVideo } from './LazyVideo';
import { clipVideoSrc } from './realApi';
import { publishClip, getZernio } from './realApi';
import { seedToggles, seedHookParams, seedSubtitleParams, seedLogoParams, seedBannerParams } from '../lib/seedClipParams';
import { localDatePlus } from '../lib/scheduleDates';
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
            {error ? 'failed' : done ? (mode === 'auto' ? 'scheduled' : 'sent') : status === 'uploading' ? 'uploading' : 'waiting'}
          </span>
        </div>
        {error && errMsg && <div role="alert" style={{ color: 'var(--danger)', fontSize: 12, overflowWrap: 'anywhere', marginTop: 5 }}>{errMsg}</div>}
      </div>
      <div className="pcheck"><Icon n={done ? 'check' : error ? 'x' : 'loader'} /></div>
    </div>
  );
}

export function PublishModal({ clips, jobId, clipStates = {}, preselections, onClose, onPublished, pushToast }) {
  const all = clips.length > 1;
  const [zernio, setZernio] = useState(null);
  const [plats, setPlats] = useState({ tiktok: true, ig: true, yt: false });
  const [schedule, setSchedule] = useState(true);
  const [stage, setStage] = useState('setup'); // setup | uploading | done
  const [progress, setProgress] = useState({});
  const [runMode, setRunMode] = useState(null);
  const [outcome, setOutcome] = useState({ ok: 0, fail: 0 });

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

  // `batchPos` is the clip's position within this batch (0-based). When
  // scheduling, each clip gets its own day (start_date = today + batchPos) so
  // a per-platform daily cap doesn't reject the tail of the batch — replicates
  // the one-clip-per-day spacing from the original publisher.
  const buildBody = (clip, idx, batchPos = 0, mode = 'auto') => {
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
      schedule_mode: mode,
      ...(mode === 'auto' ? { start_date: localDatePlus(batchPos) } : {}),
      timezone: zernio?.timezone || 'Europe/Rome',
      tiktok_settings: plats.tiktok && accounts.tiktok ? {
        privacy_level: 'PUBLIC_TO_EVERYONE', allow_comment: true, allow_duet: true,
        allow_stitch: true, content_preview_confirmed: true, express_consent_given: true,
      } : undefined,
      ...(any ? { compose_first: true, toggles, hook_params: toggles.hook ? hookParams : {}, subtitle_params: toggles.subtitles ? subtitleParams : {}, logo_params: toggles.logo ? logoParams : {}, grade_params: toggles.grade ? gradeParams : {}, banner_params: toggles.banner ? bannerParams : {}, drop_ranges: toggles.smartcut ? (cs.dropRanges || []) : [] } : {}),
    };
  };

  const run = async (mode, retryFailed = false) => {
    if (!ready) return;
    const pending = clips.map((clip, batchPos) => ({ clip, batchPos }))
      .filter(({ clip }) => !retryFailed || progress[clip._idx]?.state === 'error');
    if (!pending.length) return;
    setRunMode(mode);
    setStage('uploading');
    if (!retryFailed) setProgress({});
    let ok = retryFailed ? outcome.ok : 0;
    let fail = 0;
    // Compose and upload one clip at a time. A batch can contain large videos;
    // concurrent ffmpeg renders and uploads compete for the same resources.
    for (const { clip, batchPos } of pending) {
      const idx = clip._idx;
      // Resolve to the backend's ABSOLUTE `shorts` position for the actual
      // publish call — `idx` (array position) stays the key into local
      // clipStates/progress, which are unaffected by a manual-publish gap.
      const apiIdx = clip._apiIdx ?? idx;
      setProgress((p) => ({ ...p, [idx]: { state: 'uploading' } }));
      try {
        const result = await publishClip(jobId, apiIdx, buildBody(clip, idx, batchPos, mode));
        if (result?.success === false) throw new Error('Zernio did not accept this post');
        setProgress((p) => ({ ...p, [idx]: { state: 'done' } }));
        ok += 1;
        try { onPublished?.(idx, mode); } catch { /* The post was accepted; local state is best-effort. */ }
      } catch (e) {
        // Surface the real reason (e.g. a Zernio daily-limit 429) instead of a
        // bare "failed", so the user knows to retry that platform tomorrow.
        setProgress((p) => ({ ...p, [idx]: { state: 'error', error: e?.message || 'Publish failed' } }));
        fail += 1;
      }
    }
    if (!mountedRef.current) return;
    setOutcome({ ok, fail });
    setStage('done');
    pushToast?.(fail === 0 ? 'success' : 'warn', `${mode === 'auto' ? 'Scheduled' : 'Sent'} ${ok}/${clips.length}${fail ? `, ${fail} failed` : ''}`);
  };

  const title = stage === 'done' ? (outcome.fail ? 'Publishing incomplete' : runMode === 'auto' ? 'Scheduled' : 'Sent to Zernio')
    : all ? `Publish ${clips.length} clips` : `Publish · ${clips[0]?.video_title_for_youtube_short || ''}`;

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
                  <div className="opt" style={{ borderBottom: 0 }}>
                    <div className="oico"><Icon n="calendar-clock" /></div>
                    <div className="otxt"><div className="ot">Schedule for prime time</div><div className="od">SmartScheduler picks the slot · off = publish now</div></div>
                    <div className="r"><Switch on={schedule} onChange={setSchedule} label="Schedule for prime time" /></div>
                  </div>
                </>
              )}
            </div>
            <div className="modal-foot">
              <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
              <div className="mf-right">
                <Btn variant="grad" icon={schedule ? 'calendar-clock' : 'send'} disabled={!ready} onClick={() => run(schedule ? 'auto' : 'now')}>{schedule ? 'Schedule' : 'Publish now'}</Btn>
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
              <Icon n={outcome.fail ? 'x' : runMode === 'auto' ? 'calendar-check' : 'check'} style={{ width: 28, height: 28, color: outcome.fail ? 'var(--danger)' : 'var(--brand-teal)' }} />
            </div>
            <div style={{ fontWeight: 700, fontSize: 18 }}>{outcome.ok}/{clips.length} clips {runMode === 'auto' ? 'scheduled' : 'sent'}</div>
            <p style={{ color: 'var(--fg-3)', fontSize: 13.5, marginTop: 8, lineHeight: 1.5 }}>
              {outcome.fail ? `${outcome.fail} failed. Check the errors below and retry only those clips.` : runMode === 'auto' ? 'Scheduled in Zernio for prime time.' : 'Sent to Zernio for immediate publishing.'}
            </p>
            {outcome.fail > 0 && <div className="pubgrid" style={{ marginTop: 18, textAlign: 'left' }}>{clips.filter((c) => progress[c._idx]?.state === 'error').map((c) => <PubRow key={c._idx} clip={c} idx={c._idx} st={progress[c._idx]} plats={plats} mode={runMode} />)}</div>}
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
