// 请求失败后的重试和用户明确再次发送是不同动作，不能共享永久幂等键。
import '@testing-library/jest-dom/vitest';
import 'fake-indexeddb/auto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ChatPage } from './ChatPage';
import { WorkspaceContext } from '../../shared/state/workspace';
import { LearningPage } from '../learning/LearningPage';

let namespace: string;
beforeEach(() => { namespace = crypto.randomUUID(); });
function configureFetch(handler: (url: string, options?: RequestInit) => Promise<Response>) {
  vi.stubGlobal('fetch', (url: string, options?: RequestInit) => url === '/api/client-state'
    ? Promise.resolve(Response.json({ namespace, contract_version: 1 })) : handler(url, options));
}
async function sendMessage(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: '发送' }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
});

it('reloads knowledge collections when the workspace version changes', async () => {
  let name = '原知识集';
  configureFetch(async (url) => {
    if (url === '/api/retrieval/collections') return Response.json([{ id: 'group', name,
      asset_count: 1, chunks_embedded: 1, chunks_total: 1 }]);
    return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
  });
  const view = (version: number) => (
    <MemoryRouter><WorkspaceContext.Provider value={{ version, refresh: () => {} }}>
      <ChatPage />
    </WorkspaceContext.Provider></MemoryRouter>
  );
  const { rerender } = render(view(0));
  const mount = screen.getByRole('button', { name: '挂载知识库' });
  await waitFor(() => expect(mount).toBeEnabled());
  await userEvent.click(mount);
  await userEvent.click(screen.getByRole('button', { name: '按知识集多选' }));
  await screen.findByText('原知识集');

  name = '更新后的知识集';
  rerender(view(1));
  expect(await screen.findByText('更新后的知识集')).toBeVisible();
  expect(screen.queryByText('原知识集')).not.toBeInTheDocument();
});

it('submits on Enter while keeping Shift+Enter for newline', async () => {
  const posts: string[] = [];
  configureFetch(async (url, options) => {
    if (options?.method === 'POST') {
      posts.push(url);
      return Response.json({ id: url === '/api/conversations' ? 'enter-chat' : 'enter-run' });
    }
    if (url.endsWith('/runtime-settings')) return Response.json({ permission_mode: 'ask', reasoning_effort: 'default', revision: 0 });
    return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
  });
  const user = userEvent.setup();
  render(<MemoryRouter><Routes>
    <Route path="/" element={<ChatPage />} />
    <Route path="/chat/:conversationId" element={<ChatPage />} />
  </Routes></MemoryRouter>);
  const input = screen.getByRole('textbox', { name: '发送给研发助手的消息' });
  await waitFor(() => expect(input).toBeEnabled());
  await user.type(input, '第一行{Shift>}{Enter}{/Shift}第二行');
  expect(input).toHaveValue('第一行\n第二行');
  expect(posts).toHaveLength(0);
  await user.type(input, '{Enter}');
  await waitFor(() => expect(posts).toEqual(['/api/conversations', '/api/conversations/enter-chat/messages']));
  await waitFor(() => expect(input).toHaveValue(''));
});

