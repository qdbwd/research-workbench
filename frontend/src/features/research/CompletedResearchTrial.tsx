import { useState } from 'react';
import { useResource } from '../../shared/api/api';
import { Button } from '../../shared/ui/Button';
import { ErrorBox, Loading, Markdown } from '../../shared/ui/components';
import { DetailDialog, WorkbenchPanel } from '../../shared/ui/Workbench';
import { ResearchCommitMap } from './ResearchCommitMap';
import type { MapComparison } from './mapApi';

type FixedCommit = { id: string; message: string; branch: string };
type ExperimentCase = {
  id: string;
  gold_in_candidates: boolean;
  observed_context_has_gold: boolean;
  reranked_context_has_gold: boolean;
  observed_answer_correct: boolean;
};

type CompletedCase = {
  title: string;
  answer: {
    question: string;
    text: string;
    evidence: { id: string; text: string; title: string; version?: number; kind: 'knowledge' | 'material' }[];
  };
  task: { brief: { goal: string; scope: string; acceptance_criteria: string } };
  comparison: { common_commit_id: string; before: FixedCommit; after: FixedCommit; map: MapComparison };
  experiment: {
    specification: { question: string; input: unknown; script: string };
    receipt: {
      image: string; input_sha256: string; script_sha256: string; output: { cases: ExperimentCase[] };
    };
  };
  report: {
    content: { title: string; diagnosis: string; recommendations: string; limitations: string };
    review_reason: string;
  };
};

const caseLabels: Record<string, string> = {
  'old-version': '候选只含旧规程',
  'missing-from-context': '正确证据在组装上下文时被截断',
  'visible-misused': '正确证据已在上下文，原回答仍误用',
};

function CaseContent({ item }: { item: CompletedCase }) {
  const { report, answer, comparison, experiment } = item;
  return <div className="grid gap-4">
    <div>
      <h3 className="m-0 text-xl font-semibold">{item.title}</h3>
      <p className="mt-2 mb-0 text-sm text-control">
        模拟公司资料 · 确定性模型替身 · 实际隔离容器实验
      </p>
    </div>
    <WorkbenchPanel title={report.content.title} description="已通过案例验收">
      <div className="grid gap-3 text-sm leading-relaxed">
        <p className="m-0 font-semibold">{report.content.recommendations}</p>
        <p className="m-0">{report.content.diagnosis}</p>
        <p className="m-0 text-control">{report.content.limitations}</p>
        <p className="m-0 text-control">{report.review_reason}</p>
      </div>
    </WorkbenchPanel>
    <WorkbenchPanel title="研发简报">
      <dl className="m-0 grid gap-2 text-sm leading-relaxed">
        <div><dt className="font-semibold">研究目标</dt><dd className="m-0">{item.task.brief.goal}</dd></div>
        {item.task.brief.scope && <div><dt className="font-semibold">范围</dt><dd className="m-0">{item.task.brief.scope}</dd></div>}
        {item.task.brief.acceptance_criteria && <div><dt className="font-semibold">验收要求</dt>
          <dd className="m-0">{item.task.brief.acceptance_criteria}</dd></div>}
      </dl>
    </WorkbenchPanel>
    <WorkbenchPanel title="带出处的问答">
      <p className="mt-0 text-sm font-semibold">{answer.question}</p>
      <Markdown text={answer.text} embeddedEvidence />
      <div className="mt-3 grid gap-2 text-sm">
        {answer.evidence.map((ref) => <div key={ref.id} id={'evidence-' + ref.id}
          className="scroll-mt-4 rounded-lg bg-canvas px-3 py-2.5 target:ring-2 target:ring-brand">
          <strong>{ref.title}</strong>
          <span className="ml-2 text-xs text-control">{ref.kind === 'knowledge' ? '已发布知识' : '导入材料'}
            {ref.version != null && <> · 版本 {ref.version}</>}
          </span>
          <p className="my-1 whitespace-pre-wrap [overflow-wrap:anywhere]">{ref.text}</p>
        </div>)}
      </div>
    </WorkbenchPanel>
    <WorkbenchPanel title="分支对照" description="两条分支均从固定的试点资料和问题出发。">
      <div className="grid gap-2 text-sm">
        {[comparison.before, comparison.after].map((entry) => <div key={entry.id} className="flex flex-wrap items-baseline gap-x-2">
          <strong>{entry.branch}</strong><span>{entry.message}</span><code>{entry.id.slice(0, 8)}</code>
        </div>)}
        <p className="m-0 text-xs text-control">共同起点 · <code>{comparison.common_commit_id.slice(0, 8)}</code></p>
      </div>
    </WorkbenchPanel>
    <ResearchCommitMap map={comparison.map.after} taskTitle={item.title} difference={comparison.map}
      renderEvidence={(ref) => <a key={ref.id} href="#trial-experiment">{ref.title ?? '查看固定实验依据'}</a>} />
    <div id="trial-experiment">
      <WorkbenchPanel title="独立实验" description={experiment.specification.question}>
        <ul className="mt-0 grid gap-2 pl-4 text-sm">
          {experiment.receipt.output.cases.map((row) => <li key={row.id}>
            <strong>{caseLabels[row.id] ?? row.id}</strong>
            <p className="mt-1 mb-0 text-control">
              包含正确证据的阶段 · 候选 {row.gold_in_candidates ? '有' : '无'} · 原上下文 {row.observed_context_has_gold ? '有' : '无'}
              {' · '}重排后 {row.reranked_context_has_gold ? '有' : '无'} · 原回答{row.observed_answer_correct ? '正确' : '错误'}
            </p>
          </li>)}
        </ul>
        <DetailDialog title="查看固定回执">
          <dl className="m-0 grid gap-2 text-sm [overflow-wrap:anywhere]">
            <div><dt className="font-semibold">执行镜像</dt><dd className="m-0 font-mono">{experiment.receipt.image}</dd></div>
            <div><dt className="font-semibold">输入 SHA256</dt><dd className="m-0 font-mono">{experiment.receipt.input_sha256}</dd></div>
            <div><dt className="font-semibold">脚本 SHA256</dt><dd className="m-0 font-mono">{experiment.receipt.script_sha256}</dd></div>
          </dl>
          <h3 className="mt-4 text-sm">沙盒结果</h3>
          <pre className="max-w-full overflow-auto rounded-xl bg-canvas p-3 text-xs">{JSON.stringify(experiment.receipt.output, null, 2)}</pre>
          <h3 className="mt-4 text-sm">固定输入</h3>
          <pre className="max-w-full overflow-auto rounded-xl bg-canvas p-3 text-xs">{JSON.stringify(experiment.specification.input, null, 2)}</pre>
          <h3 className="mt-4 text-sm">固定脚本</h3>
          <pre className="max-w-full overflow-auto rounded-xl bg-canvas p-3 text-xs">{experiment.specification.script}</pre>
        </DetailDialog>
      </WorkbenchPanel>
    </div>
  </div>;
}

export function CompletedResearchTrial() {
  const [open, setOpen] = useState(false);
  const [retry, setRetry] = useState(0);
  const resource = useResource<CompletedCase>(open ? '/api/research-trial/completed' : null, retry);
  return <DetailDialog title="查看已完成案例" onOpenChange={setOpen}>
    {resource.loading && <Loading />}
    {resource.error && <>
      <ErrorBox message={resource.error} />
      <Button type="button" className="button-secondary" onClick={() => setRetry(value => value + 1)}>重试</Button>
    </>}
    {resource.data && <CaseContent item={resource.data} />}
  </DetailDialog>;
}

