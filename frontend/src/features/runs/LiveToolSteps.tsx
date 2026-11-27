import { useEffect, useState } from 'react';
import { AlertCircle, Check, ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { BrowserScreenshotButton } from './BrowserScreenshotButton';
import { formatDuration } from './runPresentation';

export type TimelineStep = {
  id: string;
  name?: string;
  label: string;
  status: 'running' | 'done' | 'error';
  startedAt?: number;
  duration?: number;
  summary?: string;
  input?: string;
  output?: string;
  detail?: string;
  browserMode?: 'launch' | 'attach';
  screenshotId?: string;
};

function StepRow({ step, elapsed, runId }: { step: TimelineStep; elapsed?: number; runId: string }) {
  const [expanded, setExpanded] = useState(false);
  const hasPreview = Boolean(step.input || step.output);
  const command = step.name === 'execute';
  const error = step.status === 'error';
  const status = step.status === 'running' ? '执行中' : error ? '出错' : '已返回';
  const icon = step.status === 'running'
    ? <Loader2 size={13} className="animate-spin motion-reduce:animate-none" />
    : error ? <AlertCircle size={13} /> : <Check size={13} className="text-brand" />;
  const row = <>
    <span className="shrink-0" aria-label={status} role="img">{icon}</span>
    <span className={`min-w-0 font-medium [overflow-wrap:anywhere] ${step.summary ? 'max-w-[55%]' : ''}`}
      title={step.name}>{step.label}</span>
    {step.summary && <span className="min-w-0 truncate rounded bg-sidebar px-1.5 py-0.5 font-mono text-control"
      title={step.summary}>{step.summary}</span>}
    {elapsed !== undefined && <span className="ml-auto shrink-0 tabular-nums text-muted">{formatDuration(elapsed)}</span>}
    {hasPreview && (expanded ? <ChevronDown size={13} className="shrink-0" aria-hidden="true" /> :
      <ChevronRight size={13} className="shrink-0" aria-hidden="true" />)}
  </>;
  const rowClass = `flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 py-1 text-left ${error ? 'text-danger' : 'text-control'}`;
  return <li className="min-w-0">
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      {hasPreview ? <button type="button" className={rowClass + ' hover:bg-sidebar focus-visible:outline-brand'}
        aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{row}</button>
        : <div className={rowClass}>{row}</div>}
      {step.browserMode && <span className="rounded-full bg-sidebar px-2 py-0.5 text-brand">
        {step.browserMode === 'attach' ? '本机可见 · CDP' : '容器隔离'}
      </span>}
      {step.screenshotId && <BrowserScreenshotButton runId={runId} artifactId={step.screenshotId} />}
    </div>
    {(step.detail || (error && step.output && !expanded)) &&
      <p className="my-1 break-all px-2 text-danger">{step.detail || step.output?.slice(0, 180)}</p>}
    {expanded && hasPreview && (
      <pre tabIndex={0} aria-label={command ? '终端输出预览' : '工具输出预览'}
        className={`my-1 min-w-0 overflow-y-auto overscroll-contain rounded-lg p-2.5 font-mono text-xs leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere] ${command
          ? 'max-h-[180px] bg-[#18251f] text-[#e0eadc]'
          : 'max-h-[160px] bg-canvas text-control'}`}>
        {step.input && (command ? '$ ' : '参数\n') + step.input}
        {step.input && step.output && '\n\n'}
        {step.output && <span className={error ? (command ? 'text-[#ffb8af]' : 'text-danger') : undefined}>
          {!command && '返回\n'}{step.output}
        </span>}
      </pre>
    )}
  </li>;
}

export function LiveToolSteps({ steps, runId }: { steps: TimelineStep[]; runId: string }) {
  const [expanded, setExpanded] = useState(true);
  const [now, setNow] = useState(Date.now);
  const running = steps.some((step) => step.status === 'running');
  useEffect(() => {
    if (!running || !expanded) return;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [running, expanded]);
  const observed = steps.filter((step) => step.duration !== undefined);
  const total = observed.reduce((value, step) => value + (step.duration ?? 0), 0);
  return <div className="mt-2">
    <button type="button" className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-control hover:bg-sidebar"
      aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
      {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
      {running ? `正在执行步骤 (${steps.length})` : `已执行 ${steps.length} 个步骤`}
      {!running && observed.length > 0 && <span className="text-muted"> · {formatDuration(total)}</span>}
    </button>
    {expanded && <ol aria-label="执行步骤"
      className="scrollbar-subtle mt-1 mb-0 ml-2 grid max-h-[280px] list-none gap-1 overflow-y-auto overscroll-contain border-l-2 border-line pl-2 pr-1 text-xs">
      {steps.map((step) => <StepRow key={step.id} step={step} runId={runId}
        elapsed={step.duration ?? (step.status === 'running' && step.startedAt !== undefined
          ? Math.max(0, now - step.startedAt) : undefined)} />)}
    </ol>}
  </div>;
}