it('ignores background evolution_review runs in chat footer and keeps mounted collection after send', async () => {
  const bodies: Record<string, unknown>[] = [];
  const runPageUrls: string[] = [];
  configureFetch(async (url, options) => {
    if (options?.method === 'POST') {
      bodies.push(JSON.parse(String(options.body)));
      return Response.json({ id: 'next-run' });
    }
    if (url === '/api/retrieval/collections') {
      return Response.json([{ id: 'col-pay', name: '支付专题', asset_count: 3, chunks_embedded: 6, chunks_total: 6 }]);
    }
    if (url.endsWith('/runtime-settings')) {
      return Response.json({ permission_mode: 'ask', reasoning_effort: 'default', revision: 0 });
    }
    if (url.includes('/runs/page')) {
      runPageUrls.push(url);
      if (!url.includes('status=')) {
        return Response.json({
          items: [
            {
              id: 'evo-1', kind: 'evolution_review', status: 'completed', task: '后台复盘',
              tool_calls: 0, active_ms: 1500, outcome: { kind: 'evolution_review' }, input: {},
            },
            {
              id: 'main-1', kind: 'agent', status: 'completed', task: '主问答',
              tool_calls: 8, active_ms: 22900, outcome: { kind: 'answered' }, input: {},
            },
          ],
          next_cursor: null,
        });
      }
    }
    if (url.includes('/messages/page')) {
      return Response.json({ items: [], revision: 1, total: 0, start: 0, older_cursor: null, newer_cursor: null });
    }
    return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
  });
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={['/chat/chat-1']}><Routes>
    <Route path="/chat/:conversationId" element={<ChatPage />} />
  </Routes></MemoryRouter>);
  expect(await screen.findByText(/8 步工具/)).toBeVisible();
  expect(screen.queryByText('evolution_review')).not.toBeInTheDocument();
  expect(runPageUrls.length).toBeGreaterThanOrEqual(3);
  expect(runPageUrls.every((url) => url.includes('exclude_kind=evolution_review'))).toBe(true);

  const mount = screen.getByRole('button', { name: '挂载知识库' });
  await waitFor(() => expect(mount).toBeEnabled());
  await user.click(mount);
  await user.click(screen.getByRole('button', { name: '按知识集多选' }));
  await user.click(await screen.findByLabelText(/支付专题/));

  const input = screen.getByRole('textbox', { name: '发送给研发助手的消息' });
  await user.type(input, '第一轮提问');
  await sendMessage(user);
  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({ rag_scope: 'collections', collection_ids: ['col-pay'] });
  await waitFor(() => expect(input).toHaveValue(''));

  await user.type(input, '第二轮追问');
  await sendMessage(user);
  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(bodies[1]).toMatchObject({ rag_scope: 'collections', collection_ids: ['col-pay'] });
});

it('prefills a trial draft without creating a conversation until submission', async () => {
  const mutations: { url: string; body: Record<string, unknown> }[] = [];
  configureFetch(async (url, options) => {
    if (url === '/api/research-trial') return Response.json({
      key: 'operations-pilot-v1', title: '运维试点', notice: '模拟资料',
      prompts: [{ id: 'answer', mode: 'chat', label: '试用带引用的问答', text: '谁需要批准紧急变更？' }],
      knowledge: ['变更规程'], materials: ['操作手册'],
    });
    if (options?.method === 'POST') {
      mutations.push({ url, body: JSON.parse(String(options.body)) });
      return Response.json({ id: url === '/api/conversations' ? 'trial-conversation' : 'trial-run' });
    }
    if (url.endsWith('/runtime-settings')) return Response.json({ permission_mode: 'ask', reasoning_effort: 'default', revision: 0 });
    return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
  });
  const user = userEvent.setup();
  render(<MemoryRouter><Routes>
    <Route path="/" element={<ChatPage />} />
    <Route path="/chat/:conversationId" element={<ChatPage />} />
  </Routes></MemoryRouter>);
  const trial = await screen.findByRole('button', { name: '试用带引用的问答' });
  await waitFor(() => expect(trial).toBeEnabled());
  await user.click(trial);
  expect(screen.getByText('变更规程')).toBeVisible();
  const input = screen.getByRole('textbox', { name: '发送给研发助手的消息' });
  expect(input).toHaveValue('谁需要批准紧急变更？');
  expect(mutations).toHaveLength(0);
  await user.type(input, '也请引用手册。');
  await sendMessage(user);
  await waitFor(() => expect(mutations).toEqual([
    { url: '/api/conversations', body: expect.objectContaining({ trial_case: 'operations-pilot-v1' }) },
    { url: '/api/conversations/trial-conversation/messages', body: expect.objectContaining({ content: '谁需要批准紧急变更？也请引用手册。' }) },
  ]));
});

