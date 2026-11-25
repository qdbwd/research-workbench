import { Button } from '../../shared/ui/Button';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { FileText, ArrowDown, FlaskConical, MessageSquare, X } from 'lucide-react';
import {
  messageOf,
  send,
  usePagedResource,
  useResource,
} from '../../shared/api/api';
import type { CollectionPage } from '../../shared/api/api';
import { Badge, Empty, ErrorBox, Loading, Markdown } from '../../shared/ui/components';
import type {
  ChangeSet,
  Run,
  Source,
} from '../../shared/types/types';
import { useWorkspace } from '../../shared/state/workspace';
import { ExecutionApprovals } from '../permissions/ExecutionApprovals';
import { ContextUsage } from './ContextUsage';
import { ComposerControls } from './ComposerControls';
import { useComposerDraft } from './useComposerDraft';
import { ComposerRecovery } from './ComposerRecovery';
import type { DraftData } from './drafts/store';
import { LiveRunPanel } from '../runs/LiveRunPanel';
import { RunPauseControl } from '../runs/RunPauseControl';
import { RunQuestions } from '../runs/RunQuestions';
import { RunChildren } from '../runs/RunChildren';
import { RunOutcome } from '../runs/RunOutcome';
import { useMessageHistory } from './useMessageHistory';
import { ChatEvidencePane, useNarrowChatPane } from './ChatEvidencePane';
import type { EvidenceTab } from './ChatEvidencePane';
import { ResearchControls } from '../research/ResearchControls';
import { ResearchChatPane } from '../research/ResearchChatPane';
import { ResearchTrialStarter, type TrialCatalog, type TrialPrompt } from './ResearchTrialStarter';
import { isNewerResearchTask, researchStatuses } from '../research/api';
import type { ResearchTask } from '../research/api';

type ConversationSettings = { permission_mode: string; reasoning_effort: string; revision: number };

