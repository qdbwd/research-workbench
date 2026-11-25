export const slotLabels = { user: '用户画像', system: '环境与系统知识' };
export type MemorySlot = keyof typeof slotLabels;

export type SlotUsage = { character_count: number; entry_count: number; char_limit: number };

export function SlotCapacityBar({
  slot,
  usage,
  selected,
  onSelect,
}: {
  slot: MemorySlot;
  usage: SlotUsage;
  selected: boolean;
  onSelect: () => void;
}) {
  const { character_count: count, entry_count: entries, char_limit: limit } = usage;
  const percent = (count / limit) * 100;
  const value = Math.min(count, limit);
  const state = percent >= 100 ? '已达上限' : percent >= 85 ? '接近上限' : '';
  const color = percent >= 100 ? 'bg-danger' : percent >= 85 ? 'bg-[#c0a76b]' : 'bg-brand';
  return (
    <div className="min-w-0 rounded-xl bg-canvas px-3.5 py-3">
      <button
        type="button"
        className="rounded-full px-2 py-1 text-sm font-semibold text-brand aria-pressed:bg-[#e3f1e8]"
        aria-pressed={selected}
        onClick={onSelect}
      >
        {slotLabels[slot]}
      </button>
      <div
        role="progressbar"
        aria-label={`${slotLabels[slot]}容量`}
        aria-valuemin={0}
        aria-valuenow={value}
        aria-valuemax={limit}
        aria-valuetext={`${count} / ${limit} 字，${entries} 条${state ? '，' + state : ''}`}
        className="my-2 h-2 overflow-hidden rounded-full bg-[#dfe5dd]"
      >
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
      <p className="m-0 text-xs text-control">
        {count} / {limit} 字 · {entries} 条{state && ` · ${state}`}
      </p>
    </div>
  );
}
