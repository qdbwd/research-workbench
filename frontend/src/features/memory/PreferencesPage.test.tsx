import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { PreferencesPage } from './PreferencesPage';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('confirms an inferred preference against its exact displayed revision', async () => {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/memory-categories') return Response.json([]);
      const row = {
        id: 'p1',
        revision: 2,
        content: '先给例子',
        scope: 'global',
        topic: '',
        status: 'pending',
        source: { kind: 'inferred' },
      };
      if (options?.method === 'PATCH') {
        calls.push({ url, body: JSON.parse(String(options.body)) });
        return Response.json({ ...row, revision: 3, status: 'active' });
      }
      return Response.json([row]);
    }),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole('button', { name: '确认生效' }));
  expect(calls).toEqual([
    { url: '/api/memories/p1', body: { expected_revision: 2, status: 'active' } },
  ]);
});

it('shows genuine evidence and restores the chosen immutable revision', async () => {
  const writes: { url: string; body: unknown }[] = [];
  const row = {
    id: 'm1',
    revision: 3,
    content: '现在偏好例子',
    category: 'cognitive',
    scope: 'global',
    topic: '',
    status: 'active',
    source: { kind: 'manual' },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/memory-categories') return Response.json([]);
      if (options?.method === 'POST') {
        writes.push({ url, body: JSON.parse(String(options.body)) });
        return Response.json(row);
      }
      if (url.endsWith('/history'))
        return Response.json([{ ...row, revision: 1, content: '以前先讲原理' }, row]);
      if (url === '/api/memories/m1')
        return Response.json({
          ...row,
          evidence: [
            {
              id: 'e1',
              quote: '我的习惯是先看例子',
              kind: 'explicit',
              context_key: 'conversation:c1',
            },
          ],
        });
      return Response.json([row]);
    }),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole('button', { name: '查看来源证据' }));
  expect(await screen.findByText(/^我的习惯是先看例子/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '来源对话' })).toHaveAttribute('href', '/chat/c1');
  await userEvent.click(screen.getByRole('button', { name: '查看修订记录' }));
  await userEvent.click(await screen.findByRole('button', { name: '恢复修订 1' }));
  expect(writes).toEqual([
    { url: '/api/memories/m1/rollback', body: { expected_revision: 3, target_revision: 1 } },
  ]);
});

it('preserves the current form and displays a conflicting revision error', async () => {
  const row = {
    id: 'm2',
    revision: 4,
    content: '我是后端开发者',
    category: 'background',
    scope: 'global',
    topic: '',
    status: 'active',
    source: { kind: 'manual' },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) =>
      url === '/api/memory-categories'
        ? Response.json([])
        : options?.method === 'PATCH'
          ? Response.json({ detail: '修订已变化，请刷新后重试' }, { status: 409 })
          : Response.json([row]),
    ),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  const input = await screen.findByRole('textbox', { name: '记忆内容 m2' });
  await userEvent.clear(input);
  await userEvent.type(input, '我现在做产品开发');
  await userEvent.click(screen.getByRole('button', { name: '保存修改' }));
  expect(await screen.findByText('修订已变化，请刷新后重试')).toBeInTheDocument();
  expect(input).toHaveValue('我现在做产品开发');
});

it('saves text with automatic classification by default and permits an explicit category', async () => {
  const calls: unknown[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/memory-categories')
        return Response.json([{ id: 'coding', name: '编程', revision: 1, memory_count: 0 }]);
      if (options?.method === 'POST') {
        calls.push(JSON.parse(String(options.body)));
        return Response.json({ id: 'saved' });
      }
      return Response.json([]);
    }),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  const content = screen.getByLabelText('新增长期记忆');
  const choice = await screen.findByLabelText('类别（可选）');
  expect(choice).toHaveValue('');
  await userEvent.type(content, '请用 Python 举例');
  await userEvent.click(screen.getByRole('button', { name: '新增记忆' }));
  await waitFor(() => expect(content).toHaveValue(''));
  await userEvent.type(content, '说明示例的输入输出');
  await userEvent.selectOptions(choice, 'coding');
  await userEvent.click(screen.getByRole('button', { name: '新增记忆' }));
  expect(calls).toEqual([
    {
      content: '请用 Python 举例',
      display_category_id: null,
      memory_slot: 'user',
      scope: 'global',
      topic: '',
    },
    {
      content: '说明示例的输入输出',
      display_category_id: 'coding',
      memory_slot: 'user',
      scope: 'global',
      topic: '',
    },
  ]);
});

