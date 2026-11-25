import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, Check, ChevronDown, ChevronRight, AlertCircle, Loader2, Square } from 'lucide-react';
import { api, messageOf, send, terminal } from '../../shared/api/api';
import { Badge, ErrorBox } from '../../shared/ui/components';
import { Button } from '../../shared/ui/Button';
import type { Guidance, Run, RunEvent } from '../../shared/types/types';
import { BrowserScreenshotButton } from './BrowserScreenshotButton';
import { RunPauseControl } from './RunPauseControl';

const TOOL_LABELS: Record<string, string> = {
  list_materials: '查看材料列表',
  inspect_material: '查看材料详情',
  read_source: '阅读材料正文',
  list_evidence: '查看已读证据',
  search_references: '检索参考资料',
  search_knowledge: '检索知识库',
  list_knowledge: '浏览知识库目录',
  knowledge_history: '查看知识版本历史',
  read_knowledge: '阅读知识文章',
  prepare_changes: '起草知识变更',
  publish_changes: '发布知识变更',
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
  execute: '执行工作区命令',
  task: '委派子任务',
  start_async_task: '启动异步子任务',
  wait_async_tasks: '等待子任务结果',
};

type TimelineStep = {
  id: string;
  label: string;
  status: 'running' | 'done' | 'error';
  detail?: string;
  browserMode?: 'launch' | 'attach';
  screenshotId?: string;
};

function toolLabel(name: unknown): string {
  const raw = String(name ?? '').trim();
  return TOOL_LABELS[raw] ?? (raw || '执行工具');
}

/** 一个任务的实时流式观察面板，普通任务与自主研发共用；组件卸载只关闭 SSE，不取消后台运行。 */
export function LiveRunPanel({
  initial,
  onChanged,
  onOpenChat,
}: {
  initial: Run;
  onChanged: () => void;
  onOpenChat?: () => void;
}) {
  const [run, setRun] = useState(initial);
  const [activity, setActivity] = useState(
    initial.status === 'waiting_children' ? '等待子任务结果' : '等待执行',
  );
  const [reasoning, setReasoning] = useState('');
  const [showReasoning, setShowReasoning] = useState(true);
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [partial, setPartial] = useState('');
  const [connection, setConnection] = useState('');
  const [error, setError] = useState('');
  const [guidance, setGuidance] = useState<Guidance[]>([]);
  const seenSequences = useRef<Set<number>>(new Set());

  useEffect(() => {
    seenSequences.current = new Set();
    setReasoning('');
    setSteps([]);
    setPartial('');
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
        setPartial((value) => value + String(event.data.text ?? ''));
      }
      if (event.kind === 'tool_started') {
        const label = toolLabel(event.data.name);
        const stepId = String(event.data.tool_call_id ?? `seq-${event.sequence}`);
        setActivity(`正在执行 · ${label}`);
        setSteps((prev) => {
          if (prev.some((item) => item.id === stepId)) return prev;
          return [...prev, { id: stepId, label, status: 'running' }];
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
          ? previous.map((item) => item.id === stepId ? step : item)
          : [...previous, step]);
      }
      if (event.kind === 'tool_finished') {
        const label = toolLabel(event.data.name);
        const stepId = String(event.data.tool_call_id ?? '');
        const failed = event.data.status === 'error';
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
            return [...prev, { id: stepId || `seq-${event.sequence}`, label, status: failed ? 'error' : 'done' }];
          }
          const next = [...prev];
          if (next[index].browserMode && next[index].status !== 'running') return prev;
          next[index] = { ...next[index], status: failed ? 'error' : 'done' };
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
      className="live-run card mb-5.5 rounded-[11px] border border-[#c9dac5] bg-white px-5 py-4 text-[13px] max-md:p-4.5 [&>p]:mt-2.5 [&>p]:mb-0"
      aria-label="实时执行进度"
    >
      <div className="row flex flex-wrap items-center justify-between gap-2">
        <span className="row flex items-center gap-2.5">
          <Activity size={17} />
          <strong>{activity}</strong>
          <Badge status={run.status} />
        </span>
        <div className="flex flex-wrap items-center gap-1.5">
          {onOpenChat && (
            <Button type="button" className="button-secondary small text-xs" onClick={onOpenChat}>
              切回会话介入
            </Button>
          )}
          <RunPauseControl run={run} onChanged={onChanged} />
          <Button
            className="quiet button-quiet small text-xs"
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

      <p className="muted text-muted small text-xs">
        {run.tool_calls} 次工具尝试 · <Link to={`/runs/${run.id}`}>查看运行详情</Link>
      </p>

      {reasoning && (
        <div className="mt-3 rounded-lg border border-line bg-sidebar/60 px-3 py-2">
          <button
            type="button"
            className="flex w-full items-center gap-1.5 text-left text-xs font-medium text-control"
            aria-expanded={showReasoning}
            onClick={() => setShowReasoning((value) => !value)}
          >
            {showReasoning ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
            <span>思考过程</span>
          </button>
          {showReasoning && (
            <div
              className="reasoning-stream mt-2 max-h-[180px] overflow-auto text-xs leading-relaxed whitespace-pre-wrap text-control"
              data-testid="reasoning-stream"
            >
              {reasoning}
            </div>
          )}
        </div>
      )}

      {steps.length > 0 && (
        <ol
          className="mt-3 m-0 grid max-h-[160px] list-none gap-1.5 overflow-auto p-0 text-xs"
          aria-label="执行步骤"
        >
          {steps.map((step) => (
            <li key={step.id} className="flex min-w-0 flex-wrap items-center gap-2 text-control">
              {step.status === 'running' && <Loader2 size={13} className="shrink-0 animate-spin" aria-hidden="true" />}
              {step.status === 'done' && <Check size={13} className="shrink-0 text-brand" aria-hidden="true" />}
              {step.status === 'error' && <AlertCircle size={13} className="shrink-0 text-danger" aria-hidden="true" />}
              <span className={step.status === 'error' ? 'text-danger' : undefined}>{step.label}</span>
              {step.browserMode && <span className="rounded-full bg-sidebar px-2 py-0.5 text-brand">
                {step.browserMode === 'attach' ? '本机可见 · CDP' : '容器隔离'}
              </span>}
              {step.screenshotId && <BrowserScreenshotButton runId={run.id} artifactId={step.screenshotId} />}
              {step.detail && <span className="basis-full text-danger">{step.detail}</span>}
            </li>
          ))}
        </ol>
      )}

      {partial && (
        <div className="stream-preview mt-3.5 max-h-[180px] overflow-auto leading-[1.8] whitespace-pre-wrap text-[#6c7a69]">
          {partial}
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
