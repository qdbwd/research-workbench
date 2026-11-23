import { Button } from '../../shared/ui/Button';
import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Download, History, Search, Pencil, Trash2 } from 'lucide-react';
import { BackLink } from '../../shared/ui/Workbench';
import { Dialog } from '../../shared/ui/Dialog';
import { messageOf, send, useResource } from '../../shared/api/api';
import {
  Empty,
  ErrorBox,
  KnowledgeEditor,
  Loading,
  Markdown,
  PageTitle,
} from '../../shared/ui/components';
import type { ChangeSet, Knowledge } from '../../shared/types/types';
import { useWorkspace } from '../../shared/state/workspace';
import { KnowledgeTabs } from './KnowledgeTabs';
import { AssetDeletionDialog, AssetDeletionHistory } from '../sources/AssetDeletion';

export function KnowledgePage() {
  const { version } = useWorkspace();
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [archived, setArchived] = useState(false);
  const [deleting, setDeleting] = useState<Knowledge | null>(null);
  const result = useResource<Knowledge[]>(
    `/api/knowledge?query=${encodeURIComponent(search)}&include_archived=${archived}`,
    version,
  );
  return (
    <>
      <PageTitle title="知识库">
        <a className="button secondary button-secondary" href="/api/knowledge/export">
          <Download size={16} />
          导出 Markdown
        </a>
      </PageTitle>
      <KnowledgeTabs />
      <div className="row flex items-center gap-3 between justify-between toolbar mb-6 flex-wrap">
        <form
          className="search flex min-w-0 max-w-[620px] flex-1 items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(query);
          }}
        >
          <div className="relative min-w-0 flex-1"><input
            type="search"
            className="pr-9"
            aria-label="检索知识库"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索标题、关键词与正文"
          /><Search size={17} className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-control" /></div>
          <Button className="button-secondary shrink-0">检索</Button>
        </form>
        <label className="check m-0 flex-row items-center gap-2 font-normal">
          <input
            type="checkbox"
            checked={archived && !search}
            disabled={Boolean(search)}
            onChange={(e) => setArchived(e.target.checked)}
          />
          含归档（仅浏览）
        </label>
      </div>
      {search && (
        <p className="muted text-muted small text-xs">
          搜索覆盖未归档文章；清空搜索后可浏览归档内容。
        </p>
      )}
      <ErrorBox message={result.error} />
      {result.error ? null : result.loading ? (
        <Loading />
      ) : result.data?.length ? (
        <div className="divide-y divide-line rounded-xl border border-line bg-white px-4">
          {result.data.map((item) => (
            <article
              className="article-card grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 py-3 max-md:grid-cols-1"
              key={item.id || item.document_id}
            >
              <div className="min-w-0">
                <h2 className="m-0 text-base font-semibold [overflow-wrap:anywhere]">
                  {item.title}
                </h2>
                <p className="my-1 text-xs text-control">
                  {[item.category, item.archived ? '已归档' : item.version ? `V${item.version}` : '', ...(item.tags ?? []).slice(0, 3)].filter(Boolean).join(' · ')}
                </p>
                <p className="m-0 line-clamp-2 text-sm text-control [overflow-wrap:anywhere]">{item.summary || item.snippet || item.body?.slice(0, 120)}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
              <Link className="button button-secondary text-xs" to={`/knowledge/${item.id || item.document_id}`} aria-label={`阅读文章：${item.title}`}>阅读文章</Link>
              <Button
                className="button-quiet text-danger"
                aria-label={`删除文章：${item.title}`}
                onClick={() => setDeleting(item)}
              >
                <Trash2 size={15} />
                删除
              </Button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty title={search ? '没有找到匹配的知识' : '还没有发布的知识文章'}>
          {search ? (
            '尝试更具体的词语，或调整检索词。'
          ) : (
            <Link to="/">通过对话整理第一篇文章 →</Link>
          )}
        </Empty>
      )}
      <AssetDeletionHistory kind="knowledge" />
      {deleting && (
        <AssetDeletionDialog
          kind="knowledge"
          identity={deleting.id || deleting.document_id!}
          title={deleting.title}
          onClose={() => setDeleting(null)}
        />
      )}
    </>
  );
}

export function ArticlePage() {
  const navigate = useNavigate();
  const { documentId } = useParams();
  const [params, setParams] = useSearchParams();
  const { version, refresh } = useWorkspace();
  const selected = params.get('version');
  const article = useResource<Knowledge>(
    `/api/knowledge/${documentId}${selected ? `?version_id=${selected}` : ''}`,
    version,
  );
  const history = useResource<Knowledge[]>(`/api/knowledge/${documentId}/versions`, version);
  const [editing, setEditing] = useState(false);
  const [actionError, setActionError] = useState('');
  const [actionBusy, setActionBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  if (article.loading) return <Loading />;
  if (article.error || !article.data) return <ErrorBox message={article.error || '文章不存在'} />;
  const item = article.data;
  const latest = history.data?.[0];
  async function prepareAction(action: 'archive' | 'restore') {
    if (!latest) return;
    setActionBusy(true);
    setActionError('');
    try {
      const draft = await send<ChangeSet>(`/api/knowledge/${item.id}/actions`, {
        action,
        request_key: crypto.randomUUID(),
        base_version_id: latest.version_id,
        restore_version_id: action === 'restore' ? item.version_id : null,
      });
      refresh();
      navigate(`/knowledge/reviews/${draft.id}`);
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setActionBusy(false);
    }
  }
  return (
    <>
      <BackLink to="/knowledge">返回知识库</BackLink>
      <PageTitle eyebrow={`版本 V${item.version}`} title={item.title}>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" className="button-secondary" onClick={() => setShowHistory(true)}>
            <History size={16} /> 版本历史
          </Button>
          <Button
            disabled={Boolean(item.deleted_at)}
            className="secondary button-secondary"
            onClick={() => setEditing(!editing)}
          >
            <Pencil size={15} />
            编辑
          </Button>
          {!item.deleted_at &&
            latest &&
            !latest.archived &&
            item.version_id === latest.version_id && (
              <Button
                className="secondary button-secondary"
                disabled={actionBusy}
                onClick={() => prepareAction('archive')}
              >
                归档文章
              </Button>
            )}
          {!item.deleted_at &&
            latest &&
            (latest.archived || item.version_id !== latest.version_id) && (
              <Button
                className="secondary button-secondary"
                disabled={actionBusy}
                onClick={() => prepareAction('restore')}
              >
                恢复为此版本
              </Button>
            )}
          {!item.deleted_at && (
            <Button className="button-quiet text-danger" onClick={() => setDeleting(true)}>
              <Trash2 size={15} />
              删除文章
            </Button>
          )}
        </div>
      </PageTitle>
      {showHistory && <Dialog title="文章版本历史" onClose={() => setShowHistory(false)}>
        <ErrorBox message={history.error} />
        {history.loading && <Loading />}
        {history.data?.length === 0 && <p>没有可查看的版本。</p>}
        {Boolean(history.data?.length) && <label>文章历史版本
          <select value={item.version_id} onChange={(event) => {
            setShowHistory(false);
            setEditing(false);
            setParams({ version: event.target.value });
          }}>
            {history.data?.map((entry) => (
              <option value={entry.version_id} key={entry.version_id}>
                v{entry.version}{entry.archived ? ' · 归档' : ''}
              </option>
            ))}
          </select>
        </label>}
      </Dialog>}
      <ErrorBox message={actionError} />
      {item.deleted_at && (
        <p className="rounded-lg border border-line p-4 text-sm text-muted">
          这篇文章已从知识库移除。此版本因仍被引用而保留，仅供查看。
        </p>
      )}
      {deleting && (
        <AssetDeletionDialog
          kind="knowledge"
          identity={item.id}
          title={item.title}
          onClose={() => setDeleting(false)}
          onDeleted={() => navigate('/knowledge')}
        />
      )}
      {selected && selected !== history.data?.[0]?.version_id && (
        <p className="notice border-l-[3px] border-[#c0a76b] bg-[#f5f0e0] px-4 py-3 text-[13px] [overflow-wrap:anywhere]">
          正在查看历史版本。编辑保存时仍会检查当前版本，防止覆盖新内容。
        </p>
      )}
      {editing ? (
        <KnowledgeEditor
          key={item.version_id}
          article={item}
          onCancel={() => setEditing(false)}
          onSave={async (edited) => {
            await send(`/api/knowledge/${item.id}/edits`, {
              request_key: crypto.randomUUID(),
              base_version_id: item.version_id,
              title: edited.title,
              body: edited.body,
              tags: edited.tags,
              category: edited.category,
              summary: edited.summary,
              relations: edited.relations ?? [],
            });
            setEditing(false);
            setParams({});
            refresh();
          }}
        />
      ) : (
        <article className="card article mb-5.5 rounded-[11px] border border-line bg-white px-10 py-8 max-lg:p-[25px] max-md:p-4.5 [&>.tags]:mb-5.5">
          <div className="tags flex flex-wrap gap-[7px] [&>span]:rounded-sm [&>span]:bg-[#f1f4ec] [&>span]:px-2 [&>span]:py-[3px] [&>span]:text-[10px] [&>span]:text-[#798971]">
            {item.category && <span>{item.category}</span>}
            {item.tags.map((tag) => (
              <span key={tag}>#{tag}</span>
            ))}
            {item.archived && <span>已归档</span>}
          </div>
          <Markdown text={item.body} />
          {Boolean(item.relations?.length) && (
            <aside className="related mt-[25px] grid gap-1.5 border-t border-line pt-5 text-[13px] [&>h3]:mb-0">
              <h3>相关阅读</h3>
              {item.relations?.map((link) => (
                <Link key={link.target_id} to={`/knowledge/${link.target_id}`}>
                  {link.label} →
                </Link>
              ))}
            </aside>
          )}
        </article>
      )}
    </>
  );
}
