import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { RunPage } from './RunsPage';
import { RunDiagnosticsPage } from './RunDiagnosticsPage';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const completedRun = {
  id: 'root',
  root_id: 'root',
  parent_id: null,
  conversation_id: 'conversation',
  kind: 'agent',
  task: '核对两道知识问答',
  status: 'completed',
  generation: 0,
  checkpoint_id: 'checkpoint',
  budgets: { model_requests: 40, tool_calls: 80, searches: 20, active_seconds: 1200 },
  model_usage: { root_used: 4 },
  tool_calls: 3,
  searches: 0,
  active_ms: 10000,
  created_at: '2026-10-13T12:00:00Z',
  last_event: 29,
  outcome: { kind: 'answered', answer: '本次问答已经完成' },
};

const emptyRecords = {
  models: [],
  events: [],
  event_page: { after: 0, next_after: null, total: 0 },
  artifacts: [],
  resources: null,
  continuation: null,
};

const finalRecords = {
  ...emptyRecords,
  models: Array.from({ length: 4 }, (_, index) => ({
    id: `model-${index}`,
    purpose: 'agent',
    usage: { total_tokens: 100 },
    duration_ms: 10,
    first_token_ms: 5,
    status: 'succeeded',
    request: {},
    response: {},
  })),
  events: [{ sequence: 29, kind: 'finished', data: {}, created_at: '2026-10-13T12:00:10Z' }],
  event_page: { after: 0, next_after: null, total: 29 },
};

function renderRun(path = '/runs/root') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/runs/:runId" element={<RunPage />} />
        <Route path="/runs/:runId/diagnostics" element={<RunDiagnosticsPage />} />
        <Route path="/chat/:conversationId" element={<h1>会话正文</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

const emptySummary = {
  tools: [],
  drafts: [],
  deliveries: [],
  tools_truncated: false,
  drafts_truncated: false,
  deliveries_truncated: false,
};

it('never duplicates task activity after repeated diagnostics or leaves it in the conversation', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/runs/root') return Response.json(completedRun);
      if (url.endsWith('/summary')) return Response.json(emptySummary);
      if (url.endsWith('/approvals')) return Response.json({ actions: [], commands: [], mcp: [] });
      if (url.includes('/records')) return Response.json(finalRecords);
      return Response.json([]);
    }),
  );
  renderRun();
  await screen.findByRole('region', { name: '任务产出与活动' });
  for (let cycle = 0; cycle < 3; cycle++) {
    await userEvent.click(screen.getByRole('link', { name: '诊断' }));
    await screen.findByRole('heading', { name: '模型调用 4 次' });
    expect(screen.queryByRole('region', { name: '任务产出与活动' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: '返回任务详情' }));
    await screen.findByRole('region', { name: '任务产出与活动' });
    expect(screen.getAllByRole('region', { name: '任务产出与活动' })).toHaveLength(1);
  }
  await userEvent.click(screen.getByRole('link', { name: '回到对话 →' }));
  await screen.findByRole('heading', { name: '会话正文' });
  expect(screen.queryByRole('region', { name: '任务产出与活动' })).not.toBeInTheDocument();
});

it('shows independent files after details expire without fetching full diagnostics', async () => {
  const fetcher = vi.fn(async (url: string) =>
    Response.json(
      url === '/api/runs/root'
        ? { ...completedRun, status: 'failed', details_available: false, resumable: false }
        : {
            ...emptySummary,
            deliveries: [
              {
                id: 'file',
                name: '已交付笔记.md',
                bytes: 1024,
                view_url: '/deliveries/file',
                url: '/api/deliveries/file/download',
              },
            ],
          },
    ),
  );
  vi.stubGlobal('fetch', fetcher);
  renderRun();
  await screen.findByRole('heading', { name: '运行结果摘要' });
  expect(screen.getByText(/无法原地恢复或导出完整轨迹/)).toBeInTheDocument();
  expect(screen.getByText('本次问答已经完成')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '确认恢复任务' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '诊断' })).not.toBeInTheDocument();
  expect(await screen.findByRole('link', { name: '已交付笔记.md' })).toHaveAttribute(
    'href',
    '/deliveries/file',
  );
  expect(fetcher.mock.calls.some(([url]) => url.includes('/records'))).toBe(false);
});

