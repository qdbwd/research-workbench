import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';
import { useResource } from '../../shared/api/api';
import { WorkbenchPanel } from '../../shared/ui/Workbench';
import type { Run } from '../../shared/types/types';
import { LiveRunPanel } from '../runs/LiveRunPanel';
import { EmbeddedResearchMap } from './ResearchMapPage';
import { RevisionHistory } from './ResearchPage';
import { ResearchCommits } from './ResearchCommits';
import { ResearchControls } from './ResearchControls';
import { researchApi, researchPath, researchPhases, researchStatuses, revisionSuffix, type ResearchTask } from './api';
import type { ResearchMap } from './mapApi';

const sections = [
  ['brief', '简报'], ['map', '研发地图'], ['history', '分支与提交'],
] as const;

export function ResearchChatPane({
  task,
  onChanged,
  activeRun,
  onRunChanged,
  onOpenChat,
}: {
  task: ResearchTask;
  onChanged: (task: ResearchTask) => void;
  activeRun?: Run | null;
  onRunChanged?: () => void;
  onOpenChat?: () => void;
}) {
  const [section, setSection] = useState<(typeof sections)[number][0]>('brief');
  const [mapFocus, setMapFocus] = useState<string>();
  const [showRevisions, setShowRevisions] = useState(false);
  const map = useResource<ResearchMap>(section === 'brief'
    ? `${researchApi(task.id)}/map?revision_id=${encodeURIComponent(task.head.id)}` : null);
  const { brief, phase } = task.head.state;
  const focus = map.data?.nodes.find((node) => node.status === 'investigating');
  const supported = map.data?.nodes.find((node) => node.conclusion && node.evidence.some((item) => item.available));
  const fields = [
    ['研发目标', brief.goal], ['当前重点', focus?.question || researchPhases[phase]],
    ['有依据的结论', supported?.conclusion], ['下一步', brief.next_action], ['当前阻塞', brief.blockers],
  ];

  return <div className="mx-auto w-full max-w-6xl p-6 max-md:p-3">
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <h1 className="m-0 text-2xl font-semibold [overflow-wrap:anywhere]">{task.title}</h1>
        <p className="mt-1 mb-0 text-xs text-control">{researchStatuses[task.status] ?? task.status}</p>
      </div>
      <Link to={researchPath(task.id)} className="button button-secondary text-xs">
        <ExternalLink size={14} aria-hidden="true" /> 打开研发工作台
      </Link>
    </div>
    <nav aria-label="研发任务页面" className="mb-4 flex flex-wrap gap-1.5 border-b border-line pb-2">
      {sections.map(([id, label]) => <button type="button" className="research-pane-section rounded-lg px-3 py-1.5 text-sm"
        aria-pressed={section === id} key={id} onClick={() => { setMapFocus(undefined); setSection(id); }}>{label}</button>)}
    </nav>
    {activeRun && onRunChanged && (
      <LiveRunPanel key={`research-live-${activeRun.id}`} initial={activeRun} onChanged={onRunChanged} onOpenChat={onOpenChat} />
    )}
    {section === 'brief' && <WorkbenchPanel title="研发简报">
      <dl className="m-0 grid gap-4">
        {fields.map(([label, value]) => <div key={label} className="grid gap-1 sm:grid-cols-[7rem_minmax(0,1fr)]">
          <dt className="text-sm font-semibold">{label}</dt>
          <dd className="m-0 whitespace-pre-wrap text-sm [overflow-wrap:anywhere]">
            {value || <span className="text-control">暂无</span>}
            {label === '有依据的结论' && supported && <button type="button" className="button-quiet ml-2 text-xs"
              onClick={() => { setMapFocus(supported.id); setSection('map'); }}>查看地图依据</button>}
          </dd>
        </div>)}
      </dl>
      {map.error && <p role="status" className="mt-4 text-xs text-control">暂时读不到地图结论，请在研发地图重试。</p>}
      <div className="mt-4 border-t border-line pt-4">
        <ResearchControls task={task} onChanged={onChanged} />
      </div>
    </WorkbenchPanel>}
    {section === 'map' && <EmbeddedResearchMap taskId={task.id} initialNodeId={mapFocus} />}
    {section === 'history' && <div className="grid gap-4">
      <ResearchCommits task={task} onChanged={onChanged} />
      <div>
        <button type="button" className="button button-secondary text-sm" aria-expanded={showRevisions}
          onClick={() => setShowRevisions((value) => !value)}>自动修订记录</button>
        {showRevisions && <div className="mt-2"><RevisionHistory key={task.head.branch_id} task={task} endpoint={researchApi(task.id) + revisionSuffix(task.write_branch_id ?? task.main_branch_id)} /></div>}
      </div>
    </div>}
  </div>;
}
