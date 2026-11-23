import { useRef, useState } from 'react';
import { useBeforeUnload, useBlocker } from 'react-router-dom';
import { ApiError, api, messageOf, send } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { ErrorBox } from '../../shared/ui/components';
import { WorkbenchPanel } from '../../shared/ui/Workbench';
import { Dialog } from '../../shared/ui/Dialog';
import { briefFields } from './ResearchBrief';
import type { Brief } from './ResearchBrief';
import { researchApi } from './api';
import type { ResearchTask } from './api';

const delta = (base: Brief, values: Brief): Partial<Brief> => {
  const result: Partial<Brief> = {};
  for (const { name } of briefFields) {
    if (base[name] !== values[name].trim()) result[name] = values[name].trim();
  }
  return result;
};

export function ResearchBriefEditor({
  initial,
  endpoint,
  onSaved,
  onObserved,
  onCancel,
}: {
  initial: ResearchTask;
  endpoint: string;
  onSaved: (task: ResearchTask) => void;
  onObserved: (task: ResearchTask) => void;
  onCancel: () => void;
}) {
  const [base, setBase] = useState(initial);
  const [values, setValues] = useState(initial.head.state.brief);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [conflict, setConflict] = useState(false);
  const [ambiguous, setAmbiguous] = useState(false);
  const [current, setCurrent] = useState<ResearchTask | null>(null);
  const [discard, setDiscard] = useState(false);
  const operation = useRef<{ signature: string; key: string } | null>(null);
  const patch = delta(base.head.state.brief, values);
  const changed = Object.keys(patch).length > 0;
  const dirty = changed || Boolean(reason.trim()) || busy;
  const blocker = useBlocker(dirty);
  useBeforeUnload((event) => {
    if (dirty) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  async function save() {
    if (busy || !changed || !values.goal.trim() || !reason.trim() || conflict) return;
    const body = {
      expected_revision_id: base.head.id,
      expected_control_version: base.control_version,
      changes: { brief: patch },
      reason: reason.trim(),
    };
    const signature = JSON.stringify(body);
    if (operation.current?.signature !== signature)
      operation.current = { signature, key: crypto.randomUUID() };
    setBusy(true);
    setNotice('');
    try {
      const saved = await send<ResearchTask>(endpoint, {
        ...body,
        operation_id: operation.current.key,
      });
      onSaved(saved);
    } catch (failure) {
      setNotice(messageOf(failure));
      setConflict(
        failure instanceof ApiError &&
          ['revision_conflict', 'control_conflict'].includes(failure.code ?? ''),
      );
      // 丢失响应时，同一份提交会沿用原身份，让服务器读取原回执。
      setAmbiguous(!(failure instanceof ApiError) || failure.status >= 500);
    } finally {
      setBusy(false);
    }
  }

  async function loadCurrent() {
    setBusy(true);
    setNotice('');
    try {
      const latest = await api<ResearchTask>(researchApi(base.id));
      setCurrent(latest);
      onObserved(latest);
    } catch (failure) {
      setNotice(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  function rebase() {
    if (!current) return;
    setValues({ ...current.head.state.brief, ...patch });
    setBase(current);
    setCurrent(null);
    setConflict(false);
    setNotice('');
    setAmbiguous(false);
  }

  return (
    <WorkbenchPanel
      title="编辑简报"
      description={`基于版本 ${base.head.number} 编辑，保存时产生一份可回看的新修订。`}
    >
      <ErrorBox message={notice} />
      {conflict && !current && (
        <Button
          className="button-secondary mb-4"
          disabled={busy}
          onClick={() => void loadCurrent()}
        >
          {busy ? '正在读取…' : '核对最新版'}
        </Button>
      )}
      {current && (
        <section
          className="mb-5 rounded-xl border border-line bg-canvas p-4"
          aria-label="对照最新版"
        >
          <h3 className="mt-0 text-sm font-semibold">最新版 · 版本 {current.head.number}</h3>
          <dl className="grid gap-3 text-sm">
            {briefFields
              .filter(({ name }) => name in patch)
              .map(({ name, label }) => (
                <div key={name} className="grid gap-2 md:grid-cols-[8rem_1fr]">
                  <dt className="font-semibold">{label}</dt>
                  <dd className="m-0 whitespace-pre-wrap [overflow-wrap:anywhere]">
                    {current.head.state.brief[name] || '未填写'}
                  </dd>
                </div>
              ))}
          </dl>
          <Button className="button-secondary" onClick={rebase}>
            保留草稿并使用最新基线
          </Button>
          <p className="mb-0 text-sm text-control">
            保留你编辑过的字段，其余字段采用最新版。请确认后再保存。
          </p>
        </section>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <fieldset disabled={busy} className="m-0 min-w-0 space-y-4 border-0 p-0">
          <div className="grid gap-4 md:grid-cols-2">
            {briefFields.map(({ name, label, max }) => (
              <label
                key={name}
                className={`grid min-w-0 gap-2 text-sm ${name === 'goal' ? 'md:col-span-2' : ''}`}
              >
                <span className="font-semibold">{label}</span>
                <textarea
                  rows={3}
                  required={name === 'goal'}
                  maxLength={max}
                  value={values[name]}
                  onChange={(event) => setValues((old) => ({ ...old, [name]: event.target.value }))}
                />
              </label>
            ))}
          </div>
          <label className="grid gap-2 text-sm">
            <span className="font-semibold">修订说明</span>
            <input
              required
              maxLength={1000}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="简要说明这次调整的原因"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              type="submit"
              disabled={!changed || !reason.trim() || !values.goal.trim() || conflict}
            >
              {busy ? '正在保存…' : ambiguous ? '重试保存' : '保存修订'}
            </Button>
            <Button
              type="button"
              className="button-secondary"
              onClick={() => (dirty ? setDiscard(true) : onCancel())}
            >
              取消编辑
            </Button>
          </div>
        </fieldset>
      </form>
      {(discard || blocker.state === 'blocked') && !busy && (
        <Dialog
          title="放弃未保存的修改？"
          onClose={() => {
            setDiscard(false);
            if (blocker.state === 'blocked') blocker.reset();
          }}
          footer={
            <>
              <Button
                className="button-secondary"
                onClick={() => {
                  setDiscard(false);
                  if (blocker.state === 'blocked') blocker.reset();
                }}
              >
                继续编辑
              </Button>
              <Button
                onClick={() => {
                  if (blocker.state === 'blocked') blocker.proceed();
                  else onCancel();
                }}
              >
                放弃修改
              </Button>
            </>
          }
        >
          本次输入还没有保存。已提交的历史版本仍可查看。
        </Dialog>
      )}
    </WorkbenchPanel>
  );
}