it('waits for explicit diagnostic opening even when the initial run status arrives late', async () => {
  let finishStatus!: (response: Response) => void;
  const statusResponse = new Promise<Response>((resolve) => {
    finishStatus = resolve;
  });
  let recordReads = 0;
  let statusReads = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/runs/root')
        return ++statusReads === 1 ? statusResponse : Response.json(completedRun);
      if (url.endsWith('/summary')) return Response.json(emptySummary);
      if (url.endsWith('/approvals')) return Response.json({ actions: [], commands: [], mcp: [] });
      if (url.startsWith('/api/runs/root/records')) {
        recordReads += 1;
        return Response.json(finalRecords);
      }
      return Response.json([]);
    }),
  );
  renderRun();
  expect(recordReads).toBe(0);
  await act(async () => finishStatus(Response.json(completedRun)));
  await screen.findByRole('heading', { name: '任务详情' });
  expect(recordReads).toBe(0);
  expect(screen.queryByText('运行标识与检查点')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('link', { name: '诊断' }));
  expect(await screen.findByRole('heading', { name: '模型调用 4 次' })).toBeInTheDocument();
  expect(screen.getByText(/事件 29–29 \/ 29/)).toBeInTheDocument();
  expect(recordReads).toBe(1);
  await userEvent.click(screen.getByRole('link', { name: '返回任务详情' }));
  expect(screen.queryByRole('region', { name: '任务诊断' })).not.toBeInTheDocument();
});

it('does not present loading diagnostics as zero calls and aborts the request when leaving', async () => {
  let signal: AbortSignal | undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url === '/api/runs/root') return Response.json(completedRun);
      if (url.endsWith('/summary')) return Response.json(emptySummary);
      if (url.endsWith('/approvals')) return Response.json({ actions: [], commands: [], mcp: [] });
      if (url.includes('/records')) {
        signal = options?.signal ?? undefined;
        return new Promise<Response>(() => {});
      }
      return Response.json([]);
    }),
  );
  renderRun();
  await screen.findByText('本次问答已经完成');
  expect(signal).toBeUndefined();
  await userEvent.click(screen.getByRole('link', { name: '诊断' }));
  expect(await screen.findByRole('heading', { name: /模型调用/ })).toHaveTextContent('加载中');
  expect(screen.queryByText(/此运行尚未冻结资源/)).not.toBeInTheDocument();
  expect(screen.queryByText(/事件 0–0 \/ 0/)).not.toBeInTheDocument();
  expect(signal?.aborted).toBe(false);
  await userEvent.click(screen.getByRole('link', { name: '回到对话' }));
  expect(signal?.aborted).toBe(true);
  expect(screen.queryByRole('region', { name: '任务诊断' })).not.toBeInTheDocument();
});

it('keeps task results available when diagnostics fail and supports an explicit retry', async () => {
  let reads = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/runs/root') return Response.json(completedRun);
      if (url.endsWith('/summary')) return Response.json(emptySummary);
      if (url.endsWith('/approvals')) return Response.json({ actions: [], commands: [], mcp: [] });
      if (url.includes('/records'))
        return ++reads === 1
          ? Response.json({ detail: '诊断读取暂时失败' }, { status: 503 })
          : Response.json(finalRecords);
      return Response.json([]);
    }),
  );
  renderRun();
  await userEvent.click(await screen.findByRole('link', { name: '诊断' }));
  await screen.findByText('诊断读取暂时失败');
  expect(screen.getByRole('link', { name: '返回任务详情' })).toHaveAttribute('href', '/runs/root');
  await userEvent.click(screen.getByRole('button', { name: '重试加载诊断' }));
  await waitFor(() =>
    expect(screen.getByRole('heading', { name: '模型调用 4 次' })).toBeInTheDocument(),
  );
  expect(reads).toBe(2);
  await userEvent.click(screen.getByRole('link', { name: '返回任务详情' }));
  expect(await screen.findByText('本次问答已经完成')).toBeInTheDocument();
});

it('opens diagnostics directly and does not load task activity or approval controls', async () => {
  const fetcher = vi.fn(async (url: string) =>
    Response.json(url === '/api/runs/root' ? completedRun : finalRecords),
  );
  vi.stubGlobal('fetch', fetcher);
  renderRun('/runs/root/diagnostics');
  await screen.findByRole('heading', { name: '模型调用 4 次' });
  expect(screen.getByRole('heading', { name: '任务诊断' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '回到对话' })).toHaveAttribute(
    'href',
    '/chat/conversation',
  );
  expect(screen.getByRole('link', { name: '导出诊断记录' })).toHaveAttribute(
    'href',
    '/api/runs/root/export',
  );
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    '/api/runs/root',
    '/api/runs/root/records?after=0',
  ]);
  expect(screen.queryByRole('region', { name: '任务产出与活动' })).not.toBeInTheDocument();
});

