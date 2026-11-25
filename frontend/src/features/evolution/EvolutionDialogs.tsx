import { useRef, useState, type FormEvent } from 'react';
import { messageOf, send, useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import type { MemorySlot, SlotUsage } from './SlotCapacityBar';

const defaults = {
  auto_review_enabled: true,
  memory_auto_write: true,
  skill_auto_write: true,
  user_slot_char_limit: 2400,
  system_slot_char_limit: 3600,
  review_turn_threshold: 6,
  skill_tool_threshold: 8,
  curator_stale_days: 30,
  daily_budget_requests: 20,
};
export type EvolutionSettings = typeof defaults & {
  revision: number;
  slots: Record<MemorySlot, SlotUsage>;
  daily_used: number;
  daily_remaining: number;
};
const switches = [
  ['auto_review_enabled', '自动后台复盘'],
  ['memory_auto_write', '记忆默认自动写入'],
  ['skill_auto_write', '技能默认自动发布'],
] as const;
const limits = [
  ['user_slot_char_limit', '用户画像上限（字）', 200, 20000],
  ['system_slot_char_limit', '环境与系统知识上限（字）', 200, 30000],
  ['review_turn_threshold', '记忆复盘轮次阈值', 1, 100],
  ['skill_tool_threshold', '技能工具调用阈值', 1, 100],
  ['curator_stale_days', '整理过时天数', 7, 365],
  ['daily_budget_requests', '每日复盘入队上限', 0, 500],
] as const;

export function EvolutionSettingsDialog({
  data,
  onClose,
  onSaved,
  onReload,
}: {
  data: EvolutionSettings;
  onClose: () => void;
  onSaved: () => void;
  onReload: () => void;
}) {
  const [draft, setDraft] = useState(() => ({
    ...defaults,
    ...Object.fromEntries(
      Object.keys(defaults).flatMap((key) => {
        const value = data[key as keyof typeof defaults];
        return value === undefined ? [] : [[key, value]];
      }),
    ),
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await send(
        '/api/evolution/settings',
        { expected_revision: data.revision, ...draft },
        'PATCH',
      );
      onSaved();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      title="自进化设置"
      busy={busy}
      onClose={onClose}
      footer={
        <>
          <Button type="button" className="button-secondary" disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button type="submit" form="evolution-settings-form" disabled={busy}>
            保存自进化设置
          </Button>
        </>
      }
    >
      <form
        id="evolution-settings-form"
        onSubmit={(event) => void save(event)}
        className="grid gap-3"
      >
        <fieldset disabled={busy} className="grid min-w-0 gap-2">
          {switches.map(([key, label]) => (
            <label key={key} className="check flex-row items-center gap-2">
              <input
                type="checkbox"
                checked={draft[key]}
                onChange={(event) => setDraft({ ...draft, [key]: event.target.checked })}
              />
              {label}
            </label>
          ))}
        </fieldset>
        <fieldset disabled={busy} className="grid min-w-0 gap-3 md:grid-cols-2">
          {limits.map(([key, label, min, max]) => (
            <label key={key} className="min-w-0">
              {label}
              <input
                type="number"
                min={min}
                max={max}
                required
                value={draft[key]}
                onChange={(event) => setDraft({ ...draft, [key]: Number(event.target.value) })}
              />
            </label>
          ))}
        </fieldset>
        <ErrorBox message={error} />
        {error && (
          <Button type="button" className="button-secondary justify-self-start" onClick={onReload}>
            重新载入设置
          </Button>
        )}
      </form>
    </Dialog>
  );
}

type ReviewType = 'memory_review' | 'skill_review' | 'combined_review';
export function ManualReviewDialog({
  initialType = 'memory_review',
  onClose,
  onQueued,
}: {
  initialType?: ReviewType;
  onClose: () => void;
  onQueued: () => void;
}) {
  const conversations = useResource<{ id: string; title: string }[]>(
    '/api/conversations?include_trials=false',
  );
  const [conversationId, setConversationId] = useState('');
  const [jobType, setJobType] = useState<ReviewType>(initialType);
  const [focus, setFocus] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<{ fingerprint: string; key: string } | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const fingerprint = JSON.stringify([conversationId, jobType, focus.trim()]);
    const key =
      request.current?.fingerprint === fingerprint ? request.current.key : crypto.randomUUID();
    request.current = { fingerprint, key };
    try {
      await send('/api/evolution/reviews', {
        conversation_id: conversationId,
        job_type: jobType,
        focus_hint: focus.trim(),
        request_key: key,
      });
      onQueued();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      title={initialType === 'skill_review' ? '立即复盘（技能）' : '立即复盘'}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <Button type="button" className="button-secondary" disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button type="submit" form="manual-review-form" disabled={busy || !conversationId}>
            发起复盘
          </Button>
        </>
      }
    >
      <form id="manual-review-form" className="grid gap-3" onSubmit={(event) => void submit(event)}>
        <label>
          复盘会话
          <select
            required
            value={conversationId}
            disabled={busy}
            onChange={(e) => setConversationId(e.target.value)}
          >
            <option value="">选择会话</option>
            {conversations.data?.map(({ id, title }) => (
              <option key={id} value={id}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <label>
          复盘范围
          <select
            value={jobType}
            disabled={busy}
            onChange={(e) => setJobType(e.target.value as ReviewType)}
          >
            <option value="memory_review">长期记忆</option>
            <option value="skill_review">技能</option>
            <option value="combined_review">长期记忆与技能</option>
          </select>
        </label>
        <label>
          关注点（可选）
          <input
            value={focus}
            maxLength={500}
            disabled={busy}
            onChange={(e) => setFocus(e.target.value)}
          />
        </label>
        <ErrorBox message={error || conversations.error} />
      </form>
    </Dialog>
  );
}
