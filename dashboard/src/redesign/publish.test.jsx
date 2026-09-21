import { test, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PublishModal } from './publish';

const publishClip = vi.hoisted(() => vi.fn(async () => ({ success: true })));
const planQueue = vi.hoisted(() => vi.fn(async (_accounts, incoming) => ({
  posts: [], duplicates: [], assignments: incoming.map((item, index) => ({
    ...item, new: true, scheduled_for: `2026-09-${22 + index}T08:30:00+03:00`,
  })),
})));
const applyQueueMoves = vi.hoisted(() => vi.fn(async () => ({ moved: 0 })));

vi.mock('./realApi', () => ({
  clipVideoSrc: () => '',
  publishClip,
  getZernio: async () => ({ configured: true, accounts: { tiktok: 'tk', instagram: 'ig' } }),
  planQueue, applyQueueMoves,
}));
vi.mock('./LazyVideo', () => ({ LazyVideo: () => null }));

test('Prime Time hides Caption and schedules each clip in its assigned fixed slot', async () => {
  publishClip.mockClear();
  planQueue.mockClear();
  render(<PublishModal
    clips={[
      { _idx: 0, video_title_for_youtube_short: 'Первый ролик' },
      { _idx: 1, video_title_for_youtube_short: 'Второй ролик' },
    ]}
    jobId="job-1" onClose={vi.fn()}
  />);

  expect(screen.queryByText('Caption')).toBeNull();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Publish in Prime Time' }).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Publish in Prime Time' }));
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(2));
  expect(publishClip.mock.calls.map((call) => call[2].caption)).toEqual([
    'Первый ролик', 'Второй ролик',
  ]);
  expect(publishClip.mock.calls.map((call) => call[2].schedule_mode)).toEqual(['manual', 'manual']);
  expect(publishClip.mock.calls.map((call) => call[2].queue_show_id)).toEqual(['job-1', 'job-1']);
});

test('setup exposes Now + Prime Time and derives the show from the source video', async () => {
  publishClip.mockClear();
  planQueue.mockClear();
  render(<PublishModal clips={[{ _idx: 0, video_title_for_youtube_short: 'Clip' }]}
    jobId="job-1" sourceInfo={{ webpage_url: 'https://www.youtube.com/watch?v=qP0fizk1zrE' }}
    onClose={vi.fn()} />);

  await waitFor(() => expect(screen.getByRole('button', { name: 'Publish in Prime Time' }).disabled).toBe(false));
  expect(screen.queryByRole('switch')).toBeNull();
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(screen.getByRole('button', { name: 'Publish Now' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Save show mapping' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Publish in Prime Time' }));
  await waitFor(() => expect(publishClip).toHaveBeenCalled());
  expect(publishClip.mock.calls[0][2].queue_show_id).toBe('source-qp0fizk1zre');
  expect(publishClip.mock.calls[0][2].queue_item_id).toBe('source-qp0fizk1zre:0');
  expect(planQueue.mock.calls[0][1][0].id).toBe('source-qp0fizk1zre:0');
});

test('Prime Time runs uploads sequentially, reports failures, and retries only failed clips', async () => {
  let releaseFirst;
  publishClip.mockReset()
    .mockImplementationOnce(() => new Promise((resolve) => { releaseFirst = resolve; }))
    .mockRejectedValueOnce(new Error('Zernio unavailable'))
    .mockResolvedValueOnce({ success: true });
  const onPublished = vi.fn();
  render(<PublishModal clips={[
    { _idx: 0, _apiIdx: 4, video_title_for_youtube_short: 'First' },
    { _idx: 1, _apiIdx: 6, video_title_for_youtube_short: 'Second' },
  ]} jobId="job-1" onClose={vi.fn()} onPublished={onPublished} />);

  await waitFor(() => expect(screen.getByRole('button', { name: 'Publish in Prime Time' }).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Publish in Prime Time' }));
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(1));
  expect(publishClip.mock.calls[0][1]).toBe(4);
  releaseFirst({ success: true });
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(2));
  expect(publishClip.mock.calls[1][1]).toBe(6);
  expect(publishClip.mock.calls[0][2].schedule_mode).toBe('manual');
  expect(publishClip.mock.calls[0][2].scheduled_for).not.toBe(publishClip.mock.calls[1][2].scheduled_for);
  expect(await screen.findByText('1/2 clips scheduled')).toBeTruthy();
  expect(screen.getByText('Zernio unavailable')).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'Retry failed' }));
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(3));
  expect(publishClip.mock.calls[2][1]).toBe(6);
  expect(await screen.findByText('2/2 clips scheduled')).toBeTruthy();
  expect(onPublished).toHaveBeenCalledTimes(2);
});

test('Prime Time stops the batch when Zernio rate-limits the account', async () => {
  publishClip.mockReset().mockRejectedValue(new Error(
    'Zernio POST /posts → HTTP 429: This account is temporarily rate-limited. Please wait 26m before posting again.',
  ));
  render(<PublishModal clips={[
    { _idx: 0, video_title_for_youtube_short: 'First' },
    { _idx: 1, video_title_for_youtube_short: 'Second' },
    { _idx: 2, video_title_for_youtube_short: 'Third' },
  ]} jobId="job-1" onClose={vi.fn()} />);

  fireEvent.click(await screen.findByRole('button', { name: 'Publish in Prime Time' }));
  await screen.findByText('0/3 clips scheduled');
  expect(publishClip).toHaveBeenCalledTimes(1);
  expect(screen.getAllByText('Zernio rate limit. Try again in 26 minutes.')).toHaveLength(3);
});


test('Publish Now sends schedule_mode=now and current edit state', async () => {
  publishClip.mockReset().mockResolvedValue({ success: true });
  planQueue.mockClear();
  render(<PublishModal
    clips={[{ _idx: 0, video_title_for_youtube_short: 'Immediate clip' }]}
    jobId="job-now" onClose={vi.fn()}
  />);

  fireEvent.click(await screen.findByRole('button', { name: 'Publish Now' }));
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(1));
  expect(planQueue).not.toHaveBeenCalled();
  expect(publishClip.mock.calls[0][2].schedule_mode).toBe('now');
  expect(publishClip.mock.calls[0][2].compose_first).toBe(true);
  expect(publishClip.mock.calls[0][2].toggles).toEqual({
    smartcut: false, hook: false, subtitles: false,
    logo: false, grade: false, banner: false,
  });
});
