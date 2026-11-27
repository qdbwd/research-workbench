export function formatDuration(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${Number((ms / 1000).toFixed(2))}s`;
}

export const runKindLabels = {
  agent: '智能体任务',
  material: '材料解析',
  context_compaction: '上下文压缩',
  memory_classification: '记忆分类',
  evolution_review: '后台复盘',
  research_start: '研发任务登记',
};
