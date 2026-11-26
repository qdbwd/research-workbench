import { Button } from '../../shared/ui/Button';
import { useState } from 'react';
import { Link, Navigate, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { FileUp, FileText, ExternalLink, Search, Trash2 } from 'lucide-react';
import { api, messageOf, send, useResource } from '../../shared/api/api';
import { Badge, Empty, ErrorBox, Loading, PageTitle } from '../../shared/ui/components';
import { Dialog } from '../../shared/ui/Dialog';
import { BackLink } from '../../shared/ui/Workbench';
import type { Evidence, Run, Source, SourceDetail } from '../../shared/types/types';
import { useWorkspace } from '../../shared/state/workspace';
import { DocumentReader } from '../../shared/ui/DocumentReader';
import { evidenceTarget } from '../../shared/ui/EvidenceLink';
import { DeliveredFiles } from './DeliveredFiles';
import { AssetDeletionDialog, AssetDeletionHistory } from './AssetDeletion';

export function SourcesPage() {
  const { version, refresh } = useWorkspace();
  const [search] = useSearchParams();
  const { state } = useLocation();
  const conversationId = search.get('conversation_id');
  const scopeQuery = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
  const result = useResource<Source[]>(`/api/sources${scopeQuery}${conversationId ? '&include_library=false' : ''}`, version);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<Source | null>(null);
  const [importKind, setImportKind] = useState<'file' | 'text' | 'url' | null>(state?.openImport ? 'text' : null);
  const [query, setQuery] = useState('');
  const visibleSources = result.data?.filter((source) =>
    `${source.title} ${source.origin ?? ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );
  const navigate = useNavigate();
  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const form = new FormData();
      form.append('file', file);
      const source = await api<SourceDetail | { source_id: string; job_id: string }>(
        `/api/sources/upload${scopeQuery}`,
        { method: 'POST', body: form },
      );
      refresh();
      navigate('job_id' in source ? `/runs/${source.job_id}` : `/sources/${source.id}${scopeQuery}`);
    } catch (error) {
      setError(messageOf(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {conversationId && <BackLink to={`/chat/${conversationId}`}>返回聊天</BackLink>}
      <PageTitle title={conversationId ? '会话材料' : '材料与来源'}>
        {conversationId && <Link className="button button-secondary" to="/sources">查看材料库</Link>}
        <Button type="button" onClick={() => { setError(''); setImportKind('file'); }}><FileUp size={16} /> 导入材料</Button>
      </PageTitle>
      <ErrorBox message={result.error} />
      {importKind && <Dialog title="导入材料" busy={busy} onClose={() => setImportKind(null)}>
      <label className="mb-4">来源
        <select value={importKind} disabled={busy} onChange={(event) => { setError(''); setImportKind(event.target.value as 'file' | 'text' | 'url'); }}>
          <option value="file">本地文件</option><option value="text">粘贴文本</option><option value="url">公开网页</option>
        </select>
      </label>
      <ErrorBox message={error} />
      {conversationId && <p className="text-sm text-control">材料导入本次会话。需要跨会话使用时，可在详情保存到材料库。</p>}
      {importKind === 'file' &&
        <label className="upload-zone relative mb-5.5 min-h-[285px] cursor-pointer items-center justify-center gap-[15px] rounded-[10px] border border-dashed border-[#bacbaa] bg-[#f0f4e9] text-[#6b865b] max-md:min-h-[200px] [&>span]:text-[11px] [&>span]:text-[#93a482] [&>input]:max-w-[90%] [&>input]:border-0 [&>input]:bg-transparent [&>input]:p-[5px] [&>input]:text-[11px]">
          <FileUp size={27} />
          <strong>{busy ? '正在导入…' : '上传学习材料'}</strong>
          <span>Markdown / TXT / PDF · 最大 10 MiB</span>
          <span>PDF 后台解析，最多 40 页，支持普通中英文扫描页。</span>
          <input
            aria-label="上传材料文件"
            type="file"
            accept=".md,.txt,.pdf"
            disabled={busy}
            onChange={(e) => void upload(e.target.files?.[0])}
          />
        </label>}
        {importKind === 'text' && <form
          className="card mb-5.5 rounded-[11px] border border-line bg-white p-6.5 max-md:p-4.5 text-import grid gap-2.5 [&>h2]:m-0 [&>h2]:text-base [&>button]:justify-self-end"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            try {
              const source = await send<SourceDetail>('/api/sources/text', { title, content, ...(conversationId ? { conversation_id: conversationId } : {}) });
              refresh();
              setImportKind(null);
              navigate(`/sources/${source.id}${scopeQuery}`);
            } catch (error) {
              setError(messageOf(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          <h2>粘贴文本</h2>
          <label className="sr-only" htmlFor="material-title">
            材料标题
          </label>
          <input
            id="material-title"
            placeholder="给这份材料一个标题"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <label className="sr-only" htmlFor="material-content">
            材料内容
          </label>
          <textarea
            id="material-content"
            rows={4}
            placeholder="粘贴正文，稍后在对话中关联…"
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
          <Button disabled={busy || !title.trim() || !content.trim()}>保存材料</Button>
        </form>}
      {importKind === 'url' && <form
        className="card mb-5.5 rounded-[11px] border border-line bg-white p-6.5 max-md:p-4.5 text-import grid gap-2.5 [&>h2]:m-0 [&>h2]:text-base [&>button]:justify-self-end"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError('');
          try {
            const run = await send<Run>('/api/sources/url', {
              url,
              request_key: crypto.randomUUID(),
              ...(conversationId ? { conversation_id: conversationId } : {}),
            });
            refresh();
            setImportKind(null);
            navigate(`/runs/${run.id}`);
          } catch (error) {
            setError(messageOf(error));
          } finally {
            setBusy(false);
          }
        }}
      >
        <h2>导入公开网页</h2>
        <label className="sr-only" htmlFor="material-url">
          网页地址
        </label>
        <input
          id="material-url"
          type="url"
          placeholder="https://…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <p className="muted text-muted small text-xs">
          保存提取后的正文，可随时回看当时的版本。导入后台进行，不自动整理为知识文章。
        </p>
        <Button disabled={busy || !url.trim()}>开始导入网页</Button>
      </form>}
      </Dialog>}
      <div className="relative max-w-80">
        <input type="search" className="pr-9" aria-label="搜索材料" placeholder="搜索材料" value={query}
          onChange={(event) => setQuery(event.target.value)} />
        <Search size={17} className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-control" />
      </div>
      <h2 className="section-title my-4.5 text-[17px] [&>span]:ml-[5px] [&>span]:text-xs [&>span]:text-[#90a184]">
        {conversationId ? '本会话使用的材料' : '已保存来源'} <span>{visibleSources?.length ?? 0}</span>
      </h2>
      {result.error ? null : result.loading ? (
        <Loading />
      ) : visibleSources?.length ? (
        <div className="list-cards grid gap-3">
          {visibleSources.map((source) => (
            <div
              className="list-card flex items-center gap-[17px] rounded-[9px] border border-line bg-white px-[23px] py-4.5 text-[#5c7057] hover:border-[#aec2a5] hover:no-underline max-md:flex-wrap max-md:gap-3 max-md:p-4 [&>div]:min-w-0 [&>div]:flex-1 [&_h3]:mt-0 [&_h3]:mb-[5px] [&_h3]:text-[#344c37] [&_h3]:[overflow-wrap:anywhere] max-md:[&_h3]:text-[13px] [&_p]:m-0 [&_p]:text-[11px] [&_p]:text-[#85917e] [&_p]:[overflow-wrap:anywhere] [&>span:last-child]:text-[11px] [&>span:last-child]:whitespace-nowrap max-md:[&>span:last-child]:hidden source-list-row [&>div]:min-w-0 [&>div]:flex-1 [&>div]:[overflow-wrap:anywhere]"
              key={source.id}
            >
              <FileText size={20} />
              <div>
                <h3>
                  {source.origin && /^https?:\/\//i.test(source.origin) ? (
                    <a href={source.origin} target="_blank" rel="noreferrer noopener">
                      {source.title} ↗
                    </a>
                  ) : (
                    <Link to={`/sources/${source.id}${scopeQuery}`}>{source.title}</Link>
                  )}
                </h3>
                <p>{source.origin || '本项目导入材料'}</p>
                {source.kind === 'web' && (
                  <Link className="mt-2 inline-flex text-xs" to={`/sources/${source.id}${scopeQuery}`}>
                    查看已保存正文
                  </Link>
                )}
              </div>
              <span className="badge inline-flex items-center rounded-[5px] bg-[#edf1eb] px-[9px] py-[3px] text-[11px] leading-[1.6] whitespace-nowrap text-[#5f7765]">
                {source.kind}
              </span>
              {conversationId ? (
                source.independently_saved || source.ownership_known === false ? (
                  <span className="text-xs text-muted">已独立保留</span>
                ) : (
                  <Button type="button" className="secondary button-secondary" disabled={busy}
                    onClick={async () => {
                      setBusy(true); setError('');
                      try { await send(`/api/sources/${source.id}/save`); refresh(); }
                      catch (failure) { setError(messageOf(failure)); }
                      finally { setBusy(false); }
                    }}>保存到材料库</Button>
                )
              ) : null}
              <Button
                type="button"
                className="quiet button-quiet danger text-danger"
                aria-label={`删除材料 ${source.title}`}
                onClick={() => setRemoving(source)}
              >
                <Trash2 size={16} />
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <Empty title={query.trim() ? '没有匹配的材料' : '还没有来源材料'}>{query.trim() ? '试试其他关键词。' : '导入文件或文本后，可在对话中选择它们。'}</Empty>
      )}
      {!conversationId && <DeliveredFiles />}
      <AssetDeletionHistory />
      {removing && <AssetDeletionDialog kind="source" identity={removing.id} title={removing.title}
        conversationId={conversationId} onClose={() => setRemoving(null)} />}
    </>
  );
}

function SourceContent({ source, page }: { source: SourceDetail; page: string | null }) {
  const [parsed, setParsed] = useState(false);
  const content = useResource<{
    text: string;
    segments: { id: string; page: number | null; text: string; status: string; error?: string }[];
  }>(source.kind === 'pdf' && !parsed ? null : `/api/source-versions/${source.version_id}/content`);
  return (
    <>
      {source.kind === 'pdf' && (
        <div className="reader-toolbar mb-4.5 flex items-center gap-3">
          <Button
            className="secondary button-secondary small text-xs"
            onClick={() => setParsed(!parsed)}
          >
            {parsed ? '查看 PDF 原件' : '查看提取文本'}
          </Button>
        </div>
      )}
      {source.kind === 'pdf' && !parsed ? (
        <iframe
          className="pdf-reader block h-[75dvh] w-full rounded-lg border border-line"
          title={source.title}
          src={`/api/source-versions/${source.version_id}/original${page ? `#page=${encodeURIComponent(page)}` : ''}`}
        />
      ) : content.loading ? (
        <Loading />
      ) : content.error ? (
        <ErrorBox message={content.error} />
      ) : (
        content.data &&
        (source.kind === 'pdf' ? (
          content.data.segments.map((item) => (
            <section key={item.id}>
              <h3>第 {item.page} 页</h3>
              {item.status !== 'ready' && (
                <ErrorBox message={item.error || '本页提取失败，请查看原件'} />
              )}
              <DocumentReader text={item.text} />
            </section>
          ))
        ) : (
          <DocumentReader
            text={content.data.text}
            markdown={
              source.body_format
                ? source.body_format === 'markdown'
                : source.kind !== 'file' || /\.md$/i.test(source.title)
            }
          />
        ))
      )}
    </>
  );
}

export function SourcePage() {
  const { sourceId } = useParams();
  const { version: workspaceVersion, refresh } = useWorkspace();
  const [saving, setSaving] = useState(false);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState('');
  const [params] = useSearchParams();
  const { state } = useLocation();
  const citationFrom = typeof state?.citationFrom === 'string' && state.citationFrom.startsWith('/knowledge/') ? state.citationFrom : null;
  const version = params.get('version');
  const conversationId = params.get('conversation_id');
  const result = useResource<SourceDetail>(
    `/api/sources/${sourceId}${version ? `?version_id=${encodeURIComponent(version)}` : ''}`,
    workspaceVersion,
  );
  if (result.loading) return <Loading />;
  if (!result.data) return <ErrorBox message={result.error} />;
  const source = result.data;
  return (
    <>
      <BackLink to={citationFrom ?? (conversationId ? `/sources?conversation_id=${encodeURIComponent(conversationId)}` : '/sources')}>
        {citationFrom ? '返回知识正文' : conversationId ? '返回会话材料' : '返回材料与来源'}
      </BackLink>
      <PageTitle eyebrow={`版本 V${source.version}`} title={source.title}>
        <Badge status={source.status} />
        {!source.deleted && source.status === 'ready' && (source.independently_saved || savedId === source.id || source.ownership_known === false) &&
          <Link className="text-xs font-medium text-brand hover:underline"
            to={`/retrieval?tab=docs&asset=source:${encodeURIComponent(source.id)}`}>查看分块与向量 →</Link>}
      </PageTitle>
      <ErrorBox message={saveError} />
      {source.independently_saved || savedId === source.id ? (
        <p className="text-xs text-muted">已独立保存在材料库。</p>
      ) : source.ownership_known && !source.deleted ? (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <span className="text-xs text-muted">需要以后独立使用这份正文，可以保存到材料库。</span>
          <Button className="button-secondary" disabled={saving} onClick={async () => {
            setSaving(true); setSaveError('');
            try {
              await send(`/api/sources/${source.id}/save`);
              setSavedId(source.id); refresh();
            } catch (failure) { setSaveError(messageOf(failure)); }
            finally { setSaving(false); }
          }}>保存到材料库</Button>
        </div>
      ) : null}
      {source.deleted && (
        <p className="notice border-l-[3px] border-[#c0a76b] bg-[#f5f0e0] px-4 py-3 text-[13px] [overflow-wrap:anywhere]">
          此材料已从材料库移除；这里保留引用时的完整版本。
        </p>
      )}
      <div className="card mb-5.5 rounded-[11px] border border-line bg-white p-6.5 max-md:p-4.5 source-reader min-w-0">
        <ErrorBox message={source.error} />
        <div className="row flex items-center gap-3 wrap flex-wrap reader-toolbar mb-4.5 flex items-center gap-3">
          {source.kind !== 'web' && source.original_available !== false && (
            <a
              href={`/api/source-versions/${source.version_id}/original`}
              target="_blank"
              rel="noreferrer"
              className="button secondary button-secondary"
            >
              <FileText size={16} />
              查看原件
            </a>
          )}
          {source.origin && /^https?:\/\//i.test(source.origin) && (
            <a
              href={source.origin}
              target="_blank"
              rel="noreferrer noopener"
              className="button secondary button-secondary"
            >
              <ExternalLink size={16} />
              原始网页
            </a>
          )}
          <span className="muted text-muted small text-xs">完整保存版本 v{source.version}</span>
          {source.captured_at && (
            <span className="text-xs text-muted">
              保存于 {new Date(source.captured_at).toLocaleString('zh-CN')}
            </span>
          )}
        </div>
        {source.kind === 'web' && (
          <p className="mb-5 text-xs text-muted">
            这里是抓取时提取的正文，保留可识别的标题、代码和表格。网页交互、布局及部分动态内容可能未包含，可打开原始网页核对。
          </p>
        )}
        <SourceContent key={source.version_id} source={source} page={params.get('page')} />
      </div>
    </>
  );
}

export function EvidencePage() {
  const { evidenceId } = useParams();
  const { state } = useLocation();
  const result = useResource<Evidence>(`/api/evidence/${evidenceId}`);
  if (result.loading) return <Loading />;
  if (!result.data) return <ErrorBox message={result.error} />;
  const evidence = result.data;
  const target = evidenceTarget(evidence);
  if (target && !/^https?:\/\//i.test(target)) return <Navigate to={target} state={state} replace />;
  // 旧书签仍可访问。网页优先给原始链接，保存的完整快照作为可选回看入口。
  return (
    <>
      <PageTitle eyebrow="SOURCE" title={evidence.title} />
      {target && (
        <a className="button" href={target} target="_blank" rel="noreferrer noopener">
          打开原始网页 ↗
        </a>
      )}
      {evidence.source_id && evidence.source_version_id && (
        <Link
          className="button secondary button-secondary"
          to={`/sources/${evidence.source_id}?version=${encodeURIComponent(evidence.source_version_id)}`}
        >
          查看完整保存版本
        </Link>
      )}
      {!target && <ErrorBox message="此引用没有可打开的完整来源。" />}
    </>
  );
}