it('opens a fixed citation alongside chat and returns focus without losing the draft', async () => {
  configureFetch(async (url) => {
    if (url.includes('/messages/page')) return Response.json({
      items: [{ id: 'answer', role: 'assistant', content: '请核对[当时的说明](/api/evidence/old)。',
        created_at: '2026-11-21T02:00:00Z' }],
      revision: 1, total: 1, start: 0, older_cursor: null, newer_cursor: null,
    });
    if (url === '/api/evidence/old') return Response.json({
      id: 'old', title: '历史规程', text: '需要审批', start: 2, end: 6,
      document_id: 'procedure', knowledge_version_id: 'version-old', version: 2,
      citation: '/api/evidence/old',
    });
    if (url === '/api/knowledge/procedure?version_id=version-old') return Response.json({ body: '📄先需要审批，然后登记。' });
    return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
  });
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/chat/conversation']}>
      <Routes>
        <Route path="/chat/:conversationId" element={<ChatPage />} />
        <Route path="/knowledge/:id" element={<p>已离开会话</p>} />
      </Routes>
    </MemoryRouter>,
  );
  const input = screen.getByRole('textbox', { name: '发送给研发助手的消息' });
  await waitFor(() => expect(input).toBeEnabled());
  await user.type(input, '我还没写完');
  const citation = await screen.findByRole('link', { name: '当时的说明' });
  await user.click(citation);
  const mark = await screen.findByText('需要审批');
  expect(mark.tagName).toBe('MARK');
  expect(mark.parentElement).toHaveTextContent('📄先需要审批，然后登记。');
  expect(screen.queryByText('已离开会话')).not.toBeInTheDocument();
  expect(input).toHaveValue('我还没写完');
  await user.click(screen.getByRole('button', { name: '关闭 当时的说明' }));
  expect(screen.queryByRole('region', { name: '资料查看器' })).not.toBeInTheDocument();
  await waitFor(() => expect(citation).toHaveFocus());
  expect(input).toHaveValue('我还没写完');
});

it('reviews the research brief and map in a closable tab without leaving the conversation', async () => {
  const task = {
    id: 'pilot', title: '内部问答试点', status: 'open', home_conversation_id: 'conversation', control_version: 1,
    main_branch_id: 'main-branch', head: { id: 'initial', number: 1, state: {
      phase: 'investigation', brief: { goal: '核对问答失败的原因', key_decisions: '复现错误引用', next_action: '比较候选片段' },
    } },
  };
  configureFetch(async (url) => {
    if (url.endsWith('/research-task') || url === '/api/research-tasks/pilot') return Response.json(task);
    if (url.split('?')[0].endsWith('/map')) return Response.json({ revision_id: 'initial', number: 1, nodes: [] });
    if (url.includes('/revisions')) return Response.json({ items: [], next_before: null });
    return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
  });
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={['/chat/conversation']}><Routes>
    <Route path="/chat/:conversationId" element={<ChatPage />} />
  </Routes></MemoryRouter>);
  const input = screen.getByRole('textbox', { name: '发送给研发助手的消息' });
  await waitFor(() => expect(input).toBeEnabled());
  await user.type(input, '先验证旧版误引。');
  await user.click(await screen.findByRole('button', { name: '查看研发简报' }));
  expect(screen.getByRole('tab', { name: '研发任务' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText('核对问答失败的原因')).toBeVisible();
  await user.click(screen.getByRole('button', { name: '研发地图' }));
  expect(await screen.findByText('研究地图还没有主题。')).toBeVisible();
  await user.click(screen.getByRole('button', { name: '关闭研发任务标签' }));
  expect(input).toBeVisible();
  expect(input).toHaveValue('先验证旧版误引。');
});

it('starts knowledge organization from the composer without sending or taking publication permission', async () => {
  const submissions: { content: string; publication_mode: string; knowledge_only: boolean }[] = [];
  configureFetch(
    vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        submissions.push(JSON.parse(String(options.body)));
        return Response.json({ id: 'run-organize' }, { status: 202 });
      }
      return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
    }),
  );
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/chat/conversation']}>
      <Routes><Route path="/chat/:conversationId" element={<ChatPage />} /></Routes>
    </MemoryRouter>,
  );
  const input = screen.getByRole('textbox', { name: /发送给.*的消息/ });
  await waitFor(() => expect(input).toBeEnabled());
  await user.type(input, '只整理检查点的恢复条件。');
  await user.click(screen.getByRole('button', { name: '更多设置与材料' }));
  await user.click(screen.getByRole('button', { name: '整理成知识' }));
  expect(input).toHaveValue('请将相关讨论或材料整理成知识库文章。\n\n只整理检查点的恢复条件。');
  expect(input).toHaveFocus();
  expect(submissions).toHaveLength(0);
  await sendMessage(user);
  await waitFor(() => expect(submissions).toEqual([expect.objectContaining({
    content: '请将相关讨论或材料整理成知识库文章。\n\n只整理检查点的恢复条件。',
    publication_mode: 'ask',
    knowledge_only: false,
  })]));
});

