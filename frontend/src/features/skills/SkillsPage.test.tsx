import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { SkillsPage } from './SkillsPage';

const skill = {
  name: 'example',
  display_name: '可复查方法',
  summary: '先固定证据，再完成核对。',
  description: '表格核对',
  enabled: true,
  available: true,
  revision: 1,
  policy_revision: 1,
  managed: true,
  protection_level: 'normal',
  archived_at: null as string | null,
  telemetry: {
    load_count: 5,
    success_run_count: 4,
    failed_run_count: 1,
    correction_count: 1,
    last_used_at: '2026-11-24T12:00:00Z',
  },
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('shows telemetry and honors lock and archive controls', async () => {
  let row = { ...skill };
  const writes: unknown[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === 'PATCH' || options?.method === 'POST') {
        const body = JSON.parse(String(options.body));
        writes.push(body);
        row = { ...row, revision: row.revision + 1 };
        if (url.endsWith('/protection')) row.protection_level = body.protection_level;
        if (url.endsWith('/archive')) row.archived_at = '2026-11-25T00:00:00Z';
        if (url.endsWith('/unarchive')) row.archived_at = null;
        return Response.json(row);
      }
      if (url.startsWith('/api/skills'))
        return Response.json(row.archived_at && !url.includes('true') ? [] : [row]);
      return Response.json([]);
    }),
  );
  render(
    <MemoryRouter>
      <SkillsPage />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/读取 5 次 · 成功 4 · 失败 1/)).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '固定技能' }));
  expect(await screen.findByText(/已固定 Pin/)).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '锁定技能' }));
  expect(await screen.findByText(/已锁定 Lock/)).toBeVisible();
  expect(screen.getByRole('button', { name: '更新' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '移除' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '归档' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: '解锁技能' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '归档' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: '归档' }));
  await waitFor(() =>
    expect(screen.queryByRole('article', { name: '可复查方法' })).not.toBeInTheDocument(),
  );
  await userEvent.click(screen.getByRole('checkbox', { name: '显示已归档' }));
  expect(await screen.findByText('已归档')).toBeVisible();
  expect(screen.getByRole('checkbox', { name: '启用技能：可复查方法' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: '恢复归档' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '归档' })).toBeEnabled());
  expect(writes).toEqual([
    { expected_revision: 1, protection_level: 'pinned' },
    { expected_revision: 2, protection_level: 'locked' },
    { expected_revision: 3, protection_level: 'normal' },
    { expected_revision: 4 },
    { expected_revision: 5 },
  ]);
});

it('reviews a colored diff, reports rollback conflict, and queues zero-model Curator', async () => {
  let reviewState = 'pending_review';
  const curatorWrites: unknown[] = [];
  const change = {
    id: 'change-a',
    action: 'patch',
    conversation_id: 'source',
    target_id: skill.name,
    created_at: '2026-11-25T05:00:00Z',
    diff_summary: { unified_diff: '--- SKILL.md\n+++ SKILL.md\n-原方法\n+新的方法' },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith('/approve')) {
        reviewState = 'approved';
        return Response.json({});
      }
      if (url.endsWith('/rollback'))
        return Response.json({ detail: '技能修订已变化，请刷新后再操作' }, { status: 409 });
      if (url === '/api/evolution/curator/preview')
        return Response.json({
          items: [
            {
              target_kind: 'skill',
              target_id: 'example',
              label: '可复查方法',
              action: 'archive',
              reason_code: 'skill_stale',
              telemetry: skill.telemetry,
            },
          ],
          due: true,
        });
      if (url === '/api/evolution/curator') {
        curatorWrites.push(JSON.parse(String(options?.body)));
        return Response.json({ id: 'curator-job', status: 'pending' }, { status: 202 });
      }
      if (url.includes('/api/evolution/jobs'))
        return Response.json([{ id: 'curator-job', status: 'completed' }]);
      if (url.includes('/api/evolution/changes'))
        return Response.json([{ ...change, review_state: reviewState }]);
      return Response.json(url.startsWith('/api/skills') ? [skill] : []);
    }),
  );
  render(
    <MemoryRouter>
      <SkillsPage />
    </MemoryRouter>,
  );
  expect(await screen.findByRole('button', { name: '技能进化与整理' })).toBeVisible();
  expect(screen.queryByRole('button', { name: '方法改进提案' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '技能进化与整理' }));
  const dialog = await screen.findByRole('dialog', { name: '技能进化与整理' });
  const review = within(dialog);
  expect(review.getByRole('button', { name: '方法改进提案' })).toBeVisible();
  expect(await review.findByText('+新的方法')).toHaveClass('bg-[#e3f1e8]');
  expect(review.getByText('-原方法')).toHaveClass('bg-[#fbeae8]');
  await userEvent.click(review.getByRole('button', { name: '批准' }));
  await userEvent.click(await review.findByRole('button', { name: '单项回滚' }));
  expect(await review.findByText(/技能修订已变化/)).toBeInTheDocument();
  await userEvent.click(review.getByRole('button', { name: 'Curator 周期整理' }));
  expect(await review.findByText(/长期未使用且有整理依据/)).toBeInTheDocument();
  expect(review.getByRole('checkbox', { name: /启用模型生成语义合并草稿/ })).not.toBeChecked();
  await userEvent.click(review.getByRole('button', { name: '运行整理扫描' }));
  expect(await review.findByText(/整理完成/)).toBeVisible();
  expect(curatorWrites).toEqual([{ request_key: expect.any(String), use_model_merge: false }]);
});
