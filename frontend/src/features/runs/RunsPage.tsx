import { Button } from '../../shared/ui/Button';
import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { RotateCcw } from 'lucide-react';
import { messageOf, send, terminal, useResource } from '../../shared/api/api';
import { RunPauseControl } from './RunPauseControl';
import { Badge, ErrorBox, Loading, Markdown, PageTitle } from '../../shared/ui/components';
import { BackLink } from '../../shared/ui/Workbench';
import type { Budgets, Run } from '../../shared/types/types';
import { ExecutionApprovals } from '../permissions/ExecutionApprovals';
import { RunQuestions } from './RunQuestions';
import { RunChildren } from './RunChildren';
import { TaskActivity } from './TaskActivity';
import { useRunDetails } from './useRunDetails';
import { RunOutcome } from './RunOutcome';
import { DeleteRunButton, RemovedRun } from './DeleteRun';
import { runKindLabels } from './runPresentation';


export { ActivityRunsPage as RunsPage } from '../activity/ActivityRunsPage';

function RunResult({ answer }: { answer: string }) {
  const body = useRef<HTMLDivElement>(null);
  const [overflowing, setOverflowing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    // 观察正文自身的自然高度，展开/收起只约束外层阅读区。
    const content = body.current?.firstElementChild;
    if (!content) return;
    const measure = () => setOverflowing(content.scrollHeight > 300);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    return () => observer.disconnect();
  }, [answer]);
  return <section className="mb-5 min-w-0 rounded-xl border border-line bg-white p-5 max-md:p-4">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 className="m-0 text-base font-semibold">本次结果</h2>
      {overflowing && <Button className="button-quiet text-xs" aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}>
        {expanded ? '收起为定高滚动' : '展开全高'}
      </Button>}
    </div>
    <div ref={body} className={`min-w-0 [&_pre]:overflow-x-auto ${overflowing
      ? `rounded-lg border border-line bg-[#fafbf9] p-4 ${expanded ? '' : 'scrollbar-subtle max-h-[300px] overflow-y-auto overscroll-contain'}`
      : ''}`} tabIndex={overflowing && !expanded ? 0 : undefined}>
      <Markdown text={answer} />
    </div>
  </section>;
}

function ResumeControls({ run, onChanged }: { run: Run; onChanged: () => void }) {
  const [limits, setLimits] = useState<Budgets>(run.budgets);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const labels: Record<keyof Budgets, string> = {
    model_requests: '模型请求总上限',
    tool_calls: '工具尝试总上限',
    searches: '搜索总上限',
    active_seconds: '活跃时间总上限（秒）',
  };
  if (run.kind === 'memory_classification' && !['paused', 'interrupted'].includes(run.status))
    return (
      <p>
        原文已保留。<Link to="/preferences">返回长期记忆选择类别或重新分类</Link>。
      </p>
    );
  return (
    <section className="card mb-5.5 rounded-[11px] border border-line bg-white p-6.5 max-md:p-4.5">
      <h2>从已保存的进展继续</h2>
      <p className="muted text-muted">
        {run.kind === 'material'
          ? '继续下载或解析材料，此操作不调用大模型。若材料为 PDF，已完成的页面会保留。'
          : run.kind === 'memory_classification'
            ? '继续原分类任务，已收到的模型响应会复用，不会再次发送同一请求。'
            : '继续使用同一次运行和检查点，已消耗的次数不会清零。已提交的知识变更会保留。'}
      </p>
      <ErrorBox message={error} />
      {run.kind !== 'material' && run.kind !== 'memory_classification' && (
        <div className="budget-inputs grid grid-cols-4 gap-3 max-md:grid-cols-2 [&_label]:text-[11px]">
          {(Object.keys(labels) as (keyof Budgets)[]).map((key) => (
            <label key={key}>
              {labels[key]}
              <input
                type="number"
                min={key === 'searches' ? 0 : 1}
                value={limits[key]}
                onChange={(e) => setLimits({ ...limits, [key]: Number(e.target.value) })}
              />
            </label>
          ))}
        </div>
      )}
      <Button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError('');
          try {
            await send(`/api/runs/${run.id}/resume`, { budgets: limits });
            onChanged();
          } catch (error) {
            setError(messageOf(error));
          } finally {
            setBusy(false);
          }
        }}
      >
        <RotateCcw size={16} />
        确认恢复任务
      </Button>
    </section>
  );
}

export function RunPage() {
  const { runId } = useParams();
  const { result, refresh, key, detailsKey } = useRunDetails(runId);
  const [error, setError] = useState('');
  if (result.loading && result.data?.id !== runId) return <Loading />;
  if (!result.data) return <ErrorBox message={result.error} />;
  const run = result.data;
  if (run.user_deleted_at) return <RemovedRun run={run} />;
  if (run.kind === 'research_start')
    return (
      <>
        <BackLink to="/runs">返回运行记录</BackLink>
        <PageTitle title="研发任务登记">
          {run.outcome.research_task_id && <Link className="button button-secondary" to={`/research/${run.outcome.research_task_id}`}>打开研发工作台</Link>}
        </PageTitle>
        <section className="rounded-xl border border-line bg-white p-5">
          <p className="m-0 whitespace-pre-wrap [overflow-wrap:anywhere]">{run.task}</p>
          {run.conversation_id && <Link className="mt-3 inline-block" to={`/chat/${run.conversation_id}`}>返回会话</Link>}
        </section>
      </>
    );
  if (run.details_available === false)
    return (
      <>
        <BackLink to="/runs">返回运行记录</BackLink>
        <PageTitle title="运行结果摘要">
          <DeleteRunButton run={run} leave />
        </PageTitle>
        <section className="card mb-5.5 rounded-[11px] border border-line bg-white p-6.5 max-md:p-4.5">
          <h2>{run.task}</h2>
          <Badge status={run.status} />
          <p className="notice border-l-[3px] border-[#c0a76b] bg-[#f5f0e0] px-4 py-3 text-[13px] [overflow-wrap:anywhere]">
            完整执行详情已过期或正在清理，无法原地恢复或导出完整轨迹。下方保留简短结果，聊天和独立文档仍可继续使用。
          </p>
          {run.details_expires_at && (
            <p className="muted text-muted">
              详情保留至：{new Date(run.details_expires_at).toLocaleString()}
            </p>
          )}
          {run.outcome.answer && <Markdown text={run.outcome.answer} />}
          {run.conversation_id && <Link to={`/chat/${run.conversation_id}`}>回到对话 →</Link>}
        </section>
        <TaskActivity key={`activity:${run.id}`} run={run} refreshKey={detailsKey} />
      </>
    );
  return (
    <>
      <BackLink to="/runs">返回运行记录</BackLink>
      <PageTitle title="任务详情">
        <div className="flex flex-wrap items-center gap-2">
          <DeleteRunButton run={run} leave />
          <Button className="secondary button-secondary" onClick={refresh}>
            刷新
          </Button>
          <Link className="button secondary button-secondary" to={`/runs/${run.id}/diagnostics`}>
            诊断
          </Link>
        </div>
      </PageTitle>
      <ErrorBox message={error || result.error} />
      {run.status === 'queued' && <RunBlocking runId={run.id} />}
      <section className="mb-5 min-w-0 rounded-xl border border-line bg-white p-5 max-md:p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge status={run.status} />
            <RunOutcome run={run} refreshKey={detailsKey} />
            <span className="rounded bg-canvas px-2 py-1 text-control">{runKindLabels[run.kind]}</span>
            <time dateTime={run.created_at} className="text-muted">{new Date(run.created_at).toLocaleString()}</time>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {run.conversation_id && <Link to={`/chat/${run.conversation_id}`}>回到对话 →</Link>}
            {run.outcome.source_id && (
              <Link to={`/sources/${run.outcome.source_id}`}>查看来源材料 →</Link>
            )}
          </div>
        </div>
        <h2 className="my-4 text-[17px] font-semibold whitespace-pre-wrap [overflow-wrap:anywhere]">{run.task}</h2>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 [&>div]:min-w-0 [&>div]:rounded-lg [&>div]:bg-canvas [&>div]:px-3 [&>div]:py-2 [&_strong]:block [&_strong]:text-xl [&_strong]:font-semibold [&_strong]:text-brand [&_small]:text-xs [&_small]:font-normal [&_small]:text-control [&_span]:text-xs [&_span]:text-muted">
            <div>
              <strong>
                {run.model_usage?.root_used ?? '—'}
                {run.kind !== 'material' && <small> / {run.budgets.model_requests}</small>}
              </strong>
              <span>模型请求</span>
            </div>
            <div>
              <strong>
                {run.tool_calls}
                <small> / {run.budgets.tool_calls}</small>
              </strong>
              <span>工具尝试</span>
            </div>
            <div>
              <strong>
                {run.searches}
                <small> / {run.budgets.searches}</small>
              </strong>
              <span>搜索调用</span>
            </div>
            <div>
              <strong>
                {(run.active_ms / 1000).toFixed(1)}
                <small> 秒</small>
              </strong>
              <span>活跃执行时间</span>
            </div>
        </div>
        {run.kind === 'material' && <p className="mt-2 mb-0 text-xs text-muted">材料下载与解析不调用大模型。</p>}
        {run.stop_reason && (
          <p className="notice border-l-[3px] border-[#c0a76b] bg-[#f5f0e0] px-4 py-3 text-[13px] [overflow-wrap:anywhere]">
            停止原因：{run.stop_reason}
          </p>
        )}
        <RunPauseControl run={run} onChanged={refresh} />
        {!terminal(run.status) && (
          <Button
            className="secondary button-secondary"
            onClick={async () => {
              try {
                await send(`/api/runs/${run.id}/cancel`);
                refresh();
              } catch (error) {
                setError(messageOf(error));
              }
            }}
          >
            停止本次任务
          </Button>
        )}
      </section>
      <ExecutionApprovals run={run} refreshKey={key} onChanged={refresh} />
      <RunQuestions run={run} refreshKey={key} onChanged={refresh} />
      <RunChildren run={run} refreshKey={key} onChanged={refresh} />
      {terminal(run.status) &&
        run.status !== 'completed' &&
        !run.parent_id &&
        !['waiting_approval', 'execution_unknown'].includes(run.outcome.kind ?? '') && (
          <ResumeControls key={`${run.id}:${run.generation}`} run={run} onChanged={refresh} />
        )}
      {run.outcome.answer && <RunResult key={run.id} answer={run.outcome.answer} />}
      <TaskActivity key={`activity:${run.id}`} run={run} refreshKey={detailsKey} />
    </>
  );
}

export function RunArtifactPage() {
  const { runId, artifactId } = useParams();
  const [start, setStart] = useState(0);
  const result = useResource<{
    source_url?: string;
    text: string;
    kind: string;
    start: number;
    end: number;
    total_length: number;
    next_start: number | null;
  }>(`/api/runs/${runId}/artifacts/${artifactId}?start=${start}`);
  if (result.data?.source_url) return <Navigate to={result.data.source_url} replace />;
  return (
    <>
      <BackLink to={`/runs/${runId}`}>返回运行详情</BackLink>
      <PageTitle title="运行材料原文" />
      <ErrorBox message={result.error} />
      {result.loading ? (
        <Loading />
      ) : (
        result.data && (
          <section className="card mb-5.5 rounded-[11px] border border-line bg-white p-6.5 max-md:p-4.5">
            <p>
              {result.data.kind} · 字符 {result.data.start}–{result.data.end} /{' '}
              {result.data.total_length}
            </p>
            <pre className="source-text">{result.data.text}</pre>
            <div className="row flex items-center gap-3">
              <Button
                className="secondary button-secondary"
                disabled={start === 0}
                onClick={() => setStart(Math.max(0, start - 6000))}
              >
                上一段
              </Button>
              <Button
                disabled={result.data.next_start === null}
                onClick={() => setStart(result.data?.next_start ?? start)}
              >
                下一段
              </Button>
            </div>
          </section>
        )
      )}
    </>
  );
}

type RunBlockingState = {
  id: string;
  status: string;
  claimable: boolean;
  reason: string;
  detail: string;
  action?: string;
  href: string;
};

/** 排队原因来自服务端领取规则；这里只解释原因并链接原处理入口，不重试。 */
function RunBlocking({ runId }: { runId: string }) {
  const { data, error } = useResource<RunBlockingState>(`/api/readiness/runs/${runId}`);
  if (error || !data || data.claimable) return null;
  return (
    <p className="notice border-l-[3px] border-[#c0a76b] bg-[#f5f0e0] px-4 py-3 text-[13px] [overflow-wrap:anywhere]" role="status">
      还在排队：{data.detail}
      {data.action ? ` · ${data.action}` : ''} <Link to={data.href}>前往处理入口 →</Link>
    </p>
  );
}