export function ChatPage({ model, modelEnabled }: { model?: string; modelEnabled?: boolean } = {}) {
  const { conversationId } = useParams();
  const [evidenceView, setEvidenceView] = useState<{
    conversation: string | undefined; tabs: EvidenceTab[]; active: string | null; narrowVisible: boolean;
  }>({ conversation: conversationId, tabs: [], active: null, narrowVisible: false });
  const evidenceTriggers = useRef(new Map<string, HTMLAnchorElement>());
  const narrow = useNarrowChatPane();
  const currentEvidence = evidenceView.conversation === conversationId ? evidenceView : null;
  const shownEvidence = currentEvidence && (!narrow || currentEvidence.narrowVisible) ? currentEvidence.active : null;
  const openEvidence = (id: string, label: string, trigger: HTMLAnchorElement) => {
    evidenceTriggers.current.set(id, trigger);
    setEvidenceView((previous) => {
      const tabs = previous.conversation === conversationId ? previous.tabs : [];
      return { conversation: conversationId, tabs: tabs.some((tab) => tab.id === id) ? tabs : [...tabs, { id, label }],
        active: id, narrowVisible: true };
    });
  };
  const restoreCitationFocus = (id: string) => {
    const trigger = evidenceTriggers.current.get(id);
    requestAnimationFrame(() => trigger?.focus({ preventScroll: true }));
  };
  const closeEvidence = (id: string) => {
    if (!currentEvidence) return;
    const index = currentEvidence.tabs.findIndex((tab) => tab.id === id);
    const tabs = currentEvidence.tabs.filter((tab) => tab.id !== id);
    const active = currentEvidence.active === id ? tabs[Math.min(index, tabs.length - 1)]?.id ?? null : currentEvidence.active;
    setEvidenceView({ ...currentEvidence, tabs, active });
    if (!active) restoreCitationFocus(id);
    evidenceTriggers.current.delete(id);
  };
  useEffect(() => () => evidenceTriggers.current.clear(), [conversationId]);
  const navigate = useNavigate();
  const { version, refresh } = useWorkspace();
  const [searchParams, setSearchParams] = useSearchParams();
  const anchor = searchParams.get('message');
  const research = useResource<ResearchTask | null>(
    conversationId ? `/api/conversations/${encodeURIComponent(conversationId)}/research-task` : null,
    version,
  );
  const [researchTab, setResearchTab] = useState<{ taskId: string; selected: boolean } | null>(null);
  const tabId = useId();
  const strip = useRef<HTMLButtonElement>(null);
  const conversationTab = useRef<HTMLButtonElement>(null);
  const openedResearchTab = useRef<HTMLButtonElement>(null);
  const [controlledResearch, setControlledResearch] = useState<ResearchTask | null>(null);
  const loadedResearch = conversationId && research.data?.home_conversation_id === conversationId ? research.data : null;
  const researchTask = loadedResearch && controlledResearch?.id === loadedResearch.id
    && isNewerResearchTask(controlledResearch, loadedResearch) ? controlledResearch : loadedResearch;
  const hasResearchTab = Boolean(researchTask && researchTab?.taskId === researchTask.id);
  const showResearch = hasResearchTab && researchTab?.selected;
  function selectResearchTab(selected: boolean) {
    if (!researchTask) return;
    setResearchTab({ taskId: researchTask.id, selected });
    requestAnimationFrame(() => (selected ? openedResearchTab.current : conversationTab.current)?.focus({ preventScroll: true }));
  }
  function closeResearchTab() {
    setResearchTab(null);
    requestAnimationFrame(() => strip.current?.focus({ preventScroll: true }));
  }
  const researchBound = Boolean(researchTask && !['closed', 'completed'].includes(researchTask.status));
  const draftProject = searchParams.get('project');
  const composer = useComposerDraft(conversationId, searchParams.get('draft') ?? undefined,
    !conversationId && draftProject && /^[0-9a-f-]{36}$/.test(draftProject) ? draftProject : undefined);
  const { state: composerState, session: composerSession } = composer;
  const { content, selected, mode, knowledgeOnly, delegation, reasoning, permission, researchMode, trialCase } = composerState.data;
  const trial = useResource<TrialCatalog | null>(conversationId
    ? `/api/conversations/${encodeURIComponent(conversationId)}/trial` : '/api/research-trial');
  const trialCatalog = trial.data?.prompts?.length ? trial.data : null;
  const researchGoalInvalid = Boolean(researchMode && Array.from(content.trim()).length > 2000);
  const edit = <K extends keyof DraftData>(key: K, value: DraftData[K]) => composerSession.edit({ [key]: value });
  const setContent = (value: string) => edit('content', value);
  const messageInput = useRef<HTMLTextAreaElement>(null);
  function chooseTrial(prompt: TrialPrompt) {
    if (!trialCatalog || conversationId || composerState.record?.pending) return;
    const untouched = trialCatalog.prompts.some((candidate) => candidate.text === content);
    const draft = content.trim() && !untouched ? `${prompt.text}\n\n${content}` : prompt.text;
    composerSession.edit({ content: draft, trialCase: trialCatalog.key,
      researchMode: prompt.mode === 'research' ? true : undefined });
    messageInput.current?.focus();
  }
  function organizeKnowledge() {
    const instruction = '请将相关讨论或材料整理成知识库文章。';
    if (!content.startsWith(instruction)) {
      setContent(content ? instruction + '\n\n' + content : instruction);
    }
    messageInput.current?.focus();
  }
  function selectResearchMode(value: true | undefined) {
    edit('researchMode', value);
    messageInput.current?.focus();
  }
  function researchChanged(next: ResearchTask) {
    setControlledResearch((previous) => previous?.id === next.id && isNewerResearchTask(previous, next) ? previous : next);
    refresh();
  }
  const setSelected = (value: string[]) => edit('selected', value);
  const completed = useRef<string | null>(null);
  useEffect(() => {
    const row = composerState.record;
    const result = composerState.completed;
    if (result && completed.current !== result.id) {
      completed.current = result.id;
      refresh();
      if (result.intent === 'materials') navigate(`/sources?conversation_id=${encodeURIComponent(result.conversationId)}`, { state: { openImport: true } });
      else if (conversationId !== result.conversationId) navigate(`/chat/${result.conversationId}`, { replace: true });
      return;
    }
    if (conversationId || !row || row.deleted) return;
    if (row.conversationId && !row.pending) {
      navigate(`/chat/${row.conversationId}`, { replace: true });
    } else if (row.scope.startsWith('new:') && searchParams.get('draft') !== row.scope.slice(4)) {
      const next = new URLSearchParams(searchParams);
      next.set('draft', row.scope.slice(4));
      setSearchParams(next, { replace: true });
    }
  }, [composerState.record, composerState.completed, conversationId, navigate, refresh, searchParams, setSearchParams]);
  const scrollArea = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const messages = useMessageHistory(conversationId, version, anchor, scrollArea, following);
  const runs = usePagedResource<Run>(
    conversationId ? `/api/runs/page?conversation_id=${conversationId}&roots_only=true` : null,
    version,
    true,
  );
  const running = useResource<CollectionPage<Run>>(
    conversationId
      ? `/api/runs/page?conversation_id=${conversationId}&roots_only=true&status=running`
      : null,
    version,
  );
  // 与分页历史分开读取，刷新历史时不能卸载等待中的 SSE 并反复重播旧事件。
  const waitingChildren = useResource<CollectionPage<Run>>(
    conversationId
      ? `/api/runs/page?conversation_id=${conversationId}&roots_only=true&status=waiting_children`
      : null,
    version,
  );
  const materials = useResource<Source[]>(
    conversationId
      ? `/api/sources?conversation_id=${encodeURIComponent(conversationId)}`
      : '/api/sources',
    version,
  );
  const capabilities = useResource<{
    levels: string[];
    default_level: string | null;
    can_disable: boolean;
    note: string;
  }>('/api/model-capabilities', version);
  const drafts = usePagedResource<ChangeSet>(
    conversationId ? `/api/changesets/page?conversation_id=${conversationId}` : null,
    version,
  );
  const historyArea = useRef<HTMLDivElement>(null);
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const toBottom = useCallback(() => {
    const area = scrollArea.current;
    if (area) area.scrollTop = area.scrollHeight;
    following.current = true;
    setAwayFromBottom(false);
  }, []);
  useEffect(() => {
    following.current = !anchor;
    const frame = requestAnimationFrame(() => { if (!anchor) toBottom(); });
    return () => cancelAnimationFrame(frame);
  }, [conversationId, anchor, toBottom]);
  useEffect(() => {
    // 流式内容增长时只跟随已在底部的读者，不把正在翻看旧消息的人拉回去。
    const update = () => {
      if (following.current) toBottom();
    };
    const frame = requestAnimationFrame(update);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    if (historyArea.current) observer?.observe(historyArea.current);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  }, [messages.data, toBottom]);
  const [settingsVersion, setSettingsVersion] = useState(0);
  const [savedSettings, setSavedSettings] = useState<{
    id: string;
    value: ConversationSettings;
  } | null>(null);
  const defaults = useResource<{
    permission_mode: string;
    reasoning_effort: string;
    revision: number;
  }>(
    conversationId ? `/api/conversations/${conversationId}/runtime-settings` : null,
    `${version}:${settingsVersion}`,
  );
  const [settingBusy, setSettingBusy] = useState(false);
  const currentSettings =
    savedSettings &&
    savedSettings.id === conversationId &&
    savedSettings.value.revision >= (defaults.data?.revision ?? -1)
      ? savedSettings.value
      : defaults.data;
  const chosenReasoning = conversationId
    ? (currentSettings?.reasoning_effort ?? 'default')
    : reasoning;
  const chosenPermission = conversationId
    ? (currentSettings?.permission_mode ?? 'ask')
    : permission;
  async function changeSetting(field: 'permission_mode' | 'reasoning_effort', value: string) {
    if (!conversationId) {
      if (field === 'permission_mode') edit('permission', value);
      else edit('reasoning', value);
      return;
    }
    if (!currentSettings || settingBusy) return;
    setSettingBusy(true);
    setError('');
    try {
      const saved = await send<ConversationSettings>(
        `/api/conversations/${conversationId}/runtime-settings`,
        { expected_revision: currentSettings.revision, [field]: value },
        'PATCH',
      );
      setSavedSettings({ id: conversationId, value: saved });
      refresh();
    } catch (failure) {
      setError(messageOf(failure));
      refresh();
    } finally {
      setSettingBusy(false);
      setSettingsVersion((value) => value + 1);
    }
  }
  const [operationBusy, setBusy] = useState(false);
  const busy = operationBusy || composerState.busy;
  const [error, setError] = useState('');
  const onChanged = useCallback(() => refresh(), [refresh]);
  async function manageMaterials() {
    if (busy || settingBusy) return;
    if (!conversationId && trialCase) {
      setError('发送试用问题后可追加材料，也可以先退出试用。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      if (conversationId) {
        await composerSession.flush();
        navigate(`/sources?conversation_id=${encodeURIComponent(conversationId)}`, { state: { openImport: true } });
      } else await composerSession.submit('materials');
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (
      !content.trim() ||
      researchGoalInvalid || (researchMode && researchBound) ||
      busy ||
      settingBusy ||
      !composerState.ready || !composerState.persisted || composerState.record?.pending ||
      composerState.record?.deleted || Boolean(composerState.record?.conflicts.length) ||
      (conversationId && (!defaults.data || defaults.loading))
    )
      return;
    setBusy(true);
    setError('');
    try {
      composerSession.edit({ reasoning: chosenReasoning, permission: chosenPermission });
      following.current = true;
      await composerSession.submit(researchMode ? 'research' : 'send');
      if (researchMode) refresh();
    } catch (error) {
      setError(messageOf(error));
    } finally {
      setBusy(false);
    }
  }
  // 分别查询 running/waiting 时，任务可能正处于两次响应之间的 queued 阶段。
  // 保留列表中未终结的观察者，直到获取终态；否则会同时丢掉 SSE 和轮询。
  const active =
    running.data?.items?.[0] ??
    waitingChildren.data?.items?.[0] ??
    runs.data.find((run) => ['running', 'waiting_children', 'queued'].includes(run.status));
  const guideTarget =
    (active?.kind === 'agent' ? active : undefined) ??
    runs.data.find(
      (run) =>
        run.kind === 'agent' &&
        (['paused', 'waiting_input'].includes(run.status) ||
          (run.status === 'interrupted' && run.outcome.kind === 'waiting_approval')),
    );
  const hasQueued = runs.data.some((run) => run.status === 'queued');
  const latestExecution = runs.data.find((run) => run.kind !== 'research_start');
  const activeResearch = guideTarget?.input?.research;
  const workingElsewhere = researchTask && activeResearch?.task_id === researchTask.id
    && activeResearch.branch_id !== (researchTask.write_branch_id ?? researchTask.main_branch_id);
  const pending =
    drafts.data?.filter((draft) => ['draft', 'approved'].includes(draft.status)) ?? [];
  return (
    <div className="flex min-h-0 w-full flex-1 flex-col overflow-hidden">
    {hasResearchTab && <div className="flex shrink-0 gap-1 border-b border-line bg-white px-3 py-1.5" role="tablist" aria-label="工作区标签页"
      inert={narrow && Boolean(shownEvidence)}
      onKeyDown={(event) => {
        if ((event.target as HTMLElement).getAttribute('role') !== 'tab' || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        selectResearchTab(event.key === 'Home' ? false : event.key === 'End' ? true : !showResearch);
      }}>
      <button type="button" ref={conversationTab} role="tab" id={`${tabId}-chat`} aria-controls={`${tabId}-chat-panel`}
        aria-selected={!showResearch} tabIndex={showResearch ? -1 : 0} data-active={!showResearch || undefined}
        className="evidence-tab flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs" onClick={() => selectResearchTab(false)}>
        <MessageSquare size={14} aria-hidden="true" /> 会话
      </button>
      <div data-active={showResearch || undefined} className="evidence-tab flex items-center rounded-lg">
        <button type="button" ref={openedResearchTab} role="tab" id={`${tabId}-research`} aria-controls={`${tabId}-research-panel`}
          aria-selected={Boolean(showResearch)} tabIndex={showResearch ? 0 : -1}
          className="flex items-center gap-1.5 rounded-lg py-1.5 pl-2.5 pr-1 text-xs" onClick={() => selectResearchTab(true)}>
          <FlaskConical size={14} aria-hidden="true" /> 研发任务
        </button>
        <button type="button" className="mr-1 rounded-md p-1 text-control hover:bg-sidebar" aria-label="关闭研发任务标签"
          onClick={closeResearchTab}><X size={13} aria-hidden="true" /></button>
      </div>
    </div>}
    <div className="relative flex min-h-0 w-full flex-1 overflow-hidden">
    <div className={`chat-page min-h-0 min-w-0 flex-1 flex-col ${showResearch ? 'hidden' : 'flex'}`}
      role={hasResearchTab ? 'tabpanel' : undefined} id={`${tabId}-chat-panel`} aria-labelledby={hasResearchTab ? `${tabId}-chat` : undefined}
      inert={showResearch || (narrow && Boolean(shownEvidence))}>
      <div
        className="chat-scroll scrollbar-subtle min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable_both-edges]"
        ref={scrollArea}
        onScroll={() => {
          const area = scrollArea.current;
          if (!area) return;
          const away = area.scrollHeight - area.scrollTop - area.clientHeight > 80;
          following.current = !away;
          setAwayFromBottom(away);
        }}
      >
        <div
          className={`chat-content mx-auto w-full max-w-[1168px] px-11 pt-[35px] pb-6 max-lg:px-7 max-lg:pt-7 max-md:px-4 max-md:pt-3 ${conversationId ? '' : 'flex min-h-full flex-col justify-center'}`}
          ref={historyArea}
        >
          <div className={`conversation mx-auto w-full max-w-[840px] ${conversationId ? '' : 'flex min-h-0 flex-col'}`}>
            {!conversationId && (
              <h1 className="m-0 text-center text-[clamp(1.55rem,3.2vw,2.1rem)] leading-tight font-semibold">今天想研究什么？</h1>
            )}
            <ErrorBox
              message={
                messages.error ||
                running.error ||
                waitingChildren.error ||
                runs.error ||
                drafts.error ||
                defaults.error ||
                error
              }
            />
            {messages.loading && !messages.data && <Loading />}
            {messages.notice && <p role="status" className="text-center text-xs text-muted">{messages.notice}</p>}
            {messages.older && <div className="mb-4 text-center"><Button className="button-quiet text-xs" disabled={messages.loading}
              onClick={() => void messages.loadOlder()}>加载更早的消息</Button></div>}
            {conversationId && messages.data?.length === 0 && (
              <Empty title="这段对话还没有消息">在下方写下问题，或关联材料开始整理。</Empty>
            )}
            {messages.data?.map((message) => (
              <article
                className={`message mt-[15px] mb-6.5 [&_.markdown]:text-sm ${message.role} ${message.role === 'user' ? 'ml-[12%] rounded-[10px] bg-[#eaf0e6] px-5.5 py-4.5 max-md:ml-[5%]' : 'py-3'}`}
                key={message.id}
                data-message-id={message.id}
                aria-label={anchor === message.id ? '搜索命中的消息' : undefined}
              >
                <div className="message-label mb-3 flex justify-between text-xs font-semibold text-[#45644a] [&>span]:text-[10px] [&>span]:font-normal [&>span]:text-[#a3aca0]">
                  {message.role !== 'user' && '研发助手'}
                  <span className="ml-auto">
                    {new Date(message.created_at).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                </div>
                <Markdown text={message.content} onOpenEvidence={openEvidence} />
              </article>
            ))}
            {messages.newer && <div className="my-4 text-center"><Button className="button-quiet text-xs" disabled={messages.loading}
              onClick={() => void messages.loadNewer()}>加载更新的消息</Button></div>}
            {active && <LiveRunPanel key={active.id} initial={active} onChanged={onChanged} />}
            {runs.data
              .filter((run) => ['paused', 'waiting_input'].includes(run.status))
              .map((run) => (
                <section
                  className="card mb-5.5 rounded-[11px] border border-line bg-white p-6.5 max-md:p-4.5"
                  key={run.id}
                >
                  <div className="row flex items-center gap-3 between justify-between">
                    <span>
                      <Badge status={run.status} /> {run.task.slice(0, 80)}
                    </span>
                    <RunPauseControl run={run} onChanged={refresh} />
                  </div>
                  <p className="muted text-muted small text-xs">
                    进展已保留。<Link to={`/runs/${run.id}`}>查看详情或取消</Link>
                  </p>
                  {run.outcome.answer && <Markdown text={run.outcome.answer} onOpenEvidence={openEvidence} />}
                </section>
              ))}
            {runs.data
              .filter((run) => run.question_count)
              .map((run) => (
                <RunQuestions
                  key={`questions-${run.id}`}
                  run={run}
                  refreshKey={version}
                  onChanged={refresh}
                />
              ))}
            {runs.data
              .filter((run) => run.child_count)
              .map((run) => (
                <RunChildren
                  key={`children-${run.id}`}
                  run={run}
                  refreshKey={version}
                  onChanged={refresh}
                />
              ))}
            {runs.data
              .filter(
                (run) =>
                  ['waiting_approval', 'execution_unknown'].includes(run.outcome.kind ?? '') &&
                  ['interrupted', 'paused'].includes(run.status),
              )
              .map((run) => (
                <div key={run.id}>
                  <ExecutionApprovals run={run} refreshKey={version} onChanged={refresh} />
                </div>
              ))}
            {runs.data
              .filter((run) => run.status === 'queued')
              .map((run) => (
                <p className="muted text-muted small text-xs" key={run.id}>
                  排队任务：{run.task.slice(0, 80)} · <Link to={`/runs/${run.id}`}>查看</Link>
                  <Button
                    className="quiet button-quiet small text-xs"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      setError('');
                      try {
                        await send(`/api/runs/${run.id}/cancel`, {});
                        refresh();
                      } catch (error) {
                        setError(messageOf(error));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    取消排队
                  </Button>
                  {guideTarget && (
                    <Button
                      className="quiet button-quiet small text-xs"
                      disabled={busy}
                      onClick={async () => {
                        setBusy(true);
                        setError('');
                        try {
                          await send(`/api/runs/${run.id}/guide`, {
                            target_root_id: guideTarget.id,
                            target_generation: guideTarget.generation,
                          });
                          refresh();
                        } catch (error) {
                          setError(messageOf(error));
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      引导当前任务
                    </Button>
                  )}
                </p>
              ))}
            {runs.data
              .filter((run) => run.outcome.kind === 'guidance')
              .map((run) => (
                <p className="muted text-muted small text-xs" key={run.id}>
                  已转为引导：{run.task.slice(0, 80)} ·{' '}
                  <Link to={`/runs/${run.outcome.target_root_id}`}>查看投递记录</Link>
                </p>
              ))}
            {!active && latestExecution && (
              <div className="run-summary mt-2.5 mb-5 flex flex-wrap items-center gap-[9px] text-xs [&>p]:m-0 [&>p]:w-full [&>p]:text-[#8c594b]">
                <Badge status={latestExecution.status} />
                <RunOutcome run={latestExecution} refreshKey={version} />
                <Link to={`/runs/${latestExecution.id}`}>任务详情 →</Link>
                {runs.data[0].stop_reason && <p>{runs.data[0].stop_reason}</p>}
              </div>
            )}
            {pending.map((draft) => (
              <Link
                className="draft-link my-3.5 flex items-center gap-[15px] rounded-lg border border-[#d8e1d0] bg-[#f8faf1] px-5 py-4.5 text-[#52673f] [&>span:nth-child(2)]:flex-1 [&_strong]:block [&_small]:mt-1 [&_small]:block [&_small]:text-[11px] [&_small]:text-[#879375] [&>span:last-child]:text-xs"
                to={`/knowledge/reviews/${draft.id}`}
                key={draft.id}
              >
                <FileText size={20} />
                <span>
                  <strong>{draft.operations[0]?.title || '知识维护草稿'}</strong>
                  <small>{draft.operations.length} 篇文章 · 等待你的选择</small>
                </span>
                <span>审阅 →</span>
              </Link>
            ))}
            {drafts.next && (
              <Button
                className="quiet button-quiet"
                disabled={drafts.loading}
                onClick={drafts.loadMore}
              >
                加载更多会话变更
              </Button>
            )}
            {runs.next && (
              <Button
                className="quiet button-quiet"
                disabled={runs.loading}
                onClick={runs.loadMore}
              >
                加载更多会话任务
              </Button>
            )}
          </div>
        </div>
      </div>
      <div className="composer-dock relative mx-auto w-full max-w-[928px] shrink-0 px-11 pt-3 max-lg:max-w-[896px] max-lg:px-7 max-md:px-2.5">
        <ComposerRecovery state={composerState} session={composerSession} />
        {!conversationId && trialCatalog && <ResearchTrialStarter catalog={trialCatalog} selected={trialCase === trialCatalog.key}
          disabled={busy || !composerState.ready || Boolean(composerState.record?.pending)} onChoose={chooseTrial}
          onLeave={() => edit('trialCase', undefined)} />}
        {conversationId && trialCatalog && <p className="my-2 text-xs text-control" role="status">试用资料 · {trialCatalog.title}</p>}
        {researchTask && (
          <section aria-label="当前研发任务" className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line bg-sidebar px-3 py-2">
            <div className="flex min-w-0 flex-1 items-center gap-2 text-sm">
              <FlaskConical size={16} className="shrink-0 text-brand" />
              <p className="m-0 truncate font-medium" title={researchTask.title}>{researchTask.title}</p>
            </div>
            <span className="rounded-md bg-white px-2 py-1 text-xs text-brand" role="status">
              {researchStatuses[researchTask.status] ?? researchTask.status}
            </span>
            <span className="text-xs text-control [overflow-wrap:anywhere]">
              {researchTask.status === 'closed' ? '取消前写入' : '写入'} {researchTask.write_branch_name ?? 'main'}
            </span>
            {workingElsewhere && <span className="text-xs text-control [overflow-wrap:anywhere]">
              存量运行 · {activeResearch?.branch_name ?? '原分支'}
            </span>}
            <Button type="button" ref={strip} className="button-secondary text-xs" onClick={() => selectResearchTab(true)}>查看研发简报</Button>
            <ResearchControls key={researchTask.id} task={researchTask} onChanged={researchChanged} />
          </section>
        )}
        {(awayFromBottom || messages.newer) && (
          <Button
            type="button"
            className="scroll-to-bottom absolute top-[-39px] left-[calc(50%_-_18px)] z-[4] size-9 rounded-full p-0 shadow-[0_3px_12px_#254b3518] secondary button-secondary"
            aria-label="回到最新消息"
            onClick={() => { if (messages.newer) void messages.latest(); else toBottom(); }}
          >
            <ArrowDown size={19} />
          </Button>
        )}
        <form
          className="composer relative m-0 w-full max-w-[840px] rounded-[18px] border border-[#cddaca] bg-white px-4.5 pt-4 pb-3 shadow-[0_7px_22px_#2a4f3810] focus-within:border-[#789985] focus-within:shadow-[0_0_0_3px_#78998520] max-md:px-2.5 max-md:pt-3 max-md:pb-2"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="sr-only" htmlFor="message">
            发送给研发助手的消息
          </label>
          <textarea
            ref={messageInput}
            id="message"
            className="min-h-16 max-h-[min(260px,30dvh)] resize-y border-0 p-0 text-[15px] placeholder:text-[#9aa69a] focus:outline-none"
            placeholder={researchMode ? '写下研发目标、范围或要验证的问题…' : '提出问题，或描述你想研究的目标…'}
            rows={3}
            disabled={!composerState.ready || composerState.record?.deleted}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={(event) => {
              // 普通 Enter 保留换行；中文输入法选字期间不能误发送。
              if (
                event.key === 'Enter' &&
                (event.ctrlKey || event.metaKey) &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                if (!busy && content.trim()) void submit();
              }
            }}
          />
          {researchGoalInvalid && <p role="alert" className="my-1 text-xs text-danger">研发目标最多 2000 字，当前输入已保留。</p>}
          {researchMode && researchBound && <p className="my-1 text-xs text-control">此会话已有研发任务。你可以查看简报，或关闭研发模式继续普通对话。</p>}
          <ComposerControls
            conversationId={conversationId}
            onViewResearch={researchTask ? () => selectResearchTab(true) : undefined}
            researchLoading={research.loading}
            onActivateResearch={!researchBound && !researchMode && composerState.ready && !composerState.record?.pending
              ? () => selectResearchMode(true) : undefined}
            onCloseResearch={() => selectResearchMode(undefined)}
            researchMode={Boolean(researchMode)}
            onManageMaterials={() => void manageMaterials()}
            onOrganizeKnowledge={organizeKnowledge}
            composerReady={composerState.ready && !composerState.record?.deleted}
            permission={chosenPermission}
            model={model}
            modelEnabled={modelEnabled}
            reasoning={chosenReasoning}
            settingBusy={settingBusy || Boolean(conversationId && !defaults.data)}
            changeSetting={(field, value) => void changeSetting(field, value)}
            capabilities={capabilities.data}
            mode={mode}
            setMode={(value) => edit('mode', value)}
            knowledgeOnly={knowledgeOnly}
            setKnowledgeOnly={(value) => edit('knowledgeOnly', value)}
            delegation={delegation}
            setDelegation={(value) => edit('delegation', value)}
            materials={materials.data ?? []}
            selected={selected}
            setSelected={setSelected}
            busy={busy}
            disabled={
              busy || settingBusy || Boolean(conversationId && defaults.loading) || !content.trim() ||
              researchGoalInvalid || Boolean(researchMode && researchBound) ||
              !composerState.ready || !composerState.persisted || Boolean(composerState.record?.pending) ||
              Boolean(composerState.record?.deleted) || Boolean(composerState.record?.conflicts.length)
            }
            context={
              conversationId ? (
                <ContextUsage
                  key={conversationId}
                  conversationId={conversationId}
                  active={Boolean(active) || hasQueued}
                  version={version}
                  onChanged={refresh}
                />
              ) : null
            }
          />
        </form>
      </div>
    </div>
    {hasResearchTab && researchTask && <div role="tabpanel" id={`${tabId}-research-panel`} aria-labelledby={`${tabId}-research`}
      className={`scrollbar-subtle min-h-0 min-w-0 flex-1 overflow-auto overscroll-contain ${showResearch ? '' : 'hidden'}`}
      inert={!showResearch || (narrow && Boolean(shownEvidence))}>
      <ResearchChatPane
        key={researchTask.id}
        task={researchTask}
        activeRun={active}
        onRunChanged={onChanged}
        onOpenChat={() => selectResearchTab(false)}
        onChanged={researchChanged}
      />
    </div>}
    {shownEvidence && currentEvidence && <ChatEvidencePane tabs={currentEvidence.tabs} active={shownEvidence} narrow={narrow}
      onActivate={(active) => setEvidenceView({ ...currentEvidence, active })} onClose={closeEvidence}
      onReturn={() => {
        setEvidenceView({ ...currentEvidence, narrowVisible: false });
        restoreCitationFocus(shownEvidence);
      }} />}
    </div>
    </div>
  );
}
