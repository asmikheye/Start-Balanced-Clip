import { test, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PublishModal } from './publish';

const publishClip = vi.hoisted(() => vi.fn(async () => ({ success: true })));

vi.mock('./realApi', () => ({
  clipVideoSrc: () => '',
  publishClip,
  getZernio: async () => ({ configured: true, accounts: { tiktok: 'tk', instagram: 'ig' } }),
}));
vi.mock('./LazyVideo', () => ({ LazyVideo: () => null }));

test('Publish All hides Caption and sends each clip with its own title', async () => {
  publishClip.mockClear();
  render(<PublishModal
    clips={[
      { _idx: 0, video_title_for_youtube_short: 'Первый ролик' },
      { _idx: 1, video_title_for_youtube_short: 'Второй ролик' },
    ]}
    jobId="job-1" onClose={vi.fn()}
  />);

  expect(screen.queryByText('Caption')).toBeNull();
  fireEvent.click(await screen.findByRole('button', { name: 'Schedule' }));
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(2));
  expect(publishClip.mock.calls.map((call) => call[2].caption)).toEqual([
    'Первый ролик', 'Второй ролик',
  ]);
});

test('Publish now sends now even after switching off prime-time scheduling', async () => {
  publishClip.mockReset().mockResolvedValue({ success: true });
  const onPublished = vi.fn();
  render(<PublishModal clips={[{ _idx: 0, video_title_for_youtube_short: 'Clip' }]}
    jobId="job-1" onClose={vi.fn()} onPublished={onPublished} />);

  fireEvent.click(await screen.findByRole('switch', { name: 'Schedule for prime time' }));
  expect(screen.queryByRole('button', { name: 'Queue' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Publish now' }));
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(1));
  expect(publishClip.mock.calls[0][2]).toMatchObject({ schedule_mode: 'now' });
  expect(publishClip.mock.calls[0][2]).not.toHaveProperty('start_date');
  await waitFor(() => expect(onPublished).toHaveBeenCalledWith(0, 'now'));
});

test('Publish All runs sequentially, reports failures, and retries only failed clips', async () => {
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

  fireEvent.click(await screen.findByRole('button', { name: 'Schedule' }));
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(1));
  expect(publishClip.mock.calls[0][1]).toBe(4);
  releaseFirst({ success: true });
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(2));
  expect(publishClip.mock.calls[1][1]).toBe(6);
  expect(publishClip.mock.calls[0][2].schedule_mode).toBe('auto');
  expect(publishClip.mock.calls[0][2].start_date).not.toBe(publishClip.mock.calls[1][2].start_date);
  expect(await screen.findByText('1/2 clips scheduled')).toBeTruthy();
  expect(screen.getByText('Zernio unavailable')).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'Retry failed' }));
  await waitFor(() => expect(publishClip).toHaveBeenCalledTimes(3));
  expect(publishClip.mock.calls[2][1]).toBe(6);
  expect(await screen.findByText('2/2 clips scheduled')).toBeTruthy();
  expect(onPublished).toHaveBeenCalledTimes(2);
});
