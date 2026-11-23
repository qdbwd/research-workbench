import { NavLink } from 'react-router-dom';

export function KnowledgeTabs() {
  const style =
    'border-b-2 border-transparent px-3 py-2 text-sm text-control hover:no-underline [&.active]:border-brand [&.active]:font-semibold [&.active]:text-brand';
  return (
    <nav aria-label="知识库内容" className="mb-5 flex flex-wrap gap-1 border-b border-line">
      <NavLink to="/knowledge" end className={style}>
        已发布文章
      </NavLink>
      <NavLink to="/knowledge/reviews" className={style}>
        草稿与审阅
      </NavLink>
    </nav>
  );
}