it.each([true, false])('binds learning-task creation to the current conversation even if it is absent from recent chats (listed: %s)', async (listed) => {
  const requests: string[] = [];
  configureFetch(async (url, options) => {
    if (options?.method === 'POST') {
      requests.push(url);
      const body = JSON.parse(String(options.body));
      expect(body.conversation_id).toBe('conversation');
      return Response.json({ id: 'new-learning-task' }, { status: 201 });
    }
    if (url === '/api/conversations') {
      return Response.json(listed ? [{ id: 'conversation', title: '当前研究会话' }] : []);
    }
    return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
  });
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/chat/conversation']}>
      <Routes>
        <Route path="/chat/:conversationId" element={<ChatPage />} />
        <Route path="/learning" element={<LearningPage />} />
        <Route path="/learning/new-learning-task" element={<p>已创建学习任务</p>} />
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.queryByRole('heading', { name: '对话' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '删除会话' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '更多设置与材料' }));
  await user.click(screen.getByRole('link', { name: '建立学习任务' }));
  await screen.findByRole('option', { name: listed ? '当前研究会话' : '当前会话' });
  expect(screen.getByRole('combobox', { name: '会话' })).toHaveValue('conversation');
  expect(requests).toEqual([]);
  await user.type(screen.getByRole('textbox', { name: '任务标题' }), '理解检查点');
  await user.type(screen.getByRole('textbox', { name: '想了解什么、用于什么' }), '核对恢复规则');
  expect(screen.getByRole('button', { name: '建立任务' })).toBeEnabled();
  await user.click(screen.getByRole('button', { name: '建立任务' }));
  expect(await screen.findByText('已创建学习任务')).toBeInTheDocument();
  expect(requests).toEqual(['/api/learning-tasks']);
});

it('shows only provider-declared reasoning levels and submits the selected value', async () => {
  const submissions: { reasoning_effort: string }[] = [];
  let settings = { permission_mode: 'ask', reasoning_effort: 'default', revision: 0 };
  configureFetch(
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith('/runtime-settings')) {
        if (options?.method === 'PATCH')
          settings = {
            ...settings,
            ...JSON.parse(String(options.body)),
            revision: settings.revision + 1,
          };
        return Response.json(settings);
      }
      if (url === '/api/model-capabilities')
        return Response.json({
          levels: ['low', 'high', 'max'],
          default_level: 'high',
          can_disable: true,
        });
      if (options?.method === 'POST') {
        submissions.push(JSON.parse(String(options.body)));
        return Response.json({ id: 'reasoning-run' }, { status: 202 });
      }
      return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
    }),
  );
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/chat/conversation']}>
      <Routes>
        <Route path="/chat/:conversationId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
  const selector = await screen.findByRole('combobox', { name: '思考强度' });
  expect(screen.queryByRole('option', { name: '中' })).not.toBeInTheDocument();
  await waitFor(() => expect(selector).toBeEnabled());
  await user.selectOptions(selector, 'max');
  await waitFor(() => expect(selector).toHaveValue('max'));
  await waitFor(() => expect(screen.getByRole('textbox', { name: '发送给研发助手的消息' })).toBeEnabled());
  await user.type(screen.getByRole('textbox', { name: '发送给研发助手的消息' }), '分析材料');
  await sendMessage(user);
  await waitFor(() => expect(submissions[0].reasoning_effort).toBe('max'));
});

