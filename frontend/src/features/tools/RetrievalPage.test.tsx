import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RetrievalPage, type RetrievalStatus } from './RetrievalPage';

function makeStatus(overrides: Partial<RetrievalStatus> = {}): RetrievalStatus {
  return {
    pgvector: { installed: true, version: '0.8.6' },
    settings: {
      hybrid_enabled: false,
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
      settings: { ...initial.settings, hybrid_enabled: true, revision: 2 },
      effective_mode: 'hybrid',
      effective_reason: 'BM25 + pgvector RRF 混合检索已就绪',
    });

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method ?? 'GET';
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

    expect(await screen.findByText(/已安装 \(v0\.8\.6\)/)).toBeTruthy();
    expect(screen.getByText(/13 \/ 15 \(87%\)/)).toBeTruthy();

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
});