it('changes only the display category and never sends confirmation or semantic fields', async () => {
  let row = {
    id: 'm3',
    revision: 3,
    content: '后台任务应保持进度',
    scope: 'global',
    topic: '',
    status: 'active',
    category: 'goal',
    display_category_id: null as string | null,
    classification: { status: 'unclassified', source: 'model', error: 'model_disabled' },
    source: { kind: 'manual' },
  };
  const writes: unknown[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/memory-categories')
        return Response.json([{ id: 'coding', name: '编程', revision: 1, memory_count: 0 }]);
      if (options?.method === 'PATCH') {
        const body = JSON.parse(String(options.body));
        writes.push(body);
        row = { ...row, display_category_id: body.display_category_id, revision: 4 };
        return Response.json(row);
      }
      return Response.json([row]);
    }),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  const field = await screen.findByLabelText('记忆内容 m3');
  const card = within(field.closest('section')!);
  expect(card.getByRole('button', { name: '保存修改' })).toBeDisabled();
  expect(card.getByText(/模型调用已关闭/)).toBeInTheDocument();
  await userEvent.selectOptions(card.getByLabelText('类别（可选）'), 'coding');
  expect(card.getByRole('button', { name: '停用' })).toBeDisabled();
  await userEvent.click(card.getByRole('button', { name: '保存修改' }));
  expect(writes).toEqual([{ expected_revision: 3, display_category_id: 'coding' }]);
  await waitFor(() => expect(card.getByRole('button', { name: '保存修改' })).toBeDisabled());
});

it('preserves a local draft when category management refreshes a newer memory revision', async () => {
  let row = {
    id: 'm4',
    revision: 1,
    content: '保留原文',
    scope: 'global',
    topic: '',
    status: 'active',
    display_category_id: null as string | null,
    source: { kind: 'manual' },
  };
  const writes: unknown[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/memory-categories') {
        if (options?.method === 'POST') {
          row = { ...row, revision: 2, display_category_id: 'coding' };
          return Response.json({ id: 'coding' });
        }
        return Response.json([{ id: 'coding', name: '编程', revision: 1, memory_count: 1 }]);
      }
      if (options?.method === 'PATCH') {
        writes.push(JSON.parse(String(options.body)));
        return Response.json({ detail: '记忆修订已变化' }, { status: 409 });
      }
      return Response.json(url === '/api/memories/m4' ? row : [row]);
    }),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  const field = await screen.findByLabelText('记忆内容 m4');
  await userEvent.clear(field);
  await userEvent.type(field, '尚未保存的原文修改');
  await userEvent.click(screen.getByRole('button', { name: '管理类别' }));
  const manager = within(await screen.findByRole('dialog', { name: '管理记忆类别' }));
  await userEvent.type(manager.getByLabelText('新类别名称'), '新类别');
  await userEvent.click(manager.getByRole('button', { name: '新增类别' }));
  await waitFor(() => expect(manager.getByLabelText('新类别名称')).toHaveValue(''));
  await userEvent.click(manager.getByRole('button', { name: '关闭管理记忆类别' }));
  expect(field).toHaveValue('尚未保存的原文修改');
  expect(await screen.findByText(/服务器已有新修订/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '保存修改' }));
  expect(writes).toEqual([{ expected_revision: 1, content: '尚未保存的原文修改' }]);
  expect(await screen.findByText('记忆修订已变化')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '放弃本地修改并重新加载' }));
  await waitFor(() => expect(field).toHaveValue('保留原文'));
});

const evolutionSettings = {
  revision: 2,
  auto_review_enabled: true,
  memory_auto_write: true,
  user_slot_char_limit: 2400,
  system_slot_char_limit: 3600,
  daily_used: 2,
  daily_budget_requests: 20,
  daily_remaining: 18,
  slots: {
    user: { character_count: 420, entry_count: 1, char_limit: 2400 },
    system: { character_count: 117, entry_count: 1, char_limit: 3600 },
  },
};

