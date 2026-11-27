import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, ChevronDown, ChevronRight, Square } from 'lucide-react';
import { api, messageOf, send, terminal } from '../../shared/api/api';
import { Badge, ErrorBox, Markdown } from '../../shared/ui/components';
import { Button } from '../../shared/ui/Button';
import type { Guidance, Run, RunEvent } from '../../shared/types/types';
import { LiveToolSteps, type TimelineStep } from './LiveToolSteps';
import { RunPauseControl } from './RunPauseControl';
import { formatDuration, runKindLabels } from './runPresentation';

const TOOL_LABELS: Record<string, string> = {
  list_materials: '查看材料列表',
  inspect_material: '查看材料详情',
  read_source: '阅读材料正文',
  list_evidence: '查看已读证据',
  search_references: '检索参考资料',
  search_knowledge: '检索知识库',
  history_search: '检索对话历史',
  memory_search: '检索长期记忆',
  list_knowledge: '浏览知识库目录',
  knowledge_history: '查看知识版本历史',
  read_knowledge: '阅读知识文章',
  prepare_changes: '起草知识变更',
  publish_changes: '发布知识变更',
  read_changeset: '读取知识变更',
  revise_changeset: '修订知识变更',
  reject_changeset: '驳回知识变更',
  read_research_map: '查看研究地图',
  update_research_map: '更新研究地图',
  set_research_focus: '调整研究焦点',
  continue_research: '安排下一轮推进',
  submit_research_delivery: '提交研发成果',
  propose_research_experiment: '提出验证实验',
  read_research_experiment: '读取实验结果',
  search_web: '网页搜索',
  read_web: '读取网页',
  browser_navigate: '导航到网页',
  browser_observe: '观察页面',
  browser_click: '点击取证控件',
  browser_wait: '等待页面内容',
  browser_back: '返回上一个页面',
  browser_capture: '采集网页证据',
  browser_close: '关闭浏览器会话',
  read_file: '读取工作区文件',
  write_file: '写入工作区文件',
  edit_file: '编辑工作区文件',
  import_file: '导入工作区文件',
  present_files: '交付文件',
  execute: '执行工作区命令',
  task: '委派子任务',
  start_async_task: '启动异步子任务',
  wait_async_tasks: '等待子任务结果',
};

function toolLabel(name: unknown): string {
  const raw = String(name ?? '').trim();
  return TOOL_LABELS[raw] ?? (raw || '执行工具');
}

const credentialKey = /^(?:authorization|api[_-]?key|password|secret|(?:access[_-]?)?token)$/i;

