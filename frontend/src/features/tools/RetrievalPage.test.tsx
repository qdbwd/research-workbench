import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RetrievalPage, type RetrievalStatus } from './RetrievalPage';

function makeStatus(overrides: Partial<RetrievalStatus> = {}): RetrievalStatus {
  return {
    pgvector: { installed: true, version: '0.8.6' },
    settings: {
      hybrid_enabled: false,
      auto_index_enabled: true,
      rerank_enabled: false,
      contextual_embedding_enabled: true,
      rrf_k: 60,
      rerank_top_k: 20,
      active_profile_id: 'tei:harrier-oss-v1-270m:640',
      revision: 1,
      updated_at: '2026-11-25T01:00:00Z',
    },
    effective_mode: 'bm25_only',
    effective_reason: '向量混合检索未启用，当前使用联合 BM25',
    embedding_provider: {
      provider: 'tei',
      configured: true,
      model: 'microsoft/harrier-oss-v1-270m',
      dimensions: 640,
      endpoint: 'http://tei-embedding:80',
      normalize: true,
      query_prefix: '',
    },
    reranker_provider: {
      provider: 'disabled',
      configured: false,
      model: '',
      endpoint: '',
    },
    coverage: {
      knowledge_total_chunks: 10,
      knowledge_embedded_chunks: 8,
      source_total_chunks: 5,
      source_embedded_chunks: 5,
      total_chunks: 15,
      embedded_chunks: 13,
      ratio: 0.8667,
    },
    profiles: [
      {
        profile_id: 'tei:harrier-oss-v1-270m:640',
        provider: 'tei',
        model: 'microsoft/harrier-oss-v1-270m',
        dimensions: 640,
        query_prefix: '',
        document_prefix: '',
        normalize: true,
        contextual: true,
        status: 'ready',
        knowledge_chunks: 8,
        source_chunks: 5,
        updated_at: '2026-11-25T01:00:00Z',
        active: true,
      },
    ],
    index_queue: { pending: 0, indexing: 0, ready: 0, failed: 0 },
    collections_count: 0,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('RetrievalPage', () => {
  it('renders pgvector status, saves RAG settings, and triggers incremental rebuild', async () => {
    const initial = makeStatus();
    const updated = makeStatus({
      settings: { ...initial.settings, hybrid_enabled: true, auto_index_enabled: false, revision: 2 },
      effective_mode: 'hybrid',
      effective_reason: 'BM25 + pgvector RRF 混合检索已就绪',
    });

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method ?? 'GET';
      if (url.includes('/api/retrieval/assets?')) return Response.json({ items: [], total: 0 });
      if (url.endsWith('/api/retrieval/collections')) return Response.json([]);
      if (url.endsWith('/api/retrieval/status') && method === 'GET') {
        return new Response(JSON.stringify(initial), { status: 200 });
      }
      if (url.endsWith('/api/retrieval/settings') && method === 'PATCH') {
        return new Response(JSON.stringify(updated), { status: 200 });
      }
      if (url.endsWith('/api/retrieval/rebuild') && method === 'POST') {
        return new Response(
          JSON.stringify({
            summary: {
              profile_id: 'tei:harrier-oss-v1-270m:640',
              dimensions: 640,
              contextual: true,
              knowledge_embedded: 2,
              knowledge_skipped: 8,
              source_embedded: 0,
              source_skipped: 5,
              elapsed_ms: 42.5,
            },
            status: updated,
          }),
          { status: 200 },
        );
      }
      return new Response('not found', { status: 404 });
    });

    render(
      <MemoryRouter initialEntries={['/retrieval']}>
        <RetrievalPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('tab', { name: '引擎与档案配置' }));
    expect(await screen.findByText(/已安装 \(v0\.8\.6\)/)).toBeTruthy();
    expect(screen.getByText(/13 \/ 15 \(87%\)/)).toBeTruthy();

    fireEvent.click(screen.getByLabelText(/入库自动异步向量化/));
    const hybridCheckbox = screen.getByLabelText(/启用向量混合检索/);
    fireEvent.click(hybridCheckbox);
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => {
      expect(screen.getByText('RAG 检索设置已保存。')).toBeTruthy();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/retrieval/settings',
      expect.objectContaining({ method: 'PATCH' }),
    );

    fireEvent.click(screen.getByRole('button', { name: '增量补建向量索引' }));
    await waitFor(() => {
      expect(screen.getByText(/向量索引增量补建完成/)).toBeTruthy();
    });
  });

  it('manages collection membership, toggles immutable chunks, and runs recall diagnostics', async () => {
    const collection = { id: 'group-1', name: '运维集合', description: '', revision: 1, asset_count: 0,
      knowledge_count: 0, source_count: 0, chunks_total: 0, chunks_embedded: 0, updated_at: '' };
    const asset = { asset_kind: 'knowledge', asset_id: 'asset-1', version_id: 'version-1', version: 2, title: '回滚规范',
      subtitle: '运维', collections: [], chunks_total: 1, chunks_enabled: 1, chunks_embedded: 1, index_status: 'ready',
      last_error: null, updated_at: '' };
    let groups: typeof collection[] = [];
    let enabled = true;
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = new URL(String(input), 'http://localhost');
      const method = init?.method ?? 'GET';
      if (url.pathname === '/api/retrieval/status') return Response.json(makeStatus());
      if (url.pathname === '/api/retrieval/collections') {
        if (method === 'POST') { groups = [collection]; return Response.json(collection, { status: 201 }); }
        return Response.json(groups);
      }
      if (url.pathname.endsWith('/items')) return Response.json({ ...collection, asset_count: 1, revision: 2 });
      if (url.pathname === '/api/retrieval/assets') {
        const items = url.searchParams.get('limit') === '100' ? [] : [asset];
        return Response.json({ items, total: items.length });
      }
      if (url.pathname.endsWith('/chunks/status')) {
        enabled = JSON.parse(init?.body as string).enabled;
        return Response.json({ enabled });
      }
      if (url.pathname.endsWith('/chunks')) return Response.json({ asset, version_id: 'version-1', profile_id: 'tei:test:4',
        total: 1, enabled_count: Number(enabled), disabled_count: Number(!enabled), items: [{ chunk_index: 0,
          segment_id: null, page: null, start: 0, end: 4, char_count: 4, text: '固定正文', contextual_header: '标题：回滚规范',
          enabled, vector_status: 'ready', dimensions: 4, updated_at: null }] });
      if (url.pathname.endsWith('/hit-test')) return Response.json({ requested_mode: 'hybrid_rerank',
        effective_mode: 'hybrid', degraded_reason: '重排服务未配置', timing_ms: { total_ms: 4 }, hits: [] });
      return new Response('missing', { status: 404 });
    });
    render(<MemoryRouter initialEntries={['/retrieval']}><RetrievalPage /></MemoryRouter>);
    expect(screen.getByRole('tab', { name: '知识集与文档索引' }).getAttribute('aria-selected')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: '新建' }));
    fireEvent.change(screen.getByLabelText('知识集名称'), { target: { value: '运维集合' } });
    fireEvent.click(screen.getByRole('button', { name: '创建知识集' }));
    fireEvent.click(await screen.findByRole('button', { name: '管理成员' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /回滚规范/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存成员' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/retrieval/collections/group-1/items',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ expected_revision: 1,
        items: [{ asset_kind: 'knowledge', asset_id: 'asset-1' }] }) })));

    fireEvent.click(await screen.findByRole('button', { name: '分块预览 (1)' }));
    expect(await screen.findByText('固定正文')).toBeTruthy();
    expect(screen.getByRole('link', { name: '前往原文编辑 →' }).getAttribute('href')).toBe('/knowledge/asset-1');
    fireEvent.click(screen.getByRole('switch', { name: '排除检索' }));
    fireEvent.click(await screen.findByRole('switch', { name: '恢复检索' }));
    await screen.findByRole('switch', { name: '排除检索' });
    fireEvent.click(screen.getByRole('button', { name: /^关闭$/ }));

    fireEvent.click(screen.getByRole('tab', { name: '召回测试（Hit Testing）' }));
    fireEvent.change(screen.getByLabelText('测试查询语句'), { target: { value: '回滚规范' } });
    fireEvent.click(screen.getByRole('button', { name: '混合 + 重排' }));
    fireEvent.click(screen.getByRole('button', { name: '运行召回测试' }));
    expect(await screen.findByText('重排服务未配置')).toBeTruthy();
  });

  it('links back to the source text from a material chunk preview', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = new URL(String(input), 'http://localhost').pathname;
      if (path === '/api/retrieval/status') return Response.json(makeStatus());
      if (path === '/api/retrieval/collections') return Response.json([]);
      if (path === '/api/retrieval/assets') return Response.json({ items: [], total: 0 });
      return new Response('missing', { status: 404 });
    });
    render(<MemoryRouter initialEntries={['/retrieval?asset=source:source-1']}><RetrievalPage /></MemoryRouter>);
    const link = screen.getByRole('link', { name: '查看来源原文 →' });
    expect(link.getAttribute('href')).toBe('/sources/source-1');
  });


});
