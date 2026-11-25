import { FlaskConical, MessageSquare, Search } from 'lucide-react';
import { Button } from '../../shared/ui/Button';
import { CompletedResearchTrial } from '../research/CompletedResearchTrial';

export type TrialPrompt = { id: string; mode: 'chat' | 'research'; text: string; label: string };
export type TrialCatalog = {
  key: string; title: string; notice: string; prompts: TrialPrompt[];
  knowledge: string[]; materials: string[];
};

const icons = { answer: MessageSquare, diagnosis: Search, research: FlaskConical };

export function ResearchTrialStarter({ catalog, selected, disabled, onChoose, onLeave }: {
  catalog: TrialCatalog; selected: boolean; disabled: boolean;
  onChoose: (prompt: TrialPrompt) => void; onLeave: () => void;
}) {
  return <section aria-label="研发试用" className="mb-3 grid gap-2.5">
    <div className="flex flex-wrap gap-2">
      {catalog.prompts.map((prompt) => {
        const Icon = icons[prompt.id as keyof typeof icons] ?? MessageSquare;
        return <Button key={prompt.id} type="button" className="button-secondary text-xs" disabled={disabled}
          onClick={() => onChoose(prompt)}><Icon size={14} aria-hidden="true" /> {prompt.label}</Button>;
      })}
    </div>
    <CompletedResearchTrial />
    {selected && <div className="grid gap-2 rounded-xl border border-line bg-white px-4 py-3 text-xs text-control">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <strong className="text-ink">{catalog.title}</strong>
        <Button type="button" className="button-quiet text-xs" disabled={disabled} onClick={onLeave}>退出试用</Button>
      </div>
      <p className="m-0">{catalog.notice} 发送后使用一份独立副本。</p>
      <dl className="m-0 grid gap-1">
        <div className="flex flex-wrap gap-x-2"><dt className="font-semibold">模拟已发布知识</dt>
          <dd className="m-0 flex flex-wrap gap-x-2">{catalog.knowledge.map((title) => <span key={title}>{title}</span>)}</dd></div>
        <div className="flex flex-wrap gap-x-2"><dt className="font-semibold">模拟导入材料</dt>
          <dd className="m-0 flex flex-wrap gap-x-2">{catalog.materials.map((title) => <span key={title}>{title}</span>)}</dd></div>
      </dl>
    </div>}
  </section>;
}