it('shows model-input context percentage and explains its measurement basis', async () => {
  configureFetch(
    vi.fn(async (url: string) => {
      if (url.endsWith('/context-usage'))
        return Response.json({
          input_tokens: 131072,
          model_window: 1048576,
          percentage: 50,
          method: 'provider_usage',
          estimated: false,
          basis: 'last_model_input',
          output_reserve: 8192,
          framing_reserve: 4096,
          summary_trigger_tokens: 235929,
          summary_fraction: 0.9,
          summary_trigger_basis: 'input_tokens',
          working_window: 262144,
        });
      return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
    }),
  );
  render(
    <MemoryRouter initialEntries={['/chat/conversation']}>
      <Routes>
        <Route path="/chat/:conversationId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText('上下文 50%')).toBeInTheDocument();
  const detail = screen.getByText(/131,072/);
  expect(detail).toHaveTextContent('262,144');
  expect(detail).toHaveTextContent('最近一次模型输入');
  expect(detail).toHaveTextContent('供应商用量');
});

it('reuses an uncertain request key but creates a fresh key after confirmed success', async () => {
  const submissions: { content: string; request_key: string }[] = [];
  configureFetch(
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.includes('/submission-receipt?')) return Response.json({ state: 'not_accepted' });
      if (options?.method === 'POST') {
        submissions.push(JSON.parse(String(options.body)));
        if (submissions.length === 1) throw new TypeError('连接中断，提交结果未知');
        return Response.json({ id: `run-${submissions.length}` }, { status: 202 });
      }
      return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
    }),
  );
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/chat/conversation']}>
      <Routes>
        <Route path="/chat/:conversationId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
  const input = screen.getByRole('textbox', { name: '发送给研发助手的消息' });
  await waitFor(() => expect(input).toBeEnabled());
  await user.type(input, '继续');
  await sendMessage(user);
  expect(await screen.findByText('上次提交结果待核对')).toBeInTheDocument();
  expect(input).toHaveValue('继续');
  await user.click(screen.getByRole('button', { name: '核对提交结果' }));
  await user.click(await screen.findByRole('button', { name: '继续原提交' }));
  await waitFor(() => expect(input).toHaveValue(''));
  await user.type(input, '继续');
  await sendMessage(user);
  await waitFor(() => expect(submissions).toHaveLength(3));
  expect(submissions[0].request_key).toBe(submissions[1].request_key);
  expect(submissions[2].request_key).not.toBe(submissions[1].request_key);
});

it('keeps the original request frozen while changed options remain in the next draft', async () => {
  const submissions: { knowledge_only: boolean; request_key: string }[] = [];
  configureFetch(
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.includes('/submission-receipt?')) return Response.json({ state: 'not_accepted' });
      if (options?.method === 'POST') {
        submissions.push(JSON.parse(String(options.body)));
        if (submissions.length === 1) throw new TypeError('连接中断，提交结果未知');
        return Response.json({ id: 'run-knowledge-only' }, { status: 202 });
      }
      return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
    }),
  );
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/chat/conversation']}>
      <Routes>
        <Route path="/chat/:conversationId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await user.click(screen.getByRole('button', { name: '更多设置与材料' }));
  await user.click(screen.getByRole('button', { name: '本次任务设置' }));
  expect(screen.queryByRole('checkbox', { name: '允许辅助模型' })).not.toBeInTheDocument();
  let option = screen.getByRole('checkbox', { name: '仅使用知识库' });
  expect(option).not.toBeChecked();
  await user.click(screen.getByRole('button', { name: '完成' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: '发送给研发助手的消息' })).toBeEnabled());
  await user.type(screen.getByRole('textbox', { name: '发送给研发助手的消息' }), '核对材料');
  await sendMessage(user);
  expect(await screen.findByText('上次提交结果待核对')).toBeInTheDocument();
  expect(submissions[0].knowledge_only).toBe(false);
  await user.click(screen.getByRole('button', { name: '更多设置与材料' }));
  await user.click(screen.getByRole('button', { name: '本次任务设置' }));
  option = screen.getByRole('checkbox', { name: '仅使用知识库' });
  await user.click(option);
  await user.click(screen.getByRole('button', { name: '完成' }));
  await user.click(screen.getByRole('button', { name: '核对提交结果' }));
  await user.click(await screen.findByRole('button', { name: '继续原提交' }));
  await waitFor(() => expect(submissions).toHaveLength(2));
  expect(submissions[1].knowledge_only).toBe(false);
  expect(submissions[1].request_key).toBe(submissions[0].request_key);
  await sendMessage(user);
  await waitFor(() => expect(submissions).toHaveLength(3));
  expect(submissions[2].knowledge_only).toBe(true);
  expect(submissions[2].request_key).not.toBe(submissions[0].request_key);
});

