import { useId, useRef, useState } from 'react';
import { ApiError, messageOf, send } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { Dialog } from '../../shared/ui/Dialog';
import { ErrorBox } from '../../shared/ui/components';
import { researchApi, researchCapabilities, researchPhases } from './api';
import type { ResearchCapability, ResearchPhase, ResearchTask } from './api';

export function ResearchFocus({ task, onClose, onSaved, onRefresh }: {
  task: ResearchTask;
  onClose: () => void;
  onSaved: (task: ResearchTask) => void;
  onRefresh: () => void;
}) {
  const formId = useId();
  const [phase, setPhase] = useState(task.head.state.phase);
  const [capabilities, setCapabilities] = useState(task.head.state.active_capabilities ?? []);
  const [pinned, setPinned] = useState(task.head.state.capability_override != null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const attempt = useRef<{ signature: string; id: string } | null>(null);

  async function save() {
    const body = {
      phase, capabilities, pinned, reason: reason.trim(),
      expected_revision_id: task.head.id,
      expected_control_version: task.control_version,
    };
    const signature = JSON.stringify(body);
    if (attempt.current?.signature !== signature) {
      attempt.current = { signature, id: crypto.randomUUID() };
    }
    setBusy(true);
    setError('');
    try {
      onSaved(await send<ResearchTask>(`${researchApi(task.id)}/focus`, {
        ...body, operation_id: attempt.current.id,
      }));
    } catch (failure) {
      setError(messageOf(failure));
      setStale(failure instanceof ApiError && failure.status === 409);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title="调整研究焦点" onClose={onClose} busy={busy} footer={<>
      <Button type="button" className="button-secondary" disabled={busy} onClick={onClose}>返回</Button>
      {stale
        ? <Button type="button" className="button-secondary" onClick={onRefresh}>核对最新版本</Button>
        : <Button type="submit" form={formId} disabled={busy || !reason.trim()}>
            {busy ? '正在保存…' : '保存研究焦点'}
          </Button>}
    </>}>
      <form id={formId} className="space-y-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <label>研究阶段
          <select value={phase} disabled={busy} onChange={(event) => setPhase(event.target.value as ResearchPhase)}>
            {Object.entries(researchPhases).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <fieldset className="rounded-lg border border-line p-3">
          <legend className="px-1 text-sm font-semibold">当前方法</legend>
          {Object.entries(researchCapabilities).map(([value, label]) => {
            const capability = value as ResearchCapability;
            return <label key={value} className="flex-row items-center gap-2">
              <input type="checkbox" disabled={busy} checked={capabilities.includes(capability)}
                onChange={(event) => setCapabilities(event.target.checked
                  ? [...capabilities, capability] : capabilities.filter((item) => item !== capability))} />
              {label}
            </label>;
          })}
        </fieldset>
        <label className="flex-row items-center gap-2">
          <input type="checkbox" checked={pinned} disabled={busy} onChange={(event) => setPinned(event.target.checked)} />
          固定选择，由我调整方法
        </label>
        <label>调整理由
          <textarea required maxLength={1000} value={reason} disabled={busy} onChange={(event) => setReason(event.target.value)} />
        </label>
      </form>
      <ErrorBox message={error} />
    </Dialog>
  );
}
