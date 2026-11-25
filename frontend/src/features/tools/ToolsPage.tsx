import { useEffect, useState } from 'react';
import { BookOpen, Bot, Layers, SquareTerminal, ToggleRight } from 'lucide-react';
import { Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { ErrorBox, Loading, PageTitle } from '../../shared/ui/components';
import { Dialog } from '../../shared/ui/Dialog';
import { SettingsRow } from '../../shared/ui/SettingsRow';
import { BrowserSettingsRow } from './BrowserSettingsDialog';
import { AgentSettingsTabs } from './AgentSettingsTabs';
import { ExecutionRules } from '../permissions/ExecutionRules';
import { CapabilitySelector, type CapabilityChoice } from './CapabilitySelector';
import { EffectiveCapabilities } from './EffectiveCapabilities';
import { ToolGroupsPanel } from './ToolGroupsPage';

type Tool = CapabilityChoice & { source: string; legacy_only?: boolean };
type Group = CapabilityChoice & { tool_ids: string[] };
type Catalog = { tools: Tool[]; groups: Group[] };
type Policy = {
  revision: number;
  enabled: Record<string, boolean>;
  skill_enabled?: Record<string, boolean>;
  main_tools: string[] | null;
  main_tool_groups: string[] | null;
  resolved_main_tools: string[] | null;
};
type Skill = CapabilityChoice & {
  enabled?: boolean;
  available?: boolean;
  availability_status?: string;
};

/** 只提交变更项；保存冲突不丢弃草稿，也不重新展开未修改的组选择。 */
function PolicyEditor({
  currentPolicy,
  catalog,
  skills,
  refreshing,
  refresh,
  openGroups,
  openRules,
}: {
  currentPolicy: Policy;
  catalog: Catalog;
  skills: Skill[];
  refreshing: boolean;
  refresh: () => void;
  openGroups: () => void;
  openRules: () => void;
}) {
  const [saved, setSaved] = useState(currentPolicy);
  const [draft, setDraft] = useState(currentPolicy);
  // 组编辑可能推进策略修订。干净的表单跟随新版，已有草稿保留原比较基线。
  if (currentPolicy.revision !== saved.revision && draft === saved) {
    setSaved(currentPolicy);
    setDraft(currentPolicy);
  }
  const [selecting, setSelecting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inherited = draft.main_tools === null && draft.main_tool_groups === null;
  const local = catalog.tools.filter((tool) => tool.source !== 'mcp');
  const patch: Record<string, unknown> = { expected_revision: saved.revision };
  const enabled = Object.fromEntries(
    local
      .filter((tool) => draft.enabled[tool.id] !== saved.enabled[tool.id])
      .map((tool) => [tool.id, draft.enabled[tool.id]]),
  );
  if (Object.keys(enabled).length) patch.enabled = enabled;
  const skillEnabled = Object.fromEntries(
    skills
      .filter(
        (skill) =>
          (draft.skill_enabled?.[skill.name] ?? true) !==
          (saved.skill_enabled?.[skill.name] ?? true),
      )
      .map((skill) => [skill.name, draft.skill_enabled?.[skill.name] ?? true]),
  );
  if (Object.keys(skillEnabled).length) patch.skill_enabled = skillEnabled;
  for (const field of ['main_tools', 'main_tool_groups'] as const) {
    if (JSON.stringify(draft[field]) !== JSON.stringify(saved[field])) patch[field] = draft[field];
  }
  const changed = Object.keys(patch).length > 1;
  useEffect(() => {
    if (!changed) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [changed]);
  const skillOptions = skills.map((skill) => {
    const available = skill.available ?? !skill.unavailable_reason;
    return {
      ...skill,
      id: skill.name,
      disabled: !available,
      detail_url: `/api/skills/${encodeURIComponent(skill.name)}`,
      status: !available
        ? '不可用'
        : (draft.skill_enabled?.[skill.name] ?? true)
          ? '已启用'
          : '已关闭',
    };
  });
  const groups = catalog.groups
    .filter((group) => group.id !== 'mcp')
    .map((group) => ({
      ...group,
      summary: group.description,
      members: local.filter((tool) => group.tool_ids.includes(tool.id)),
    }));
  async function save() {
    setBusy(true);
    setError('');
    try {
      await send('/api/tools/policy', patch, 'PATCH');
      refresh();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      aria-label="工具策略配置"
      className="mt-5 space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (changed && !busy && !refreshing) void save();
      }}
    >
      <section className="rounded-xl border border-line bg-white" aria-label="内置工具">
        <header className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
          <h2 className="m-0 text-base font-semibold">工具配置</h2>
          <p className="m-0 text-xs text-muted">
            当前配置修订 {saved.revision}{changed ? ' · 有未保存修改' : ''}
          </p>
        </header>
        <BrowserSettingsRow />
        <SettingsRow title="全局工具开关" description={`已启用 ${local.filter((tool) => draft.enabled[tool.id]).length} 项工具 · 适用于主智能体和子智能体`}
          icon={<ToggleRight size={17} aria-hidden="true" />}>
          <CapabilitySelector
            title="全局工具开关"
            options={local}
            selected={local.filter((tool) => draft.enabled[tool.id] === true).map((tool) => tool.id)}
            disabled={busy || refreshing}
            buttonLabel="管理全部工具"
            triggerOnly
            onChange={(selected) =>
              setDraft({
                ...draft,
                enabled: {
                  ...draft.enabled,
                  ...Object.fromEntries(local.map((tool) => [tool.id, selected.includes(tool.id)])),
                },
              })
            }
          />
        </SettingsRow>
        <SettingsRow title="主智能体工具" description={inherited ? '继承默认本地工具选择' : '使用自定义工具选择'}
          icon={<Bot size={17} aria-hidden="true" />}>
          <Button type="button" className="button-secondary" disabled={busy || refreshing} onClick={() => setSelecting(true)}>
            配置选择
          </Button>
          <EffectiveCapabilities version={currentPolicy.revision} />
        </SettingsRow>
        <SettingsRow title="工具组" description="按用途管理可复用的工具组合"
          icon={<Layers size={17} aria-hidden="true" />}>
          <Button type="button" className="button-secondary" onClick={openGroups}>管理工具组</Button>
        </SettingsRow>
        <SettingsRow title="命令执行" description="管理命令的询问、允许和拒绝规则"
          icon={<SquareTerminal size={17} aria-hidden="true" />}>
          <Button type="button" className="button-secondary" onClick={openRules}>命令执行规则</Button>
        </SettingsRow>
        <SettingsRow title="全局技能开关" description={`已启用 ${skillOptions.filter((skill) => skill.status === '已启用').length} 项技能`}
          icon={<BookOpen size={17} aria-hidden="true" />}>
          <CapabilitySelector
            title="Skills"
            options={skillOptions}
            selected={skillOptions
              .filter((skill) => !skill.disabled && (draft.skill_enabled?.[skill.name] ?? true))
              .map((skill) => skill.id)}
            disabled={busy || refreshing}
            buttonLabel="管理全部 Skills"
            triggerOnly
            onChange={(selected) =>
              setDraft({
                ...draft,
                skill_enabled: {
                  ...draft.skill_enabled,
                  ...Object.fromEntries(
                    skillOptions
                      .filter((skill) => !skill.disabled)
                      .map((skill) => [skill.name, selected.includes(skill.id)]),
                  ),
                },
              })
            }
          />
        </SettingsRow>
      </section>
      {selecting && (
        <Dialog title="主智能体工具" onClose={() => setSelecting(false)}
          footer={<Button type="button" onClick={() => setSelecting(false)}>完成配置</Button>}>
          <label className="m-0 flex-row items-center gap-2 text-sm font-normal">
            <input
              type="checkbox"
              checked={inherited}
              disabled={busy || refreshing}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  main_tools: event.target.checked ? null : [],
                  main_tool_groups: event.target.checked ? null : [],
                })
              }
            />
            继承默认本地工具选择
          </label>
          <p className="text-xs text-muted">
            {inherited
              ? '使用默认选择，仍受全局开关和任务选项约束。'
              : '工具组与单独工具合并；两者都不选表示不使用本地工具。MCP 许可单独管理。'}
          </p>
          {!inherited && (
            <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
              <CapabilitySelector
                title="工具组"
                options={groups}
                selected={draft.main_tool_groups ?? []}
                disabled={busy || refreshing}
                onChange={(main_tool_groups) => setDraft({ ...draft, main_tool_groups })}
              />
              <CapabilitySelector
                title="单独工具"
                options={local.filter((item) => !item.legacy_only)}
                selected={draft.main_tools ?? []}
                disabled={busy || refreshing}
                onChange={(main_tools) => setDraft({ ...draft, main_tools })}
              />
            </div>
          )}
        </Dialog>
      )}
      <ErrorBox message={error} />
      <div className="sticky bottom-0 z-10 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-white/95 p-4 shadow-sm backdrop-blur-sm">
        <Button type="submit" disabled={busy || refreshing || !changed}>
          {busy ? '正在保存…' : '保存工具配置'}
        </Button>
        <Button
          type="button"
          className="secondary button-secondary"
          disabled={busy || refreshing}
          onClick={refresh}
        >
          放弃本地修改并刷新
        </Button>
      </div>
      <p className="text-xs text-muted">
        新选择用于新任务。关闭会限制后续调用，不能撤销已执行操作；重新开启不会恢复旧任务已撤回的许可。
      </p>
    </form>
  );
}