it('filters the two slots, shows capacity and applies protection against displayed revisions', async () => {
  const userRow = {
    id: 'user-row',
    content: '先给示例',
    revision: 1,
    scope: 'global',
    topic: '',
    status: 'active',
    source: { kind: 'manual' },
    memory_slot: 'user',
  };
  let systemRow = {
    ...userRow,
    id: 'system-row',
    content: '测试使用隔离库',
    memory_slot: 'system',
    protection_level: 'locked',
  };
  const writes: unknown[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/evolution/settings') return Response.json(evolutionSettings);
      if (url === '/api/memory-categories') return Response.json([]);
      if (options?.method === 'PATCH') {
        const body = JSON.parse(String(options.body));
        writes.push(body);
        systemRow = {
          ...systemRow,
          protection_level: body.protection_level,
          revision: systemRow.revision + 1,
        };
        return Response.json(systemRow);
      }
      return Response.json([userRow, systemRow]);
    }),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/420 \/ 2400 字/)).toBeInTheDocument();
  expect(screen.getByRole('progressbar', { name: '环境与系统知识容量' })).toHaveAttribute(
    'aria-valuenow',
    '117',
  );
  await userEvent.click(screen.getByRole('button', { name: '环境与系统知识' }));
  expect(screen.queryByLabelText('记忆内容 user-row')).not.toBeInTheDocument();
  const field = screen.getByLabelText('记忆内容 system-row');
  expect(field).toBeDisabled();
  expect(screen.getByRole('button', { name: '删除记忆' })).toBeDisabled();
  expect(screen.getByText(/请先解锁/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '解锁记忆' }));
  await waitFor(() => expect(field).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: '固定记忆' }));
  expect(await screen.findByText(/已固定 Pin/)).toBeInTheDocument();
  expect(writes).toEqual([
    { expected_revision: 1, protection_level: 'normal' },
    { expected_revision: 2, protection_level: 'pinned' },
  ]);
});

it('keeps settings input on a conflict and sends the optimistic revision', async () => {
  const writes: unknown[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === 'PATCH') {
        writes.push(JSON.parse(String(options.body)));
        return Response.json({ detail: '自进化设置已变化，请刷新后再保存' }, { status: 409 });
      }
      return Response.json(url === '/api/evolution/settings' ? evolutionSettings : []);
    }),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/剩余 18 次/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '自进化设置' }));
  const toggle = screen.getByRole('checkbox', { name: '自动后台复盘' });
  await userEvent.click(toggle);
  await userEvent.click(screen.getByRole('button', { name: '保存自进化设置' }));
  expect(await screen.findByText(/自进化设置已变化/)).toBeInTheDocument();
  expect(toggle).not.toBeChecked();
  expect(writes).toEqual([
    {
      expected_revision: 2,
      auto_review_enabled: false,
      memory_auto_write: true,
      skill_auto_write: true,
      user_slot_char_limit: 2400,
      system_slot_char_limit: 3600,
      review_turn_threshold: 6,
      skill_tool_threshold: 8,
      curator_stale_days: 30,
      daily_budget_requests: 20,
    },
  ]);
});