it('lets the user turn subagent delegation off without forcing it back on', async () => {
  const submissions: { delegation: boolean }[] = [];
  configureFetch(
    vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        submissions.push(JSON.parse(String(options.body)));
        return Response.json({ id: 'run-no-delegation' }, { status: 202 });
      }
      return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
    }),
  );
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/chat/conversation']}>
      <Routes>
        <Route path="/chat/:conversationId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await user.click(screen.getByRole('button', { name: '更多设置与材料' }));
  await user.click(screen.getByRole('button', { name: '本次任务设置' }));
  const option = screen.getByRole('checkbox', { name: '允许子Agent分工' });
  if ((option as HTMLInputElement).checked) await user.click(option);
  await user.click(screen.getByRole('button', { name: '完成' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: '发送给研发助手的消息' })).toBeEnabled());
  await user.type(screen.getByRole('textbox', { name: '发送给研发助手的消息' }), '自行核对');
  await sendMessage(user);
  await waitFor(() => expect(submissions).toHaveLength(1));
  expect(submissions[0].delegation).toBe(false);
  await user.click(screen.getByRole('button', { name: '更多设置与材料' }));
  await user.click(screen.getByRole('button', { name: '本次任务设置' }));
  expect(screen.getByRole('checkbox', { name: '允许子Agent分工' })).not.toBeChecked();
});

it('shows deleted mounted collection as removable stale chip so users can clear it before sending', async () => {
  let collections = [{ id: 'col-gone', name: '临时知识集', asset_count: 1, chunks_embedded: 1, chunks_total: 1 }];
  configureFetch(async (url) => {
    if (url === '/api/retrieval/collections') return Response.json(collections);
    if (url.endsWith('/runtime-settings')) {
      return Response.json({ permission_mode: 'ask', reasoning_effort: 'default', revision: 0 });
    }
    return Response.json(url.includes('/page') ? { items: [], next_cursor: null } : []);
  });
  const user = userEvent.setup();
  const view = (version: number) => (
    <MemoryRouter initialEntries={['/chat/chat-stale']}>
      <WorkspaceContext.Provider value={{ version, refresh: () => {} }}>
        <Routes>
          <Route path="/chat/:conversationId" element={<ChatPage />} />
        </Routes>
      </WorkspaceContext.Provider>
    </MemoryRouter>
  );
  const { rerender } = render(view(0));
  const mount = screen.getByRole('button', { name: '挂载知识库' });
  await waitFor(() => expect(mount).toBeEnabled());
  await user.click(mount);
  await user.click(screen.getByRole('button', { name: '按知识集多选' }));
  await user.click(await screen.findByText('临时知识集'));
  expect(await screen.findByRole('button', { name: '移除挂载 临时知识集' })).toBeVisible();

  // 模拟后台删除该知识集后刷新工作区
  collections = [];
  rerender(view(1));
  expect(await screen.findByText('已失效知识集')).toBeVisible();
  await user.click(screen.getByRole('button', { name: '移除挂载 已失效知识集' }));
  await waitFor(() => expect(screen.queryByText('已失效知识集')).not.toBeInTheDocument());
});
