import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUp, BookOpen, FlaskConical, GraduationCap, Paperclip, Plus, Search, ShieldCheck, SlidersHorizontal, X } from 'lucide-react';
import type { Source } from '../../shared/types/types';
import { useResource } from '../../shared/api/api';
import { type BrowserMode, type BrowserSettings, browserSettingsPath, effectiveBrowserMode } from '../tools/browserSettings';
import { ConversationMemorySnapshot } from './ConversationMemorySnapshot';
import { KnowledgeMount, type MountProps } from './KnowledgeMount';

type Props = {
  conversationId?: string;
  snapshotEligible?: boolean;
  trial?: boolean;
  knowledgeMount?: MountProps;
  onManageMaterials: () => void;
  onOrganizeKnowledge: () => void;
  composerReady: boolean;
  permission: string;
  reasoning: string;
  model?: string;
  modelEnabled?: boolean;
  settingBusy: boolean;
  changeSetting: (field: 'permission_mode' | 'reasoning_effort', value: string) => void;
  capabilities?: {
    levels: string[];
    default_level: string | null;
    can_disable: boolean;
    note: string;
  } | null;
  mode: 'ask' | 'preview' | 'direct';
  setMode: (value: 'ask' | 'preview' | 'direct') => void;
  knowledgeOnly: boolean;
  browserMode?: BrowserMode;
  setBrowserMode?: (value: BrowserMode | undefined) => void;
  setKnowledgeOnly: (value: boolean) => void;
  delegation: boolean;
  setDelegation: (value: boolean) => void;
  materials: Source[];
  selected: string[];
  setSelected: (value: string[]) => void;
  disabled: boolean;
  busy: boolean;
  context: ReactNode;
  onViewResearch?: () => void;
  researchLoading?: boolean;
  onActivateResearch?: () => void;
  onCloseResearch?: () => void;
  researchMode: boolean;
};

const reasoningLabels: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
  max: '最高',
  none: '无',
};

