import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ComposerControls } from './ComposerControls';

const noop = () => {};
const props = {
  conversationId: 'conversation',
  composerReady: true,
  permission: 'ask',
  reasoning: 'default',
  settingBusy: false,
  changeSetting: noop,
  mode: 'ask' as const,
  setMode: noop,
  knowledgeOnly: false,
  setKnowledgeOnly: noop,
  delegation: false,
  setDelegation: noop,
  materials: [],
  selected: [],
  setSelected: noop,
  disabled: false,
  busy: false,
  context: null,
  onManageMaterials: noop,
  onOrganizeKnowledge: noop,
  researchMode: false,
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openSettings() {
  await userEvent.click(screen.getByRole('button', { name: '更多设置与材料' }));
  await userEvent.click(screen.getByRole('button', { name: '本次任务设置' }));
  return within(screen.getByRole('dialog', { name: '本次任务设置' }));
}

it('refreshes the conversation memory snapshot from task settings', async () => {
  let refreshed = false;
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      calls.push(url);
      if (options?.method === 'POST') {
        refreshed = true;
        return Response.json({ snapshot_hash: 'after', entry_count: 2 });
      }
      return Response.json({
        frozen: true,
        entry_count: refreshed ? 2 : 1,
        snapshot_hash: refreshed ? 'after' : 'before',
        frozen_at: '2026-11-25T05:00:00Z',
      });
    }),
  );
  render(
    <MemoryRouter>
      <ComposerControls {...props} />
    </MemoryRouter>,
  );
  expect(screen.queryByRole('button', { name: '刷新本会话记忆快照' })).not.toBeInTheDocument();
  const settings = await openSettings();
  expect(await settings.findByText('before')).toBeVisible();
  await userEvent.click(settings.getByRole('button', { name: '刷新本会话记忆快照' }));
  expect(await settings.findByText('快照已更新，共载入 2 条生效记忆')).toBeVisible();
  expect(await settings.findByText('after')).toBeVisible();
  expect(calls).toEqual([
    '/api/conversations/conversation/memory-snapshot',
    '/api/conversations/conversation/memory-snapshot/refresh',
    '/api/conversations/conversation/memory-snapshot',
  ]);
});

it.each([{ snapshotEligible: false }, { trial: true }])(
  'disables refresh when %j',
  async (overrides) => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    render(
      <MemoryRouter>
        <ComposerControls {...props} {...overrides} />
      </MemoryRouter>,
    );
    const settings = await openSettings();
    expect(settings.getByRole('button', { name: '刷新本会话记忆快照' })).toBeDisabled();
    expect(fetch).not.toHaveBeenCalled();
  },
);
