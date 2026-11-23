import { WorkbenchPanel } from '../../shared/ui/Workbench';
import { Link } from 'react-router-dom';
import type { ResearchRevision } from './api';

export const briefFields = [
  { name: 'goal', label: '研发目标', max: 2000 },
  { name: 'scope', label: '研究范围', max: 4000 },
  { name: 'constraints', label: '约束条件', max: 4000 },
  { name: 'acceptance_criteria', label: '验收标准', max: 4000 },
  { name: 'key_decisions', label: '关键决策', max: 4000 },
  { name: 'blockers', label: '当前阻塞', max: 4000 },
  { name: 'next_action', label: '下一步', max: 4000 },
] as const;

export type Brief = Record<(typeof briefFields)[number]['name'], string>;

export const sampleBrief: Brief = {
  goal: '比较内部文档检索方案',
  scope: '用现有技术文档和人工标注问题核对召回结果。',
  constraints: '沿用现有 PostgreSQL，离线运行评测。',
  acceptance_criteria: '比较现有关键词检索与候选方案，记录每条漏检的原因。',
  key_decisions: '先建立可复现的关键词检索基线。',
  blockers: '',
  next_action: '整理包含缩写、别名和跨段引用的十个查询。',
};

export function ResearchBrief({ brief, title = '研发简报', materials }: {
  brief: Brief;
  title?: string;
  materials?: ResearchRevision['state']['material_refs'];
}) {
  return (
    <WorkbenchPanel title={title}>
      <dl className="m-0 grid min-w-0 gap-x-8 gap-y-4 md:grid-cols-2">
        {briefFields.map(({ name, label }, i) => (
          <div
            key={name}
            className={i === 0 ? 'border-b border-line pb-4 md:col-span-2' : 'min-w-0'}
          >
            <dt className="text-sm font-semibold">{label}</dt>
            <dd className="mt-1.5 mb-0 ml-0 text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
              {brief[name] || <span className="text-control">未填写</span>}
            </dd>
          </div>
        ))}
      </dl>
      {Boolean(materials?.length) && (
        <section className="mt-4 border-t border-line pt-4">
          <h3 className="m-0 text-sm font-semibold">启动时关联的材料</h3>
          <ul className="my-2 grid gap-2 text-sm">
            {materials?.map((item) => (
              <li key={item.source_version_id}>
                <Link to={`/sources/${encodeURIComponent(item.source_id)}?version=${encodeURIComponent(item.source_version_id)}`}>{item.title}</Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </WorkbenchPanel>
  );
}