it('does not fetch expired diagnostics even when visiting a saved diagnostic URL', async () => {
  const fetcher = vi.fn(async () => Response.json({ ...completedRun, details_available: false }));
  vi.stubGlobal('fetch', fetcher);
  renderRun('/runs/root/diagnostics');
  await screen.findByText('诊断详情已过期或正在清理。');
  expect(screen.getByRole('status')).toHaveTextContent('诊断详情已过期或正在清理');
  expect(screen.queryByRole('link', { name: '导出诊断记录' })).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('keeps empty deliveries compact and filters or reverses tool activity', async () => {
  const tools = [
    { id: 'two', name: 'execute', display_name: '执行命令', status: 'error', arg_summary: 'pwd',
      duration_ms: 1420, created_at: '2026-10-13T12:00:02Z' },
    { id: 'one', name: 'read_file', display_name: '读取文件', status: 'returned', arg_summary: 'notes.md',
      duration_ms: 260, created_at: '2026-10-13T12:00:01Z' },
  ];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/summary')) return Response.json({ ...emptySummary, tools });
    if (url.endsWith('/approvals')) return Response.json({ actions: [], commands: [], mcp: [] });
    return Response.json(url === '/api/runs/root' ? completedRun : []);
  }));
  renderRun();
  const panel = within(await screen.findByRole('region', { name: '任务产出与活动' }));
  expect(await panel.findByText('notes.md')).toBeVisible();
  expect(panel.queryByRole('heading', { name: '已交付文件' })).not.toBeInTheDocument();
  expect(panel.getByRole('link', { name: /管理材料与交付文件/ })).toBeVisible();
  const steps = within(panel.getByRole('list', { name: '工具活动' }));
  expect(steps.getAllByRole('listitem')[0]).toHaveTextContent(/读取文件.*notes.md.*260ms/);
  await userEvent.click(panel.getByRole('button', { name: /时间正序/ }));
  expect(steps.getAllByRole('listitem')[0]).toHaveTextContent(/执行命令.*pwd.*1.42s/);
  await userEvent.click(panel.getByRole('button', { name: '仅出错' }));
  expect(steps.getAllByRole('listitem')).toHaveLength(1);
  expect(steps.getByText('执行命令')).toBeVisible();
});

it('uses a scrollable long result and responds when the Markdown shrinks', async () => {
  let measure: ResizeObserverCallback = () => {};
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { measure = callback; }
    observe() {}
    disconnect() {}
  });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/summary')) return Response.json(emptySummary);
    if (url.endsWith('/approvals')) return Response.json({ actions: [], commands: [], mcp: [] });
    return Response.json(url === '/api/runs/root' ? completedRun : []);
  }));
  renderRun();
  const markdown = (await screen.findByText(completedRun.outcome.answer)).closest('.markdown')!;
  expect(screen.queryByRole('button', { name: '展开全高' })).not.toBeInTheDocument();
  Object.defineProperty(markdown, 'scrollHeight', { value: 800, configurable: true });
  act(() => measure([], {} as ResizeObserver));
  const expand = screen.getByRole('button', { name: '展开全高' });
  expect(markdown.closest('[class*="max-h-[300px]"]')).not.toBeNull();
  await userEvent.click(expand);
  expect(markdown.closest('[class*="max-h-[300px]"]')).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: '收起为定高滚动' }));
  expect(markdown.closest('[class*="max-h-[300px]"]')).not.toBeNull();
  Object.defineProperty(markdown, 'scrollHeight', { value: 120 });
  act(() => measure([], {} as ResizeObserver));
  expect(screen.queryByRole('button', { name: '展开全高' })).not.toBeInTheDocument();
});

it('filters only the displayed event page and leaves diagnostics export intact', async () => {
  const kinds = ['model_delta', 'reasoning_delta', 'tool_finished', 'model_finished', 'finished'];
  const records = {
    ...finalRecords,
    events: kinds.map((kind, index) => ({
      kind, sequence: index + 1, created_at: completedRun.created_at, data: { summary: '回滚核对' },
    })),
    event_page: { after: 0, next_after: 200, total: 300 },
  };
  const fetcher = vi.fn(async (url: string) => Response.json(url === '/api/runs/root' ? completedRun : records));
  vi.stubGlobal('fetch', fetcher);
  renderRun('/runs/root/diagnostics');
  await screen.findByText('事件 1–5 / 300');
  expect(screen.queryByText('model_delta')).not.toBeInTheDocument();
  expect(screen.queryByText('reasoning_delta')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('checkbox', { name: /隐藏流式碎片/ }));
  expect(screen.getByText('model_delta')).toBeVisible();
  expect(screen.getByText('reasoning_delta')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '工具' }));
  expect(screen.getByText('tool_finished')).toBeVisible();
  expect(screen.queryByText('model_finished')).not.toBeInTheDocument();
  const search = screen.getByRole('searchbox', { name: '搜索当前页事件' });
  await userEvent.type(search, '回滚');
  expect(screen.getByText('tool_finished')).toBeVisible();
  await userEvent.type(search, '无匹配');
  expect(screen.queryByText('tool_finished')).not.toBeInTheDocument();
  expect(screen.getByText('事件 1–5 / 300')).toBeVisible();
  expect(screen.getByRole('button', { name: '下一页事件' })).toBeEnabled();
  expect(screen.getByRole('link', { name: '导出诊断记录' })).toHaveAttribute('href', '/api/runs/root/export');
  expect(fetcher).toHaveBeenCalledTimes(2);
});
