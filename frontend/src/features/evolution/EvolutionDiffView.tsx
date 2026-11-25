import { slotLabels } from './SlotCapacityBar';

type Values = Record<string, string | number>;
export type EvolutionDiff = {
  before?: Values;
  after?: Values;
  reason?: string;
  reason_code?: string;
  rollback_revision?: number;
  unified_diff?: string;
};
const fields = [
  ['content', '正文'],
  ['memory_slot', '槽位'],
  ['scope', '作用域'],
  ['topic', '主题'],
  ['category', '类别'],
  ['status', '状态'],
];
const categoryLabels: Record<string, string> = {
  preference: '表达偏好',
  background: '用户背景',
  behavior: '协作习惯',
  cognitive: '思考与学习习惯',
  goal: '持续目标',
  correction: '明确纠正',
};

function displayValue(key: string, value: string | number | undefined) {
  if (key === 'memory_slot' && (value === 'user' || value === 'system')) return slotLabels[value];
  if (key === 'scope') return value === 'global' ? '全局' : value === 'topic' ? '指定主题' : '—';
  if (key === 'category') return categoryLabels[String(value)] ?? (value || '—');
  if (key === 'status')
    return (
      ({ active: '已生效', inactive: '已停用', archived: '已归档' } as Record<string, string>)[
        String(value)
      ] ?? '—'
    );
  return value || '—';
}

export function EvolutionDiffView({ diff }: { diff?: EvolutionDiff | null }) {
  const { before = {}, after = {}, unified_diff } = diff ?? {};
  if (unified_diff)
    return (
      <pre
        aria-label="技能正文差异"
        className="m-0 max-w-full overflow-auto whitespace-pre-wrap rounded-xl border border-line bg-canvas p-3 text-xs [overflow-wrap:anywhere]"
      >
        {unified_diff.split('\n').map((line, index) => (
          <span
            key={index}
            className={`block ${
              line.startsWith('+') && !line.startsWith('+++')
                ? 'bg-[#e3f1e8] text-[#185940]'
                : line.startsWith('-') && !line.startsWith('---')
                  ? 'bg-[#fbeae8] text-[#a03e36]'
                  : ''
            }`}
          >
            {line || '\u00a0'}
          </span>
        ))}
      </pre>
    );
  if (!Object.keys(after).length) return null;
  return (
    <dl className="my-2 grid min-w-0 gap-2 text-sm">
      {fields
        .filter(([key]) => key in after)
        .map(([key, label]) => (
          <div
            key={key}
            className="grid min-w-0 gap-1 md:grid-cols-[5rem_minmax(0,1fr)_minmax(0,1fr)]"
          >
            <dt className="font-medium">{label}</dt>
            <dd className="m-0 whitespace-pre-wrap rounded-lg bg-canvas p-2 [overflow-wrap:anywhere]">
              修改前 {displayValue(key, before[key])}
            </dd>
            <dd className="m-0 whitespace-pre-wrap rounded-lg bg-[#e3f1e8] p-2 [overflow-wrap:anywhere]">
              修改后 {displayValue(key, after[key])}
            </dd>
          </div>
        ))}
    </dl>
  );
}
