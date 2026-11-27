import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useResource } from '../../shared/api/api';
import type { Run } from '../../shared/types/types';
import { Button } from '../../shared/ui/Button';
import { ErrorBox, Loading } from '../../shared/ui/components';
import { formatDuration } from './runPresentation';

type Summary = {
  tools: {
    id: string; name: string; display_name: string; status: string; created_at: string;
    arg_summary?: string | null; duration_ms?: number | null;
  }[];
  tools_truncated: boolean;
  drafts: { id: string; title: string | null; status: string }[];
  drafts_truncated: boolean;
  deliveries: { id: string; name: string; bytes: number; url: string; view_url: string }[];
  deliveries_truncated: boolean;
};
const statuses: Record<string, string> = {
  started: '已开始',
  returned: '已返回',
  replayed: '复用已保存结果',
  error: '未完成或出错',
  unsettled: '结果尚未核对',
};
const hasError = (tool: Summary['tools'][number]) => ['error', 'unsettled'].includes(tool.status);

/** 日常任务详情只读取有界摘要，内部产物和模型输入留在诊断入口。 */
export function TaskActivity({ run, refreshKey }: { run: Run; refreshKey: string }) {
  const [expanded, setExpanded] = useState(false);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [newestFirst, setNewestFirst] = useState(false);
  const [retry, setRetry] = useState(0);
  const result = useResource<Summary>(`/api/runs/${run.id}/summary`, `${refreshKey}:${retry}`);
  const data = result.data;
  const tools = data?.tools ?? [];
  const errors = tools.filter(hasError).length;
  const timed = tools.filter((tool) => tool.duration_ms != null);
  const total = timed.reduce((sum, tool) => sum + (tool.duration_ms ?? 0), 0);
  // API 按最后一次观测的序号倒序返回，界面默认从较早的步骤开始阅读。
  const ordered = tools.slice().reverse().map((tool, index) => ({ tool, number: index + 1 }));
  if (newestFirst) ordered.reverse();
  const visible = errorsOnly && errors ? ordered.filter(({ tool }) => hasError(tool)) : ordered;
  const hasDeliveries = Boolean(data && (data.deliveries.length || data.drafts.length));
  return (
    <section
      className="mb-6 min-w-0 rounded-xl border border-line bg-white p-5 max-md:p-4"
      aria-label="任务产出与活动"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 text-base font-semibold">任务产出与活动</h2>
        <Link className="text-xs" to="/sources">管理材料与交付文件 →</Link>
      </div>
      <ErrorBox message={result.error} />
      {result.error && (
        <Button className="secondary button-secondary" onClick={() => setRetry((n) => n + 1)}>
          重试加载任务摘要
        </Button>
      )}
      {result.loading && !result.data && <Loading />}
      {data && (
        <>
          {hasDeliveries ? <div className="mb-4 rounded-lg border border-line bg-canvas p-3">
            <h3 className="mt-0 mb-2 text-sm font-semibold">任务产出</h3>
            <div className="scrollbar-subtle grid max-h-[220px] gap-3 overflow-y-auto overscroll-contain pr-1 text-sm">
              {data.deliveries.length > 0 && <div>
                <h4 className="my-1 text-xs font-semibold text-control">已交付文件</h4>
                {data.deliveries.map((file) => <div key={file.id}
                  className="flex flex-wrap items-center justify-between gap-2 border-b border-line py-2 last:border-0">
                  <Link className="min-w-0 [overflow-wrap:anywhere]" to={file.view_url}>{file.name}</Link>
                  <a className="text-xs" href={file.url} download>下载 · {(file.bytes / 1024).toFixed(1)} KB</a>
                </div>)}
                {data.deliveries_truncated && <p className="text-xs text-muted">这里显示最近 50 份交付。</p>}
              </div>}
              {data.drafts.length > 0 && <div>
                <h4 className="my-1 text-xs font-semibold text-control">知识草稿与变更</h4>
                {data.drafts.map((draft) => <div key={draft.id} className="py-1 [overflow-wrap:anywhere]">
                  <Link to={`/knowledge/reviews/${draft.id}`}>{draft.title || '查看知识变更'} →</Link>
                </div>)}
                {data.drafts_truncated && <Link to="/knowledge/reviews">查看全部知识变更 →</Link>}
              </div>}
            </div>
          </div> : <p className="mt-0 mb-3 text-xs text-muted">本次任务暂无文件或知识草稿。</p>}
          {run.details_available !== false && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-2">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <h3 className="m-0 text-sm font-semibold text-ink">工具活动</h3>
                  <span>{tools.length} 步</span>
                  {errors > 0 && <span className="text-danger">{errors} 步出错或待核对</span>}
                  {timed.length > 0 && <span>可观测耗时 {formatDuration(total)}</span>}
                </div>
                {tools.length > 0 && <div className="flex flex-wrap items-center gap-1.5">
                  {errors > 0 && <div role="group" aria-label="工具活动筛选" className="flex rounded-lg bg-canvas p-0.5">
                    <Button className={`small text-xs ${errorsOnly ? 'button-quiet' : 'button-secondary'}`}
                      aria-pressed={!errorsOnly} onClick={() => setErrorsOnly(false)}>全部</Button>
                    <Button className={`small text-xs ${errorsOnly ? 'button-secondary' : 'button-quiet'}`}
                      aria-pressed={errorsOnly} onClick={() => setErrorsOnly(true)}>仅出错</Button>
                  </div>}
                  <Button className="small button-secondary text-xs" onClick={() => setNewestFirst((value) => !value)}>
                    顺序：{newestFirst ? '最新优先 ↓' : '时间正序 ↑'}
                  </Button>
                </div>}
              </div>
              {tools.length > 0 ? <ol aria-label="工具活动"
                className="scrollbar-subtle m-0 max-h-[380px] list-none divide-y divide-line overflow-y-auto overscroll-contain p-0 pr-1">
                {(expanded ? visible : visible.slice(0, 6)).map(({ tool, number }) => (
                  <li key={tool.id} className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 gap-y-1 px-1 py-2.5 text-xs md:grid-cols-[auto_minmax(0,1fr)_auto]">
                    <span className="font-mono text-muted">#{number}</span>
                    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                      <span className={`rounded px-1.5 py-0.5 ${hasError(tool) ? 'bg-[#fff1ec] text-danger' : 'bg-sidebar text-brand'}`}>
                        {statuses[tool.status] || tool.status}
                      </span>
                      <span className="min-w-0 break-all font-medium" title={tool.name}>{tool.display_name}</span>
                      {tool.arg_summary && <code className="max-w-full truncate rounded bg-canvas px-1.5 py-0.5 text-control"
                        title={tool.arg_summary}>{tool.arg_summary}</code>}
                    </div>
                    <div className="col-start-2 flex flex-wrap gap-2 tabular-nums text-muted md:col-start-auto">
                      {tool.duration_ms != null && <span>{formatDuration(tool.duration_ms)}</span>}
                      <time dateTime={tool.created_at}>{new Date(tool.created_at).toLocaleTimeString()}</time>
                    </div>
                  </li>
                ))}
              </ol> : <p className="text-xs text-muted">暂无工具活动。</p>}
              {visible.length > 6 && (
                <Button
                  className="secondary button-secondary mt-3"
                  aria-expanded={expanded}
                  onClick={() => setExpanded(!expanded)}
                >
                  {expanded ? '收起工具活动' : `展开工具活动（${visible.length}）`}
                </Button>
              )}
              {data.tools_truncated && (
                <p className="mb-0 text-xs text-muted">显示最近 50 项工具活动 · <Link to={`/runs/${run.id}/diagnostics`}>查看完整事件诊断 →</Link></p>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
