import '@testing-library/jest-dom/vitest';
import 'fake-indexeddb/auto';
import { StrictMode, useEffect } from 'react';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useComposerDraft } from './useComposerDraft';
import { ComposerRecovery } from './ComposerRecovery';
import type { ComposerSession, ComposerState } from './drafts/session';
import { emptyDraft } from './drafts/store';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Editor({ id, changed }: { id?: string; changed: (session: ComposerSession) => void }) {
  const { session, state } = useComposerDraft(undefined, id);
  useEffect(() => changed(session), [session, changed]);
  return <><p>{state.ready ? 'ready' : 'loading'}</p><textarea aria-label="draft" value={state.data.content}
    onChange={(event) => session.edit({ content: event.target.value })} /></>;
}

it('keeps the same editor when its allocated draft identity is written to the URL', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ namespace: crypto.randomUUID(), contract_version: 1 })));
  let editor!: ComposerSession;
  const changed = (session: ComposerSession) => { editor = session; };
  const result = render(<StrictMode><Editor changed={changed} /></StrictMode>);
  await screen.findByText('ready');
  const original = editor;
  const id = original.snapshot().record!.scope.slice(4);
  // 模拟身份归一化与第一笔输入同时发生，旧实现会换掉 session。
  act(() => {
    original.edit({ content: '地址变化期间的输入' });
    result.rerender(<StrictMode><Editor id={id} changed={changed} /></StrictMode>);
  });
  expect(editor).toBe(original);
  await waitFor(() => expect(editor.snapshot().persisted).toBe(true));
  expect(screen.getByRole('textbox')).toHaveValue('地址变化期间的输入');
});

it('avoids flickering draft status text and discard button during fast saves', () => {
  vi.useFakeTimers();
  const baseState: ComposerState = {
    data: { ...emptyDraft(), content: '正在输入的文字' },
    record: {
      key: 'ns::new:1', namespace: 'ns', kind: 'draft', scope: 'new:1', revision: 1, change: 1, deleted: false,
      data: { ...emptyDraft(), content: '正在输入的文字' }, conflicts: [], pending: null, updatedAt: 0,
    },
    ready: true, saving: 1, busy: false, persisted: false, error: '', storageError: '', completed: null,
  };
  const session = {} as ComposerSession;
  const { rerender } = render(<ComposerRecovery state={baseState} session={session} />);
  expect(screen.getByRole('status')).toHaveTextContent('草稿已保存到本浏览器');
  expect(screen.getByRole('button', { name: '放弃草稿' })).toBeEnabled();

  act(() => { vi.advanceTimersByTime(450); });
  expect(screen.getByRole('status')).toHaveTextContent('正在保存草稿…');

  rerender(<ComposerRecovery state={{ ...baseState, saving: 0, persisted: true }} session={session} />);
  expect(screen.getByRole('status')).toHaveTextContent('草稿已保存到本浏览器');
  vi.useRealTimers();
});