/** 常用控制常驻底栏，其余选项渐进展开；收起菜单不会重置用户选择。 */
export function ComposerControls(props: Props) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState(false);
  const browser = useResource<BrowserSettings>(settings && props.setBrowserMode ? browserSettingsPath : null);
  const defaultBrowser = effectiveBrowserMode(browser.data);
  const attachAllowed = Boolean(browser.data?.local_attach_enabled && browser.data.deployment?.local_attach_available);
  const unavailableAttach = props.browserMode === 'attach' && !attachAllowed && !!browser.data;
  const browserMode = props.browserMode ?? defaultBrowser;
  const [materials, setMaterials] = useState<string[] | null>(null);
  const [query, setQuery] = useState('');
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuItem = 'button-quiet w-full justify-start gap-2 text-left text-sm';
  const openMaterials = () => {
    trigger.current?.focus();
    setOpen(false);
    setQuery('');
    setMaterials(props.selected);
  };
  const visibleMaterials = props.materials.filter((source) =>
    source.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);
  return (
    <>
      {props.selected.length > 0 && (
        <div className="composer-attachments" aria-label="已关联材料">
          <Button type="button" className="button-secondary text-xs" onClick={openMaterials}>
            <Paperclip size={14} /> {props.selected.length} 份材料
          </Button>
        </div>
      )}
      <div className="composer-toolbar mt-2.5 flex items-center justify-between gap-2 max-md:flex-wrap max-md:gap-[3px]">
        <div className="composer-toolbar-left flex min-w-0 flex-wrap items-center gap-2 max-md:gap-[3px]">
          <div ref={container} className="composer-menu relative">
            <Button
              ref={trigger}
              type="button"
              className="quiet button-quiet icon-button size-[34px] shrink-0 rounded-full p-2 max-md:h-8 max-md:w-[30px]"
              aria-label="更多设置与材料"
              aria-expanded={open}
              aria-controls="composer-extra-options"
              onClick={() => setOpen(!open)}
            >
              <Plus size={21} />
            </Button>
            {open && (
              <div
                id="composer-extra-options"
                className="composer-popover absolute bottom-[calc(100%+10px)] left-0 z-20 grid w-[min(235px,calc(100vw-48px))] gap-0.5 rounded-xl border border-line bg-white p-1.5 shadow-[0_10px_35px_#183d3525]"
                role="region"
                aria-label="更多设置与材料"
              >
                <Button type="button" className={menuItem} onClick={openMaterials}>
                  <Paperclip size={16} /> 文件和文件夹
                </Button>
                <Button type="button" className={menuItem} disabled={!props.composerReady}
                  onClick={() => { setOpen(false); props.onOrganizeKnowledge(); }}>
                  <BookOpen size={16} /> 整理成知识
                </Button>
                <Link
                  className={`button ${menuItem}`}
                  to={props.conversationId ? `/learning?conversation_id=${encodeURIComponent(props.conversationId)}` : '/learning'}
                  onClick={() => setOpen(false)}
                >
                  <GraduationCap size={16} /> 建立学习任务
                </Link>
                {props.onViewResearch && (
                  <Button type="button" className={menuItem}
                    onClick={() => { trigger.current?.focus(); setOpen(false); props.onViewResearch?.(); }}>
                    <FlaskConical size={16} /> 查看研发简报
                  </Button>
                )}
                {props.onActivateResearch && (
                  <Button type="button" className={menuItem} disabled={props.researchLoading}
                    onClick={() => { setOpen(false); props.onActivateResearch?.(); }}>
                    <FlaskConical size={16} /> 研发模式
                  </Button>
                )}
                <Button type="button" className={menuItem} onClick={() => { trigger.current?.focus(); setOpen(false); setSettings(true); }}>
                  <SlidersHorizontal size={16} /> 本次任务设置
                </Button>
              </div>
            )}
            {settings && (
              <Dialog title="本次任务设置" onClose={() => setSettings(false)}
                footer={<Button type="button" onClick={() => setSettings(false)}>完成</Button>}>
                <div className="grid gap-3">
                <label>
                  知识变更
                  <select
                    value={props.mode}
                    onChange={(e) => props.setMode(e.target.value as Props['mode'])}
                  >
                    <option value="ask">需要时询问</option>
                    <option value="preview">先看草稿</option>
                    <option value="direct">本次直接保存</option>
                  </select>
                </label>
                <label className="check m-0 flex-row items-center gap-2 font-normal">
                  <input
                    type="checkbox"
                    checked={props.knowledgeOnly}
                    onChange={(e) => props.setKnowledgeOnly(e.target.checked)}
                  />
                  仅使用知识库
                </label>
                <label className="check m-0 flex-row items-center gap-2 font-normal">
                  <input
                    type="checkbox"
                    checked={props.delegation}
                    onChange={(e) => props.setDelegation(e.target.checked)}
                  />
                  允许子Agent分工
                </label>
                {props.setBrowserMode && (
                  <div className="min-w-0 rounded-lg border border-line bg-canvas p-3">
                    <label className="m-0 text-sm font-medium">
                      动态浏览器取证（本次任务）
                      <select aria-label="本次任务浏览器模式" value={browserMode}
                        disabled={props.knowledgeOnly || props.trial || (!browser.data?.deployment?.browser_enabled && !unavailableAttach)}
                        onChange={(event) => {
                          const value = event.target.value as BrowserMode;
                          props.setBrowserMode?.(value);
                        }}>
                        <option value="launch">容器隔离浏览器{defaultBrowser === 'launch' ? '（默认）' : ''}</option>
                        {unavailableAttach && <option value="attach" disabled>本机浏览器已停用，请重新选择</option>}
                        {attachAllowed && <option value="attach">本机可见浏览器 · CDP{defaultBrowser === 'attach' ? '（默认）' : ''}</option>}
                      </select>
                    </label>
                    {(props.knowledgeOnly || props.trial || !browser.data?.deployment?.browser_enabled) && (
                      <p className="mt-2 mb-0 text-xs text-muted">
                        {props.knowledgeOnly ? '仅知识库模式下暂停联网和动态浏览器取证。'
                          : props.trial ? '试用案例使用固定材料。'
                            : browser.loading ? '正在读取部署状态…' : '当前部署未启用动态浏览器取证。'}
                      </p>
                    )}
                  </div>
                )}
                {props.conversationId && (
                  <Link to={`/tools?conversation_id=${props.conversationId}#execution-rules`}>
                    命令规则
                  </Link>
                )}
                </div>
                <ConversationMemorySnapshot key={props.conversationId} conversationId={props.conversationId}
                  enabled={props.snapshotEligible ?? Boolean(props.conversationId)} trial={props.trial} />
              </Dialog>
            )}
            {materials && (
              <Dialog title="文件和文件夹" onClose={() => setMaterials(null)}
                footer={<>
                  <Button type="button" className="button-secondary" onClick={() => setMaterials(null)}>取消</Button>
                  <Button type="button" onClick={() => { props.setSelected(materials); setMaterials(null); }}>
                    确定（{materials.length}）
                  </Button>
                </>}>
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="relative min-w-0 flex-1">
                    <input autoFocus type="search" aria-label="搜索已有材料" className="pr-9"
                      placeholder="搜索已有材料" value={query} onChange={(event) => setQuery(event.target.value)} />
                    <Search className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-control" size={16} />
                  </div>
                  <Button type="button" className="button-secondary" disabled={props.busy || !props.composerReady}
                    onClick={() => { props.setSelected(materials); setMaterials(null); props.onManageMaterials(); }}>
                    导入材料
                  </Button>
                </div>
                <div className="max-h-60 overflow-y-auto divide-y divide-line">
                  {visibleMaterials.map((source) => (
                    <label className="check m-0 flex-row items-center gap-2 py-2 font-normal" key={source.id}>
                      <input type="checkbox" checked={materials.includes(source.id)}
                        onChange={(event) => setMaterials(event.target.checked
                          ? [...materials, source.id] : materials.filter((id) => id !== source.id))} />
                      <span className="[overflow-wrap:anywhere]">{source.title}</span>
                    </label>
                  ))}
                  {!visibleMaterials.length && <p className="text-sm text-control">{query ? '没有匹配的材料。' : '还没有可选材料。'}</p>}
                </div>
              </Dialog>
            )}
          </div>
          {props.knowledgeMount && <KnowledgeMount {...props.knowledgeMount} />}
          <label
            className="composer-permission m-0 flex-row items-center gap-[3px] max-md:[&>svg]:hidden"
            title="请求批准：逐次确认修改操作。替我审批：独立模型审查具体动作，共享任务预算；不确定时仍询问你。"
          >
            <ShieldCheck size={15} />
            <span className="sr-only">执行权限</span>
            <select
              className="h-[34px] max-w-[138px] border-0 bg-transparent px-0.5 py-0 text-xs text-control max-md:h-8 max-md:max-w-[92px] max-md:text-[11px]"
              aria-label="执行权限"
              value={props.permission}
              disabled={props.settingBusy}
              onChange={(e) => props.changeSetting('permission_mode', e.target.value)}
            >
              <option value="ask">请求批准</option>
              <option value="auto_review">替我审批</option>
            </select>
          </label>
          {props.researchMode && <Button type="button" className="button-quiet rounded-full bg-sidebar text-xs text-brand"
            aria-label="关闭研发模式" onClick={props.onCloseResearch}>研发模式 <X size={14} /></Button>}
        </div>
        <div className="composer-toolbar-right flex min-w-0 items-center justify-end gap-2 max-md:ml-auto max-md:gap-[3px]">
          {props.context}
          <span
            className="composer-model min-w-0 max-w-[180px] truncate text-xs leading-[34px] text-control max-md:max-w-[100px] max-md:text-[11px] max-md:leading-8"
            title={`${props.model ?? '正在读取模型'} · ${props.modelEnabled === undefined ? '正在读取状态' : props.modelEnabled ? '模型调用已开启' : '模型调用关闭'}`}
          >
            {props.model ?? '读取模型…'}
          </span>
          {props.capabilities?.levels?.length ? (
            <label
              className="composer-reasoning m-0 shrink-0 flex-row items-center gap-[3px]"
              title={props.capabilities.note}
            >
              <span className="sr-only">思考强度</span>
              <select
                className="h-[34px] max-w-[138px] border-0 bg-transparent px-0.5 py-0 text-xs text-control max-md:h-8 max-md:max-w-[92px] max-md:text-[11px]"
                aria-label="思考强度"
                value={props.reasoning}
                disabled={props.settingBusy}
                onChange={(e) => props.changeSetting('reasoning_effort', e.target.value)}
              >
                <option value="default">
                  默认
                  {props.capabilities.default_level
                    ? `（${reasoningLabels[props.capabilities.default_level] ?? props.capabilities.default_level}）`
                    : ''}
                </option>
                {props.capabilities.can_disable && <option value="off">关闭</option>}
                {props.capabilities.levels.map((level) => (
                  <option key={level} value={level}>
                    {reasoningLabels[level] ?? level}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <span className="muted text-muted small text-xs" title="思考强度由供应商决定">
              默认思考
            </span>
          )}
          <Button
            className="send icon-button size-[34px] shrink-0 rounded-full p-2 max-md:h-8 max-md:w-[30px]"
            disabled={props.disabled}
            type="submit"
            aria-label={props.researchMode ? '开始研发' : '发送'}
            title={props.busy ? '提交中…' : '发送（Ctrl / ⌘ + Enter）'}
          >
            <ArrowUp size={20} />
          </Button>
        </div>
      </div>
    </>
  );
}
