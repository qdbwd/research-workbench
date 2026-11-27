import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useResource } from '../../shared/api/api';
import type { ModelRecord, Run, RunEvent } from '../../shared/types/types';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox, Loading } from '../../shared/ui/components';
import { BrowserScreenshotButton } from './BrowserScreenshotButton';
import { RuntimeResources, type RuntimeManifest, type Continuation } from './RuntimeResources';
import { formatDuration } from './runPresentation';

type Records = {
  models: ModelRecord[];
  events: RunEvent[];
  event_page: { after: number; next_after: number | null; total: number };
  artifacts: { id: string; kind: string; length: number | null; available?: boolean }[];
  resources?: RuntimeManifest | null;
  continuation?: Continuation | null;
};

const categories = { all: '全部', tools: '工具', models: '模型', state: '状态与错误' };
type Category = keyof typeof categories;
const categoryOf = (kind: string): Exclude<Category, 'all'> =>
  /^(tool_|browser_)/.test(kind) ? 'tools' : /^(model_|reasoning_)/.test(kind) ? 'models' : 'state';
const eventTone = {
  tools: 'bg-[#e7f1e8] text-brand',
  models: 'bg-[#e8eff8] text-[#315684]',
  state: 'bg-sidebar text-control',
};
const isDelta = (event: RunEvent) => ['model_delta', 'reasoning_delta'].includes(event.kind);
const modelStatuses: Record<string, string> = {
  succeeded: '成功', failed: '失败', reserved: '已预占', running: '进行中', unknown: '结果待核对',
};

function eventSummary(event: RunEvent) {
  const { data } = event;
  // 搜索轻量可读字段，完整内容由 JSON 弹窗按需展开。
  return ['summary', 'name', 'purpose', 'status', 'reason', 'message', 'error', 'action', 'source_id', 'text']
    .map((key) => data[key])
    .filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))
    .map((value) => value.slice(0, 240).replace(/\s+/g, ' ').trim())
    .join(' · ');
}

type Selection = { title: string; event: RunEvent } | { title: string; model: ModelRecord };
const modalPre = 'm-0 rounded-lg bg-canvas p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]';
const diagnosticCard = 'min-w-0 scroll-mt-4 rounded-xl border border-line bg-white p-5 max-md:p-4';

function DiagnosticKpis({ data, loading }: { data: Records | null; loading: boolean }) {
  const models = data?.models ?? [];
  const tokens = models.flatMap((record) => record.usage?.total_tokens ?? []);
  const first = models.flatMap((record) => record.first_token_ms ?? []);
  const durations = models.flatMap((record) => record.duration_ms ?? []);
  const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);
  const entries: [label: string, value: string | null, note: string, target: string][] = [
    ['模型调用', data && `${models.length} 次`, tokens.length > 0 ? `累计 ${sum(tokens).toLocaleString()} tokens` : '', 'diag-models'],
    ['平均首字 / 总耗时', data && `${first.length ? formatDuration(sum(first) / first.length) : '—'} / ${durations.length ? formatDuration(sum(durations)) : '—'}`, '', 'diag-models'],
    ['持久化事件', data && `${data.event_page.total} 条`, '', 'diag-events'],
    ['内部运行资料', data && `${data.artifacts.length} 项`, '', 'diag-artifacts'],
  ];
  return <nav aria-label="诊断区块" className="grid grid-cols-2 gap-2 md:grid-cols-4 md:gap-3">
    {entries.map(([label, value, note, target]) => <a key={label} href={`#${target}`}
      onClick={(event) => {
        // SPA 内页锚点留在本页，顺带把键盘焦点移到可定位的分区。
        event.preventDefault();
        const section = document.getElementById(target);
        section?.focus({ preventScroll: true });
        section?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      }}
      className="block min-w-0 rounded-xl border border-line bg-white p-3 text-xs text-muted hover:border-brand focus-visible:outline-brand">
      <span>{label}</span>
      <strong className="mt-1 block text-base font-semibold text-ink [overflow-wrap:anywhere]">{value ?? (loading ? '加载中…' : '—')}</strong>
      {note && <span className="mt-1 block">{note}</span>}
    </a>)}
  </nav>;
}

