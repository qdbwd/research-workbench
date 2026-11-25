import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { ResearchTask } from './api';
import { ResearchAutonomy } from './ResearchAutonomy';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const sampleTask: ResearchTask = {
  id: 'task-1',
  title: '排查检索重排问题',
  status: 'open',
  home_conversation_id: 'conv-1',
  control_version: 2,
  main_branch_id: 'branch-1',
  write_branch_id: 'branch-1',
  write_branch_name: 'main',
  write_version: 1,
  created_at: '2026-11-24T09:00:00Z',
  head: {
    id: 'rev-1',
    branch_id: 'branch-1',
    parent_revision_id: null,
    number: 1,
    reason: 'start',
    created_at: '2026-11-24T09:00:00Z',
    state: {
      phase: 'investigation',
      active_capabilities: ['technical_research'],
      capability_override: null,
      brief: {
        goal: '定位旧版本规程被召回的原因',
        scope: '试点失败记录与规程',
        constraints: '',
        acceptance_criteria: '',
        key_decisions: '',
        blockers: '',
        next_action: '',
      },
      material_refs: [],
    },
  },
};

it('launches autonomous research with intuitive preset and web toggle without manual number entry', async () => {
  const submissions: Record<string, unknown>[] = [];
  vi.stubGlobal('fetch', async (url: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      submissions.push(JSON.parse(String(options.body)));
      return Response.json({ task: sampleTask, permit: null });
    }
    return Response.json(null);
  });

  const user = userEvent.setup();
  const onChanged = vi.fn();
  render(
    <MemoryRouter>
      <ResearchAutonomy task={sampleTask} onChanged={onChanged} />
    </MemoryRouter>,
  );

  await user.click(screen.getByRole('button', { name: '自主推进' }));
  await screen.findByText('定位旧版本规程被召回的原因');

  // 切换到“深度推进”并开启联网搜索，无需手填 4 个数字
  await user.click(screen.getByRole('button', { name: /深度推进/ }));
  await user.click(screen.getByRole('checkbox', { name: /允许联网搜索/ }));
  await user.click(screen.getByRole('button', { name: '确认范围和额度，开始执行' }));

  await waitFor(() => expect(submissions).toHaveLength(1));
  expect(submissions[0]).toMatchObject({
    action: 'grant',
    budgets: {
      model_requests: 100,
      tool_calls: 300,
      searches: 12,
      active_seconds: 2400,
    },
  });
});
