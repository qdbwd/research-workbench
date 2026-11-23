export type MapEvidence = {
  id: string;
  title?: string;
  quote: string;
  url: string | null;
  available: boolean;
  start?: number;
  end?: number;
  offset_unit?: 'characters' | 'lines';
  page?: number | null;
};

export const nodeStatuses = {
  open: '待研究',
  investigating: '调研中',
  blocked: '受阻',
  resolved: '已解决',
  deferred: '已暂缓',
  needs_review: '待复核',
} as const;

export type ResearchNode = {
  id: string;
  parent_id: string | null;
  required: boolean;
  question: string;
  status: keyof typeof nodeStatuses;
  criteria: string;
  reason: string;
  conclusion: string;
  details: string;
  evidence_ids: string[];
  related: { target_id: string; description: string }[];
  evidence: MapEvidence[];
};

export type ResearchMap = {
  revision_id: string;
  number: number;
  nodes: ResearchNode[];
};

export type MapComparison = {
  before: ResearchMap;
  after: ResearchMap;
  changes: { id: string; types: string[] }[];
};

export type AdoptionPreview = {
  source_revision_id: string;
  target_revision_id: string;
  node_id: string;
  before: ResearchNode;
  after: ResearchNode;
  preview_hash: string;
  evidence: MapEvidence[];
};