/** 仅在诊断路由内挂载；离页/切换任务即取消未完成请求并释放大记录。 */
export function RunDiagnostics({ run, refreshKey }: { run: Run; refreshKey: string }) {
  const [after, setAfter] = useState(0);
  const [retry, setRetry] = useState(0);
  const [category, setCategory] = useState<Category>('all');
  const [hideDelta, setHideDelta] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Selection>();
  const records = useResource<Records>(
    run.details_available === false ? null : `/api/runs/${run.id}/records?after=${after}`,
    `${refreshKey}:${retry}`,
  );
  const data = records.data;
  if (run.details_available === false) return <p role="status">诊断详情已过期或正在清理。</p>;
  const query = search.trim().toLowerCase();
  const events = data?.events ?? [];
  const filtered = events.map((event) => ({ event, summary: eventSummary(event), type: categoryOf(event.kind) }))
    .filter(({ event, summary, type }) => (!hideDelta || !isDelta(event))
      && (category === 'all' || category === type)
      && (!query || `${event.kind} ${summary}`.toLowerCase().includes(query)));
  return (
    <section id="run-diagnostics" className="mt-5 grid min-w-0 gap-4" aria-label="任务诊断">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="m-0 text-base font-semibold">诊断记录</h2>
          {run.details_expires_at && <p className="mt-1 mb-0 text-xs text-muted">
            详情保留至：{new Date(run.details_expires_at).toLocaleString()}
          </p>}
        </div>
        <a className="button secondary button-secondary text-xs" href={`/api/runs/${run.id}/export`}>
          导出诊断记录
        </a>
      </div>
      <ErrorBox message={records.error} />
      {records.error && (
        <Button className="secondary button-secondary justify-self-start" onClick={() => setRetry((n) => n + 1)}>
          重试加载诊断
        </Button>
      )}
      {records.loading && !data && <Loading />}
      <DiagnosticKpis data={data} loading={records.loading} />
      <div className="grid gap-3 md:grid-cols-2">
        <section className="min-w-0 rounded-xl border border-line bg-white p-4">
          <h2 className="mt-0 mb-3 text-sm font-semibold">运行标识与检查点</h2>
          <dl className="m-0 grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 gap-y-2 text-xs [&_dt]:text-muted [&_dd]:m-0 [&_dd]:font-mono [&_dd]:[overflow-wrap:anywhere]">
            <dt>运行 ID</dt><dd>{run.id}</dd>
            <dt>根任务 ID</dt><dd>{run.root_id}</dd>
            <dt>检查点</dt><dd>{run.checkpoint_id}</dd>
            <dt>调度代次</dt><dd>{run.generation}</dd>
          </dl>
        </section>
        <RuntimeResources resources={data?.resources} continuation={data?.continuation}
          unavailable={data ? undefined : records.error ? '运行依据暂不可用。' : '正在加载运行依据…'} />
      </div>
      <section id="diag-models" tabIndex={-1} className={diagnosticCard} aria-labelledby="diag-models-heading">
        <h3 id="diag-models-heading" className="mt-0 mb-3 text-base font-semibold">
          模型调用{' '}
          {records.loading ? data ? '更新中…' : '加载中…' : data ? `${data.models.length} 次` : '记录暂不可用'}
        </h3>
        {data?.models.length ? <ol aria-label="模型调用记录"
          className="scrollbar-subtle m-0 max-h-[300px] list-none divide-y divide-line overflow-y-auto overscroll-contain p-0 pr-1">
          {data.models.map((record, index) => (
            <li key={record.id} className="flex min-w-0 flex-wrap items-center justify-between gap-2 py-2 text-xs">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-mono text-muted">#{index + 1}</span>
                <span className="font-semibold [overflow-wrap:anywhere]">{record.purpose}</span>
                <span className={`rounded px-1.5 py-0.5 ${record.status === 'failed' ? 'bg-[#fff1ec] text-danger' : 'bg-sidebar text-brand'}`}>
                  {modelStatuses[record.status] ?? record.status}
                </span>
                <span className="text-muted">{record.usage?.total_tokens ?? '未知'} tokens</span>
                <span className="text-muted">耗时 {record.duration_ms == null ? '未知' : formatDuration(record.duration_ms)}</span>
                <span className="text-muted">首响应 {record.first_token_ms == null ? '未观测到' : formatDuration(record.first_token_ms)}</span>
              </div>
              <Button className="small button-secondary text-xs" aria-label={`查看第 ${index + 1} 次模型请求 / 响应`}
                onClick={() => setSelected({ title: `模型调用 #${index + 1} · ${record.purpose}`, model: record })}>
                查看请求 / 响应
              </Button>
            </li>
          ))}
        </ol> : data && <p className="mb-0 text-xs text-muted">没有可查看的模型调用。</p>}
      </section>
      <section id="diag-artifacts" tabIndex={-1} className={diagnosticCard} aria-labelledby="diag-artifacts-heading">
        <h3 id="diag-artifacts-heading" className="mt-0 mb-3 text-base font-semibold">内部运行资料</h3>
        {data?.artifacts.length ? <ul aria-label="运行资料列表"
          className="scrollbar-subtle m-0 max-h-[240px] list-none divide-y divide-line overflow-y-auto overscroll-contain p-0 pr-1">
          {data.artifacts.map((artifact) => <li key={artifact.id}
            className="flex min-w-0 flex-wrap items-center justify-between gap-2 py-2 text-xs">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="rounded bg-sidebar px-2 py-0.5 font-mono text-control [overflow-wrap:anywhere]">{artifact.kind}</span>
              {artifact.kind !== 'browser_screenshot' && <span className="text-muted">{artifact.length ?? '未知'} 字符</span>}
            </div>
            {artifact.available === false ? <span className="text-muted">正文已过期</span>
              : artifact.kind === 'browser_screenshot' ? <BrowserScreenshotButton runId={run.id} artifactId={artifact.id} />
                : <Link to={`/runs/${run.id}/artifacts/${artifact.id}`}>查看原文 →</Link>}
          </li>)}
        </ul> : data && <p className="mb-0 text-xs text-muted">没有可查看的内部资料。</p>}
      </section>
      <section id="diag-events" tabIndex={-1} className={diagnosticCard} aria-labelledby="diag-events-heading">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 id="diag-events-heading" className="m-0 text-base font-semibold">事件时间线</h3>
            <p className="mt-1 mb-0 text-xs text-muted">
              {data ? `事件 ${events[0]?.sequence ?? 0}–${events.at(-1)?.sequence ?? 0} / ${data.event_page.total}`
                : records.error ? '事件记录暂不可用。' : '正在加载事件记录…'}
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Button className="small button-secondary text-xs" disabled={records.loading || after === 0}
              onClick={() => setAfter(Math.max(0, after - 200))}>上一页事件</Button>
            <Button className="small button-secondary text-xs" disabled={records.loading || data?.event_page.next_after == null}
              onClick={() => setAfter(data?.event_page.next_after ?? after)}>下一页事件</Button>
          </div>
        </div>
        {data && <>
          <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg bg-canvas p-2">
            <div role="group" aria-label="事件分类" className="flex flex-wrap gap-1">
              {(Object.keys(categories) as Category[]).map((item) => <Button key={item}
                aria-pressed={category === item} className={`small text-xs ${category === item ? 'button-secondary' : 'button-quiet'}`}
                onClick={() => setCategory(item)}>{categories[item]}</Button>)}
            </div>
            <label className="m-0 inline-flex flex-row items-center gap-1.5 text-xs text-control">
              <input type="checkbox" className="m-0 size-4 accent-brand" checked={hideDelta}
                onChange={(event) => setHideDelta(event.target.checked)} />
              隐藏流式碎片（delta）
            </label>
            <input type="search" aria-label="搜索当前页事件" value={search} placeholder="搜索当前页事件"
              className="m-0 min-w-0 flex-1 rounded-md border border-line bg-white px-2 py-1 text-xs md:ml-auto md:max-w-60"
              onChange={(event) => setSearch(event.target.value)} />
          </div>
          <p className="my-2 text-xs text-muted">当前显示 {filtered.length} 条</p>
          {filtered.length > 0 ? <ol key={after} aria-label="事件记录"
            className="scrollbar-subtle m-0 max-h-[420px] list-none divide-y divide-line overflow-y-auto overscroll-contain p-0 pr-1">
            {filtered.map(({ event, summary, type }) => (
              <li key={event.sequence} className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-x-2 gap-y-1 py-2 text-xs md:grid-cols-[auto_auto_minmax(0,1fr)_auto_auto]">
                <span className="font-mono text-muted">#{event.sequence}</span>
                <span className={`min-w-0 justify-self-start rounded px-1.5 py-0.5 font-mono [overflow-wrap:anywhere] ${
                  /error|failed/.test(event.kind) ? 'bg-[#fff1ec] text-danger' : eventTone[type]}`}>{event.kind}</span>
                {summary && <span title={summary} className="col-span-3 col-start-2 row-start-2 min-w-0 truncate text-control md:col-span-1 md:col-start-3 md:row-start-1">{summary}</span>}
                <time className="col-start-3 row-start-1 text-muted md:col-start-4" dateTime={event.created_at}>
                  {new Date(event.created_at).toLocaleTimeString()}
                </time>
                <Button className="button-quiet col-start-4 row-start-1 px-1.5 py-1 text-xs md:col-start-5" aria-label={`事件 #${event.sequence} JSON`}
                  onClick={() => setSelected({ title: `事件 #${event.sequence} · ${event.kind}`, event })}>JSON</Button>
              </li>
            ))}
          </ol> : <p className="mb-0 text-xs text-muted">
            {events.length === 0 ? '此页暂无事件。' : hideDelta && events.every(isDelta) ? <>
              此页只有流式碎片。 <button type="button" className="text-brand underline"
                onClick={() => setHideDelta(false)}>显示流式碎片</button>
            </> : '此页没有匹配的事件。'}
          </p>}
        </>}
      </section>
      {selected && <Dialog title={selected.title} onClose={() => setSelected(undefined)}>
        {'event' in selected ? <pre className={modalPre}>{JSON.stringify(selected.event.data, null, 2)}</pre> : <>
          <ErrorBox message={selected.model.error ?? undefined} />
          <h3 className="mt-0 text-sm font-semibold">实际输入</h3>
          <pre className={modalPre}>{JSON.stringify(selected.model.request, null, 2)}</pre>
          <h3 className="mt-4 text-sm font-semibold">实际输出</h3>
          <pre className={modalPre}>{JSON.stringify(selected.model.response, null, 2)}</pre>
        </>}
      </Dialog>}
    </section>
  );
}
