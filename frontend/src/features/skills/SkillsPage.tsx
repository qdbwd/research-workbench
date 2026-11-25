import { useState } from 'react';
import { Activity, BookOpen, Download, Search, Sparkles, Upload } from 'lucide-react';
import { messageOf, send, useResource } from '../../shared/api/api';
import { useWorkspace } from '../../shared/state/workspace';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { Empty, ErrorBox, Loading, PageTitle } from '../../shared/ui/components';
import { HelpHint } from '../../shared/ui/Workbench';
import {
  ProtectionBadge,
  ProtectionControls,
  type ProtectionLevel,
} from '../evolution/ProtectionControls';
import { PendingCount, type Change } from '../evolution/LearningJourneyList';
import { AgentSettingsTabs } from '../tools/AgentSettingsTabs';
import { CapabilityDetails } from '../tools/CapabilityDetails';
import { SkillEvolutionDialog } from './SkillEvolutionDialog';
import { SkillImportDialog } from './SkillImportDialog';
import { SkillMethodProposals } from './SkillMethodProposals';
import type { SkillEntry } from './types';

export function SkillsPage() {
  const { version, refresh } = useWorkspace();
  const [local, setLocal] = useState(0);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [evolutionOpen, setEvolutionOpen] = useState(false);
  const viewVersion = String(version) + ':' + String(local);
  const catalog = useResource<SkillEntry[]>(
    includeArchived ? '/api/skills?include_archived=true' : '/api/skills',
    viewVersion,
  );
  const pending = useResource<Change[]>(
    '/api/evolution/changes?target_kind=skill&review_state=pending_review',
    viewVersion,
  );
  const pendingCount =
    pending.data?.filter((change) => change.review_state === 'pending_review').length ?? 0;
  const [query, setQuery] = useState('');
  const [proposalsOpen, setProposalsOpen] = useState(false);
  const [editor, setEditor] = useState<{ skill: SkillEntry | null } | null>(null);
  const [detail, setDetail] = useState<SkillEntry | null>(null);
  const [confirm, setConfirm] = useState<{
    skill: SkillEntry;
    action: 'remove' | 'disable';
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const reload = () => {
    setLocal((value) => value + 1);
    refresh();
  };
  async function toggle(skill: SkillEntry, enabled: boolean) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await send(
        '/api/tools/policy',
        { expected_revision: skill.policy_revision, skill_enabled: { [skill.name]: enabled } },
        'PATCH',
      );
      setNotice(enabled ? '技能已启用，新任务可以按需读取。' : '技能已停用。');
      setConfirm(null);
      reload();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  async function remove(skill: SkillEntry) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await send(
        `/api/skills/${encodeURIComponent(skill.name)}`,
        { expected_revision: skill.revision },
        'DELETE',
      );
      setNotice('技能已移除。');
      setConfirm(null);
      reload();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  async function changeSkill(
    skill: SkillEntry,
    action: 'protection' | 'archive' | 'unarchive',
    level?: ProtectionLevel,
  ) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await send(
        '/api/skills/' + encodeURIComponent(skill.name) + '/' + action,
        { expected_revision: skill.revision, ...(level ? { protection_level: level } : {}) },
        action === 'protection' ? 'PATCH' : 'POST',
      );
      setNotice(
        action === 'archive'
          ? '技能已归档。'
          : action === 'unarchive'
            ? '技能已恢复。'
            : '技能保护已更新。',
      );
      reload();
    } catch (failure) {
      setError(messageOf(failure));
      reload();
    } finally {
      setBusy(false);
    }
  }
  async function download(skill: SkillEntry) {
    setBusy(true);
    setError('');
    try {
      const response = await fetch(`/api/skills/${encodeURIComponent(skill.name)}/export`);
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.detail || '导出失败');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `${skill.name}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  const shown = (catalog.data ?? []).filter(
    (skill) =>
      (includeArchived || !skill.archived_at) &&
      `${skill.name} ${skill.display_name} ${skill.description}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );
  return (
    <>
      <PageTitle title="智能体设置" />
      <AgentSettingsTabs />
      <div className="mb-3 flex items-center gap-1">
        <h2 className="m-0 text-lg font-semibold">技能</h2>
        <HelpHint label="技能说明">
          技能可以提供按需读取的任务方法，也可以附带脚本和参考资料。
        </HelpHint>
      </div>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex w-full flex-wrap items-center gap-3 md:w-auto">
          <div className="relative w-full max-w-72 text-control">
            <input
              aria-label="搜索技能"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="min-w-0 bg-white pr-10"
              placeholder="搜索名称或用途"
            />
            <Search
              size={17}
              className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2"
            />
          </div>
          <label className="check flex-row items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
            />
            显示已归档
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button className="button-secondary" onClick={() => setProposalsOpen(true)}>
            方法改进提案
          </Button>
          <Button className="button-secondary" onClick={() => setEvolutionOpen(true)}>
            <Sparkles size={16} aria-hidden="true" />
            技能进化与整理
            <PendingCount count={pendingCount} />
          </Button>
          <Button
            disabled={busy}
            onClick={() => {
              setEditor({ skill: null });
              setError('');
            }}
          >
            <Upload size={16} />
            导入技能
          </Button>
        </div>
      </div>
      {evolutionOpen && (
        <SkillEvolutionDialog
          onClose={() => setEvolutionOpen(false)}
          onChanged={reload}
          version={viewVersion}
        />
      )}
      {proposalsOpen && (
        <SkillMethodProposals onClose={() => setProposalsOpen(false)} onPublished={reload} />
      )}
      {!confirm && <ErrorBox message={error} />}
      <ErrorBox message={catalog.error} />
      {(busy || notice) && (
        <p role="status" className="rounded-lg bg-[#e7f1e8] p-3 text-sm">
          {busy ? '正在处理…' : notice}
        </p>
      )}
      {catalog.loading && !catalog.data && <Loading />}
      {catalog.error && (
        <Button className="secondary button-secondary" onClick={reload}>
          重新加载
        </Button>
      )}
      {catalog.data && !shown.length && (
        <Empty title={query ? '没有匹配的技能' : '还没有技能'}>
          导入 SKILL.md 或一个完整文件包即可开始。
        </Empty>
      )}
      <div className="grid auto-rows-fr gap-4 md:grid-cols-2">
        {shown.map((skill) => (
          <article
            key={skill.name}
            aria-label={skill.display_name}
            className="flex min-w-0 flex-col rounded-xl border border-line bg-white p-5"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h2 className="m-0 truncate text-base font-semibold" title={skill.display_name}>
                  {skill.display_name}
                </h2>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-control">
                  <span className="break-all">
                    {skill.name} · 修订 {skill.revision}
                  </span>
                  <ProtectionBadge level={skill.protection_level} />
                </div>
              </div>
              <label className="flex shrink-0 items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  aria-label={`启用技能：${skill.display_name}`}
                  checked={skill.enabled}
                  disabled={
                    busy || catalog.loading || !skill.available || Boolean(skill.archived_at)
                  }
                  onChange={() => {
                    setError('');
                    if (skill.enabled) setConfirm({ skill, action: 'disable' });
                    else void toggle(skill, true);
                  }}
                />
                {skill.archived_at ? (
                  <span className="rounded-full border border-line bg-canvas px-2 py-1">
                    已归档
                  </span>
                ) : skill.enabled ? (
                  '已启用'
                ) : (
                  '停用'
                )}
              </label>
            </div>
            <p className="my-3 line-clamp-2 h-10 text-sm leading-5 text-control [overflow-wrap:anywhere]">
              {skill.summary || skill.description}
            </p>
            <p className="mb-3 flex items-start gap-2 text-xs text-muted">
              <Activity size={14} className="shrink-0" aria-hidden="true" />
              {skill.telemetry && skill.telemetry.load_count > 0 ? (
                <span>
                  读取 {skill.telemetry.load_count} 次 · 成功 {skill.telemetry.success_run_count} ·
                  失败 {skill.telemetry.failed_run_count}
                  {skill.telemetry.correction_count > 0
                    ? ' · 纠偏 ' + skill.telemetry.correction_count
                    : ''}
                  {skill.telemetry.last_used_at &&
                    ' · 最近使用 ' +
                      new Date(skill.telemetry.last_used_at).toLocaleDateString('zh-CN')}
                </span>
              ) : (
                '尚无运行读取记录'
              )}
            </p>
            {skill.managed && (
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <ProtectionControls
                  level={skill.protection_level}
                  noun="技能"
                  disabled={busy || catalog.loading}
                  onChange={(level) => void changeSkill(skill, 'protection', level)}
                />
                <Button
                  type="button"
                  className="button-quiet"
                  disabled={
                    busy ||
                    catalog.loading ||
                    (!skill.archived_at &&
                      Boolean(skill.protection_level && skill.protection_level !== 'normal'))
                  }
                  title={
                    !skill.archived_at &&
                    skill.protection_level &&
                    skill.protection_level !== 'normal'
                      ? '请先取消保护再归档'
                      : undefined
                  }
                  onClick={() =>
                    void changeSkill(skill, skill.archived_at ? 'unarchive' : 'archive')
                  }
                >
                  {skill.archived_at ? '恢复归档' : '归档'}
                </Button>
              </div>
            )}
            {skill.unavailable_reason && (
              <p
                className="mb-3 truncate rounded-lg bg-[#fbf4e9] px-3 py-2 text-xs"
                title={skill.unavailable_reason}
              >
                {skill.unavailable_reason}
              </p>
            )}
            <div className="mt-auto flex flex-wrap items-center gap-3 border-t border-line pt-3 text-sm">
              <button
                type="button"
                className="inline-flex items-center gap-1 text-brand"
                onClick={() => setDetail(skill)}
              >
                <BookOpen size={15} />
                查看详情
              </button>
              <button
                type="button"
                className="text-brand disabled:opacity-40"
                disabled={busy || !skill.managed || skill.protection_level === 'locked'}
                title={skill.protection_level === 'locked' ? '请先解锁' : undefined}
                onClick={() => {
                  setError('');
                  setEditor({ skill });
                }}
              >
                更新
              </button>
              <button
                type="button"
                className="inline-flex items-center gap-1 text-brand disabled:opacity-40"
                disabled={busy || !skill.managed}
                onClick={() => void download(skill)}
              >
                <Download size={15} />
                导出
              </button>
              <button
                type="button"
                className="ml-auto text-[#a44b40] disabled:opacity-40"
                disabled={busy || !skill.managed || skill.protection_level === 'locked'}
                title={skill.protection_level === 'locked' ? '请先解锁' : undefined}
                onClick={() => {
                  setError('');
                  setConfirm({ skill, action: 'remove' });
                }}
              >
                移除
              </button>
            </div>
            {!skill.managed && (
              <p className="mb-0 text-xs text-muted">部署接管尚未完成，包更新与移除暂不可用。</p>
            )}
          </article>
        ))}
      </div>
      {editor && (
        <SkillImportDialog
          skill={editor.skill}
          onClose={() => setEditor(null)}
          onSaved={(text) => {
            setNotice(text);
            setEditor(null);
            reload();
          }}
        />
      )}
      {detail && (
        <CapabilityDetails
          choice={{
            ...detail,
            id: detail.name,
            detail_url: `/api/skills/${encodeURIComponent(detail.name)}`,
          }}
          onClose={() => setDetail(null)}
        />
      )}
      {confirm && (
        <Dialog
          title={confirm.action === 'remove' ? '移除技能' : '停用技能'}
          busy={busy}
          onClose={() => setConfirm(null)}
          footer={
            <>
              <Button
                className="secondary button-secondary"
                disabled={busy}
                onClick={() => setConfirm(null)}
              >
                取消
              </Button>
              <Button
                disabled={busy}
                onClick={() =>
                  void (confirm.action === 'remove'
                    ? remove(confirm.skill)
                    : toggle(confirm.skill, false))
                }
              >
                {confirm.action === 'remove' ? '确认移除技能' : '确认停用技能'}
              </Button>
            </>
          }
        >
          <ErrorBox message={error} />
          <p>
            确认{confirm.action === 'remove' ? '移除' : '停用'}“{confirm.skill.display_name}”？
          </p>
          <p className="text-sm">
            新任务将不再加载它；已经读取它的任务会在后续执行前停止，历史记录和固定快照继续保留。
          </p>
          {confirm.action === 'remove' && (
            <p className="text-sm">
              当前技能包的文件会移除。如需保存副本，请先导出。重新导入不会恢复旧任务的许可。
            </p>
          )}
        </Dialog>
      )}
    </>
  );
}