function preview(value: unknown, limit = 1500, singleLine = false): string | undefined {
  if (value == null) return undefined;
  // SSE 已在后端脱敏，这里再屏蔽预览中可识别的凭据格式与链接查询参数。
  const formatted = typeof value === 'string' ? value
    : JSON.stringify(value, (key, item: unknown) => credentialKey.test(key) ? '[REDACTED]' : item, 2);
  const safe = formatted
    .replace(/\b(?:Bearer|Basic)\s+[^\s"';,]+/gi, '[REDACTED]')
    .replace(/(https?:\/\/)[^\s/"']+@/gi, '$1')
    .replace(/(https?:\/\/[^\s"'?#]+)[?#][^\s"']*/gi, '$1')
    .replace(/\b(?:[\w-]*[_-])?(?:api[_-]?key|password|secret|(?:access[_-]?)?token)["']?\s*[=:]\s*(?:"[^"]*"|'[^']*'|[^\s;&|,]+)/gi, '[REDACTED]')
    .replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)/g, '[REDACTED]');
  const text = singleLine ? safe.replace(/\s+/g, ' ').trim() : safe;
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text || undefined;
}

function eventTime(event: RunEvent): number | undefined {
  const timestamp = Date.parse(event.created_at);
  return Number.isFinite(timestamp) ? timestamp : undefined;
}

/** 历史事件没有投影字段时，仅预览既有 SSE 中的白名单键。 */
function toolSummary(data: RunEvent['data'], args: Record<string, unknown> | undefined): string | undefined {
  if (typeof data.summary === 'string' && data.summary.trim()) return preview(data.summary, 120, true);
  if (!args) return undefined;
  let target: unknown;
  switch (data.name) {
    case 'search_references': case 'search_knowledge': case 'search_web': case 'history_search': case 'memory_search':
      target = args.query;
      break;
    case 'read_file': case 'write_file': case 'edit_file': case 'import_file': case 'present_files':
      target = args.path ?? args.file_path ?? (Array.isArray(args.paths) ? args.paths[0] : undefined);
      break;
    case 'execute':
      target = args.command;
      break;
    case 'read_source': case 'inspect_material':
      target = args.source_id ?? args.segment_id;
      break;
    case 'read_knowledge': case 'knowledge_history':
      target = args.document_id ?? args.title ?? args.version;
      break;
    case 'prepare_changes': case 'publish_changes': case 'read_changeset': case 'revise_changeset': case 'reject_changeset':
      target = args.changeset_id ?? (Array.isArray(args.operations) ? `${args.operations.length} 项操作` : undefined);
      break;
    case 'read_research_map': case 'update_research_map': case 'set_research_focus': case 'submit_research_delivery':
    case 'propose_research_experiment':
      target = args.focus ?? args.node_id ?? args.title ?? args.summary;
      break;
    case 'task': case 'start_async_task':
      target = args.subagent_type ?? args.role ?? args.description ?? args.task;
      break;
    case 'read_web': {
      if (typeof args.url !== 'string') return undefined;
      try {
        const url = new URL(args.url);
        if (url.protocol !== 'https:' && url.protocol !== 'http:') return undefined;
        target = url.origin + url.pathname;
      } catch { return undefined; }
      break;
    }
  }
  return typeof target === 'string' || typeof target === 'number'
    ? preview(target, data.name === 'execute' ? 100 : 120, true) : undefined;
}

/** 一个任务的实时流式观察面板，普通任务与自主研发共用；组件卸载只关闭 SSE，不取消后台运行。 */
export function LiveRunPanel({
  initial,
  onChanged,
  onOpenChat,
  onOpenEvidence,
  variant = 'card',
}: {
  initial: Run;
  onChanged: () => void;
  onOpenChat?: () => void;
  onOpenEvidence?: (id: string, label: string, trigger: HTMLAnchorElement) => void;
  variant?: 'inline' | 'card';
}) {
  const [run, setRun] = useState(initial);
  const [activity, setActivity] = useState(
    initial.status === 'waiting_children' ? '等待子任务结果' : '等待执行',
  );
  const [reasoning, setReasoning] = useState('');
  const [showReasoning, setShowReasoning] = useState(true);
  const [reasoningDuration, setReasoningDuration] = useState<number>();
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [partial, setPartial] = useState('');
  const [connection, setConnection] = useState('');
  const [error, setError] = useState('');
  const [guidance, setGuidance] = useState<Guidance[]>([]);
  const seenSequences = useRef<Set<number>>(new Set());
  const userToggledReasoning = useRef(false);
  const inline = variant === 'inline';

  useEffect(() => {
    seenSequences.current = new Set();
    setReasoning('');
    setShowReasoning(true);
    setReasoningDuration(undefined);
    setSteps([]);
    setPartial('');
    userToggledReasoning.current = false;
    let firstReasoningAt: number | undefined;
    let hasStreamedAnswer = false;
    const source = new EventSource(`/api/runs/${initial.id}/events`);
    source.addEventListener('progress', (message: MessageEvent) => {
      const event: RunEvent = JSON.parse(message.data);
      if (typeof event.sequence === 'number') {
        if (seenSequences.current.has(event.sequence)) return;
        seenSequences.current.add(event.sequence);
      }
      setConnection('');
      if (event.kind === 'model_started') {
        setActivity('正在阅读与决策');
        setPartial('');
      }
      if (event.kind === 'reasoning_delta') {
        const chunk = String(event.data.text ?? '');
        if (chunk) {
          firstReasoningAt ??= eventTime(event);
          setActivity('正在思考与规划');
          setReasoning((value) => value + chunk);
        }
      }
      if (event.kind === 'model_finished') {
        setSteps((prev) =>
          prev.some((item) => item.id.startsWith('protocol-') && item.status === 'running')
            ? prev.map((item) =>
                item.id.startsWith('protocol-') && item.status === 'running'
                  ? { ...item, label: '模型调用格式异常，已自动纠偏重试', status: 'done' }
                  : item,
              )
            : prev,
        );
      }
      if (event.kind === 'model_delta') {
        const chunk = String(event.data.text ?? '');
        if (chunk) {
          if (!hasStreamedAnswer && chunk.trim()) {
            hasStreamedAnswer = true;
            const time = eventTime(event);
            if (time !== undefined && firstReasoningAt !== undefined) {
              setReasoningDuration(Math.max(0, time - firstReasoningAt));
            }
            if (!userToggledReasoning.current) setShowReasoning(false);
          }
          if (hasStreamedAnswer) setActivity('正在输出回答');
          setPartial((value) => value + chunk);
        }
      }
      if (event.kind === 'tool_started') {
        const name = String(event.data.name ?? '');
        const label = toolLabel(name);
        const stepId = String(event.data.tool_call_id ?? `seq-${event.sequence}`);
        const rawArgs = event.data.args;
        const args = rawArgs && typeof rawArgs === 'object' && !Array.isArray(rawArgs)
          ? rawArgs as Record<string, unknown> : undefined;
        const input = preview(name === 'execute' ? args?.command ?? args?.preview : args?.preview ??
          (args && Object.keys(args).length > 0 ? args : typeof rawArgs === 'string' ? rawArgs : undefined));
        setActivity(`正在执行 · ${label}`);
        setSteps((prev) => {
          if (prev.some((item) => item.id === stepId)) return prev;
          return [...prev, { id: stepId, name, label, status: 'running',
            startedAt: eventTime(event), input, summary: toolSummary(event.data, args) }];
        });
      }
      if (event.kind === 'browser_action' || event.kind === 'browser_capture') {
        const stepId = String(event.data.tool_call_id ?? `seq-${event.sequence}`);
        const screenshot = event.data.screenshot as { artifact_id?: unknown } | undefined;
        const step: TimelineStep = {
          id: stepId,
          label: toolLabel(`browser_${String(event.data.action ?? 'capture')}`),
          status: event.data.status === 'started' ? 'running' : event.data.status === 'failed' ? 'error' : 'done',
          browserMode: event.data.mode === 'attach' ? 'attach' : 'launch',
          detail: typeof event.data.message === 'string' ? event.data.message : undefined,
          screenshotId: typeof screenshot?.artifact_id === 'string' ? screenshot.artifact_id : undefined,
        };
        setSteps((previous) => previous.some((item) => item.id === stepId)
          ? previous.map((item) => item.id === stepId ? { ...item, ...step, screenshotId: step.screenshotId ?? item.screenshotId } : item)
          : [...previous, step]);
      }
      if (event.kind === 'tool_finished' || event.kind === 'tool_replayed') {
        const name = String(event.data.name ?? '');
        const label = toolLabel(name);
        const stepId = String(event.data.tool_call_id ?? '');
        const failed = event.data.status === 'error';
        const output = preview(event.data.preview ?? event.data.content);
        const elapsed = event.data.duration_ms;
        const duration = typeof elapsed === 'number' && Number.isFinite(elapsed) && elapsed >= 0 ? elapsed : undefined;
        const finishedAt = eventTime(event);
        const summary = toolSummary(event.data, undefined);
        setSteps((prev) => {
          let index = -1;
          if (stepId) {
            index = prev.findIndex((item) => item.id === stepId);
          } else {
            for (let i = prev.length - 1; i >= 0; i -= 1) {
              if (prev[i].label === label && prev[i].status === 'running') {
                index = i;
                break;
              }
            }
          }
          if (index === -1) {
            return [...prev, { id: stepId || `seq-${event.sequence}`, name, label,
              status: failed ? 'error' : 'done', output, duration, summary }];
          }
          const next = [...prev];
          const step = next[index];
          next[index] = {
            ...step,
            status: failed || step.status === 'error' ? 'error' : 'done',
            output: output ?? step.output,
            summary: summary ?? step.summary,
            duration: duration ?? step.duration ?? (step.startedAt !== undefined && finishedAt !== undefined
              ? Math.max(0, finishedAt - step.startedAt) : undefined),
          };
          return next;
        });
      }
      if (event.kind === 'model_protocol_error') {
        const retrying = Boolean(event.data.retrying);
        const label = retrying ? '模型调用格式异常，正在自动纠偏重试' : '模型调用格式异常，已停止';
        setActivity(label);
        setSteps((prev) => [
          ...prev,
          { id: `protocol-${event.sequence}`, label, status: retrying ? 'running' : 'error' },
        ]);
      }
      if (event.kind === 'guidance_pending') setActivity('已收到引导，等待安全点处理');
      if (event.kind === 'guidance_applied') {
        setActivity('已应用新指令');
        setPartial('');
      }
      if (event.kind === 'pause_requested') setActivity('正在收尾暂停，保留当前响应');
      if (event.kind === 'waiting_children') setActivity('等待子任务结果');
      if (event.kind === 'children_ready') setActivity('子结果已就绪，准备继续');
      if (event.kind === 'guidance_ready') setActivity('已收到新要求，准备继续');
      if (
        [
          'finished',
          'paused',
          'waiting_input',
          'question_pending',
          'waiting_children',
          'child_started',
          'child_finished',
          'child_updated',
          'children_ready',
          'guidance_ready',
        ].includes(event.kind)
      )
        onChanged();
    });
    source.addEventListener('closed', () => {
      source.close();
      onChanged();
    });
    source.onerror = () => setConnection('连接暂时断开，正在重连；后台任务仍可继续。');
    const timer = window.setInterval(() => {
      Promise.all([
        api<Run>(`/api/runs/${initial.id}`),
        api<Guidance[]>(`/api/runs/${initial.id}/guidance`),
      ])
        .then(([value, deliveries]) => {
          setRun(value);
          setGuidance(deliveries);
          if (terminal(value.status) || ['paused', 'waiting_input'].includes(value.status)) {
            source.close();
            onChanged();
          }
        })
        .catch((failure: Error) => setError(failure.message));
    }, 2000);
    return () => {
      source.close();
      window.clearInterval(timer);
    };
  }, [initial.id, onChanged]);

  return (
    <div
      className={`live-run mb-5.5 min-w-0 ${inline
        ? 'message assistant py-3 [&_.markdown]:text-sm'
        : 'card rounded-[11px] border border-[#c9dac5] bg-white px-5 py-4 text-[13px] max-md:p-4.5'}`}
      aria-label="实时执行进度"
    >
      <div className="row flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
          {inline ? <strong className="text-[#45644a]">研发助手</strong> : <>
            <Activity size={16} aria-hidden="true" />
            <strong>{run.input?.research ? '研发自主推进' : runKindLabels[run.kind]}</strong>
            <Badge status={run.status} />
          </>}
          <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full bg-[#e7f1e8] px-2 py-0.5 text-[11px] text-brand">
            <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-brand motion-safe:animate-pulse" />
            <span className="[overflow-wrap:anywhere]">{activity}</span>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Link to={`/runs/${run.id}`} className="px-1 text-xs text-control hover:underline">
            {inline ? '运行详情 →' : '查看运行详情'}
          </Link>
          {onOpenChat && (
            <Button type="button" className="button-secondary small text-xs" onClick={onOpenChat}>
              切回会话介入
            </Button>
          )}
          <RunPauseControl run={run} onChanged={onChanged} />
          <Button
            className="quiet button-quiet small text-xs text-danger"
            disabled={run.cancel_requested}
            onClick={async () => {
              try {
                setRun(await send<Run>(`/api/runs/${run.id}/cancel`));
              } catch (failure) {
                setError(messageOf(failure));
              }
            }}
          >
            <Square size={12} />
            {run.cancel_requested ? '正在取消…' : '取消任务'}
          </Button>
        </div>
      </div>

      {reasoning && (
        <div className="mt-2">
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-left text-xs font-medium text-control hover:bg-sidebar"
            aria-expanded={showReasoning}
            onClick={() => {
              userToggledReasoning.current = true;
              setShowReasoning((value) => !value);
            }}
          >
            {showReasoning ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
            <span>思考过程</span>
            {inline && <span className="text-muted">
              {reasoningDuration === undefined ? '· 思考中…' : `· 已思考 ${formatDuration(reasoningDuration)}`}
            </span>}
          </button>
          {showReasoning && (
            <div
              className="reasoning-stream scrollbar-subtle mt-1 ml-2 max-h-[180px] overflow-y-auto overscroll-contain border-l-2 border-[#c9dac5] pl-3.5 text-xs leading-relaxed whitespace-pre-wrap text-control [overflow-wrap:anywhere]"
              data-testid="reasoning-stream"
            >
              {reasoning}
            </div>
          )}
        </div>
      )}

      {steps.length > 0 && <LiveToolSteps steps={steps} runId={run.id} />}

      {partial && (
        <div className="stream-preview mt-3.5 min-w-0 [&_pre]:overflow-x-auto">
          <Markdown text={partial} onOpenEvidence={onOpenEvidence} />
          {run.status === 'running' && <span aria-hidden="true"
            className="mt-1 inline-block h-1 w-4 rounded-full bg-brand/50 motion-safe:animate-pulse" />}
        </div>
      )}

      {connection && <p className="muted text-muted small text-xs">{connection}</p>}
      {guidance.map((item) => (
        <p key={item.id} className="muted text-muted small text-xs">
          引导消息 {item.user_sequence} ·{' '}
          {{ pending: '等待应用', applied: '已应用', stale: '未应用，目标已变化' }[item.status]}
          {item.result.reason || item.result.authorization_error}
        </p>
      ))}
      <ErrorBox message={error} />
    </div>
  );
}
