import type { Brief } from './ResearchBrief';

export const researchPhases = {
  exploration: '探索阶段',
  investigation: '调研阶段',
  validation: '验证阶段',
  synthesis: '综合阶段',
} as const;
export type ResearchPhase = keyof typeof researchPhases;

export const researchCapabilities = {
  product_design: '产品设计',
  technical_research: '技术调研',
  experiment_analysis: '实验分析',
} as const;
export type ResearchCapability = keyof typeof researchCapabilities;

export type RevisionSummary = {
  id: string;
  number: number;
  reason: string;
  created_at: string;
};

export type ResearchRevision = RevisionSummary & {
  branch_id: string;
  parent_revision_id: string | null;
  task_removed?: boolean;
  state: {
    brief: Brief;
    phase: ResearchPhase;
    active_capabilities?: ResearchCapability[];
    capability_override?: ResearchCapability[] | null;
    material_refs?: { source_id: string; source_version_id: string; title: string }[];
  };
};

export type ResearchSummary = {
  id: string;
  title: string;
  home_conversation_id: string | null;
  created_at: string;
  status: string;
  control_version: number;
};

export type ResearchTask = ResearchSummary & {
  main_branch_id: string;
  head: ResearchRevision;
};

export const researchStatuses: Record<string, string> = {
  open: '待推进', paused: '已暂停', closed: '已取消', awaiting_acceptance: '待验收', completed: '已完成',
};

export const researchApi = (id: string) => `/api/research-tasks/${encodeURIComponent(id)}`;
export const researchPath = (id: string) => `/research/${encodeURIComponent(id)}`;
export const revisionSuffix = (branch: string) =>
  `/branches/${encodeURIComponent(branch)}/revisions`;
