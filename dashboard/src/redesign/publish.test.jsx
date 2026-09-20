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
