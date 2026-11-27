import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { Run } from '../../shared/types/types';
import { LiveRunPanel } from './LiveRunPanel';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  listeners: Record<string, ((event: MessageEvent) => void)[]> = {};
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners[type] = [...(this.listeners[type] ?? []), listener];
  }

  emit(type: string, payload: unknown) {
    const message = { data: JSON.stringify(payload) } as MessageEvent;
    for (const listener of this.listeners[type] ?? []) listener(message);
  }

  close() {
    this.closed = true;
  }
}

afterEach(() => {
  cleanup();
  FakeEventSource.instances = [];
  vi.unstubAllGlobals();
});

const baseRun: Run = {
  id: 'run-live-1',
  root_id: 'run-live-1',
  checkpoint_id: 'run-live-1',
  parent_id: null,
  conversation_id: 'conv-1',
  kind: 'agent',
  status: 'running',
  task: '自主研发推进',
  input: {
    publication_mode: 'preview',
    knowledge_only: false,
    delegation: true,
    source_ids: [],
  },
  outcome: {},
  budgets: { model_requests: 40, tool_calls: 120, searches: 0, active_seconds: 900 },
  tool_calls: 0,
  searches: 0,
  active_ms: 0,
  generation: 1,
  cancel_requested: false,
  pause_requested: false,
  stop_reason: null,
  created_at: '2026-11-24T09:00:00Z',
  last_event: 0,
  child_count: 0,
  question_count: 0,
  details_available: true,
};

it('streams collapsible reasoning and preserves multi-step tool timeline without duplicates', async () => {
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json([])));
  const user = userEvent.setup();
  const onChanged = vi.fn();

  render(
    <MemoryRouter>
      <LiveRunPanel initial={baseRun} onChanged={onChanged} />
    </MemoryRouter>,
  );

  const source = FakeEventSource.instances[0];
  expect(source.url).toBe('/api/runs/run-live-1/events');

  act(() => {
    source.emit('progress', { sequence: 1, kind: 'reasoning_delta', data: { text: '第一步先查材料列表。' } });
    // 重复序号应被去重，不重复拼接
    source.emit('progress', { sequence: 1, kind: 'reasoning_delta', data: { text: '第一步先查材料列表。' } });
    source.emit('progress', { sequence: 2, kind: 'tool_started', data: { name: 'list_materials', tool_call_id: 'c1' } });
    source.emit('progress', { sequence: 3, kind: 'tool_finished', data: { name: 'list_materials', tool_call_id: 'c1', status: 'success' } });
    source.emit('progress', { sequence: 4, kind: 'tool_started', data: { name: 'read_source', tool_call_id: 'c2' } });
    source.emit('progress', { sequence: 5, kind: 'model_protocol_error', data: { kind: 'unexecuted_tool_markup', retrying: true } });
  });

  expect(screen.getByTestId('reasoning-stream')).toHaveTextContent('第一步先查材料列表。');
  expect(screen.getByTestId('reasoning-stream').textContent).toBe('第一步先查材料列表。');
  const stepsList = screen.getByRole('list', { name: '执行步骤' });
  expect(stepsList).toHaveTextContent('查看材料列表');
  expect(stepsList).toHaveTextContent('阅读材料正文');
  expect(stepsList).toHaveTextContent('模型调用格式异常，正在自动纠偏重试');

  act(() => {
    source.emit('progress', { sequence: 6, kind: 'model_finished', data: { tool_calls: 1 } });
  });
  expect(stepsList).toHaveTextContent('模型调用格式异常，已自动纠偏重试');

  await user.click(screen.getByRole('button', { name: '思考过程' }));
  expect(screen.queryByTestId('reasoning-stream')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '思考过程' }));
  expect(screen.getByTestId('reasoning-stream')).toBeVisible();
  expect(screen.getByLabelText('实时执行进度')).toHaveClass('border');
});

it.each([false, true])('streams full Markdown and honors manual reasoning choice: %s', async (manual) => {
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json([])));
  const openEvidence = vi.fn();
  const user = userEvent.setup();
  render(<MemoryRouter><LiveRunPanel initial={baseRun} variant="inline"
    onChanged={vi.fn()} onOpenEvidence={openEvidence} /></MemoryRouter>);
  const source = FakeEventSource.instances[0];
  function emit(sequence: number, kind: string, data: object) {
    act(() => source.emit('progress', { sequence, kind, data, created_at: '2026-11-24T09:00:00Z' }));
  }
  emit(1, 'reasoning_delta', { text: '核对材料中' });
  expect(screen.getByTestId('reasoning-stream')).toBeVisible();
  const reasoning = screen.getByRole('button', { name: /思考过程/ });
  if (manual) {
    await user.click(reasoning);
    await user.click(reasoning);
  }
  emit(2, 'model_delta', { text: '' });
  expect(screen.getByTestId('reasoning-stream')).toBeVisible();
  emit(3, 'tool_started', { name: 'execute', tool_call_id: 'exec', summary: 'pwd', args: { command: 'pwd' } });
  emit(4, 'tool_finished', { name: 'execute', tool_call_id: 'exec', duration_ms: 260,
    content: '/workspace\nAuthorization: Bearer offline-test-token' });
  emit(5, 'model_delta', { text: '## 核对结论\n\n**执行成功**\n\n[固定证据](/api/evidence/e-1)' });
  expect(screen.getByLabelText('实时执行进度')).not.toHaveClass('border');
  expect(screen.getByRole('heading', { name: '核对结论' }).closest('.markdown')?.parentElement)
    .not.toHaveClass('max-h-[180px]');
  expect(screen.getByText('执行成功').tagName).toBe('STRONG');
  expect(Boolean(screen.queryByTestId('reasoning-stream'))).toBe(manual);
  await user.click(screen.getByRole('link', { name: '固定证据' }));
  expect(openEvidence).toHaveBeenCalledWith('e-1', '固定证据', expect.any(HTMLAnchorElement));
  const command = screen.getByRole('button', { name: /执行工作区命令.*pwd.*260ms/ });
  await user.click(command);
  expect(screen.getByText(/\/workspace/).closest('pre')).toHaveClass('bg-[#18251f]');
  expect(screen.getByLabelText('终端输出预览')).not.toHaveTextContent('offline-test-token');
  await user.click(screen.getByRole('button', { name: /已执行 1 个步骤/ }));
  expect(screen.queryByRole('list', { name: '执行步骤' })).not.toBeInTheDocument();
});