it('approves, rejects and rolls back individual Journey changes and renders a conflict', async () => {
  const base = {
    action: 'update',
    conversation_id: 'review-chat',
    review_state: 'pending_review',
    created_at: '2026-11-25T02:00:00Z',
    evidence_refs: { quote: '用户原话' },
    security_scan_result: { reasons: [] },
  };
  let changes = [
    {
      ...base,
      id: 'accept',
      diff_summary: {
        before: { content: '先讲定义' },
        after: { content: '先举例', category: 'preference' },
      },
    },
    { ...base, id: 'decline', diff_summary: { before: {}, after: { content: '不接受的草稿' } } },
    {
      ...base,
      id: 'conflict',
      review_state: 'applied',
      diff_summary: { before: {}, after: { content: '已经再次编辑' } },
    },
  ];
  const writes: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/evolution/settings') return Response.json(evolutionSettings);
      if (options?.method === 'POST') {
        writes.push(url);
        const [, id, action] = url.match(/changes\/([^/]+)\/([^/]+)$/)!;
        if (id === 'conflict')
          return Response.json({ detail: '目标记忆修订已变化，请刷新后再回滚' }, { status: 409 });
        const resolved: Record<string, string> = {
          approve: 'approved',
          reject: 'rejected',
          rollback: 'rolled_back',
        };
        const next = resolved[action];
        changes = changes.map((change) =>
          change.id === id ? { ...change, review_state: next } : change,
        );
        return Response.json({});
      }
      return Response.json(url.startsWith('/api/evolution/changes?') ? changes : []);
    }),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole('button', { name: '复盘记录' }));
  const approved = within((await screen.findByText('修改后 先举例')).closest('article')!);
  const rejected = within(screen.getByText('修改后 不接受的草稿').closest('article')!);
  const conflict = within(screen.getByText('修改后 已经再次编辑').closest('article')!);
  expect(approved.getByRole('link', { name: '来源会话' })).toHaveAttribute(
    'href',
    '/chat/review-chat',
  );
  expect(approved.getByText('修改前 先讲定义')).toBeInTheDocument();
  expect(approved.getByText('修改后 表达偏好')).toBeInTheDocument();
  await userEvent.click(approved.getByRole('button', { name: '批准' }));
  await userEvent.click(await approved.findByRole('button', { name: '单项回滚' }));
  expect(await approved.findByText('已回滚')).toBeInTheDocument();
  await userEvent.click(rejected.getByRole('button', { name: '驳回' }));
  expect(await rejected.findByText('已驳回')).toBeInTheDocument();
  await userEvent.click(conflict.getByRole('button', { name: '单项回滚' }));
  expect(await conflict.findByText(/目标记忆修订已变化/)).toBeInTheDocument();
  expect(writes).toEqual([
    '/api/evolution/changes/accept/approve',
    '/api/evolution/changes/accept/rollback',
    '/api/evolution/changes/decline/reject',
    '/api/evolution/changes/conflict/rollback',
  ]);
});

it('starts a manual review for the selected conversation and opens its activity', async () => {
  const writes: unknown[] = [];
  const job = { id: 'job', run_id: 'run', status: 'pending', conversation_id: 'c1' };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/evolution/settings') return Response.json(evolutionSettings);
      if (url === '/api/conversations?include_trials=false')
        return Response.json([{ id: 'c1', title: '已有会话' }]);
      if (url === '/api/evolution/reviews') {
        writes.push(JSON.parse(String(options?.body)));
        return Response.json(job, { status: 202 });
      }
      return Response.json(url.startsWith('/api/evolution/jobs?') ? [job] : []);
    }),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole('button', { name: '立即复盘' }));
  await screen.findByRole('option', { name: '已有会话' });
  await userEvent.selectOptions(screen.getByLabelText('复盘会话'), 'c1');
  await userEvent.type(screen.getByLabelText('关注点（可选）'), '只看长期设置');
  await userEvent.click(screen.getByRole('button', { name: '发起复盘' }));
  expect(await screen.findByText('排队中')).toBeInTheDocument();
  expect(writes).toEqual([
    {
      conversation_id: 'c1',
      focus_hint: '只看长期设置',
      job_type: 'memory_review',
      request_key: expect.any(String),
    },
  ]);
});

it('keeps Journey cards and actions usable when audit metadata is empty or missing', async () => {
  const base = {
    action: 'update',
    conversation_id: null,
    review_state: 'pending_review',
    created_at: '2026-11-25T02:00:00Z',
  };
  const changes = [
    { ...base, id: 'empty', diff_summary: {}, evidence_refs: {}, security_scan_result: {} },
    { ...base, id: 'missing' },
    { ...base, id: 'null', diff_summary: null, evidence_refs: null, security_scan_result: null },
  ];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      Response.json(url.startsWith('/api/evolution/changes?') ? changes : []),
    ),
  );
  render(
    <MemoryRouter>
      <PreferencesPage />
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole('button', { name: '复盘记录' }));
  const cards = await screen.findAllByRole('article');
  expect(cards).toHaveLength(3);
  for (const card of cards) expect(within(card).getByText('待审核')).toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: '批准' })).toHaveLength(3);
  expect(screen.queryByText(/^修改前 /)).not.toBeInTheDocument();
});
