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
});