function ToolsManagement() {
  const location = useLocation();
  const [rules, setRules] = useState(false);
  const [groups, setGroups] = useState(false);
  const [catalogVersion, setCatalogVersion] = useState(0);
  const [dismissedFor, setDismissedFor] = useState<string>();
  const [version, setVersion] = useState(0);
  const refresh = () => setVersion((value) => value + 1);
  const catalog = useResource<Catalog>('/api/tools/catalog', version + catalogVersion);
  const policy = useResource<Policy>('/api/tools/policy', version + catalogVersion);
  const skills = useResource<Skill[]>('/api/skills', version);
  // 聊天仍可通过旧地址直达规则，同时保留会话参数用于限缩作用域。
  const directRules = location.hash === '#execution-rules' || new URLSearchParams(location.search).has('conversation_id');
  const closeRules = () => { setRules(false); setDismissedFor(location.key); };
  return (
    <>
      <PageTitle title="智能体设置" />
      <AgentSettingsTabs />
      <ErrorBox message={catalog.error || policy.error || skills.error} />
      {(catalog.loading || policy.loading || skills.loading) && <Loading />}
      {(catalog.error || policy.error || skills.error) && (
        <Button className="secondary button-secondary" onClick={refresh}>
          重试加载配置
        </Button>
      )}
      {catalog.data && policy.data && (
        <PolicyEditor
          key={version}
          currentPolicy={policy.data}
          catalog={catalog.data}
          skills={skills.data ?? []}
          refreshing={
            catalog.loading ||
            policy.loading ||
            skills.loading ||
            Boolean(catalog.error || policy.error || skills.error)
          }
          refresh={refresh}
          openGroups={() => setGroups(true)}
          openRules={() => setRules(true)}
        />
      )}
      {groups && (
        <Dialog title="管理工具组" onClose={() => setGroups(false)}
          footer={<Button type="button" className="button-secondary" onClick={() => setGroups(false)}>关闭工具组</Button>}>
          <ToolGroupsPanel onChanged={() => setCatalogVersion((n) => n + 1)} />
        </Dialog>
      )}
      {(rules || (directRules && dismissedFor !== location.key)) &&
        <Dialog title="命令执行规则" onClose={closeRules}><ExecutionRules /></Dialog>}
    </>
  );
}

export function ToolsPage() {
  const [query] = useSearchParams();
  const role = query.get('role_id');
  return role ? (
    <Navigate to={`/subagents?effective=${encodeURIComponent(role)}`} replace />
  ) : (
    <ToolsManagement />
  );
}
