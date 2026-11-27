# Research Workbench 项目上下文

更新：2026-11-27。开发对象：本 Git 仓库。历史批次交付流水已无损归档至 [历史上下文归档](docs/history/project-context-archive-20261124.md)，本文只维护当前接续状态与核心基线。

## 一、当前接续状态

- **最新迁移与数据库基线（2026-11-27）**：代码迁移与开发部署的正式业务库 `knowledgeops` 均保持在 `0065`（冲刺包 1 提交 `3eb1fd7` + CI 修复 `4cdddb3` 已推至 `origin/main`；冲刺包 2 阶段 1 为 0 数据库迁移）。
- **阶段收口已完成项**：
  - 通用辅助模型工具 `call_model` 与 `auxiliary` 工具组已彻底下线（迁移 `0057`，见 [决策 0032](docs/decisions/0032-retire-auxiliary-call-model.md)），应用内部单次调用及历史兼容保留。
  - 工程治理与 CI（2026-11-24）：新增 [数据库总览与核心状态机](docs/database-and-state-machines.md)（7 大表群、5 条 DB 红线、五轨状态机）；[AGENTS.md](AGENTS.md) 将 OpenAI Codex 列入长期参考对象；已新增离线零付费 GitHub CI（[.github/workflows/ci.yml](.github/workflows/ci.yml)），CD 镜像发布暂缓。
- **研发助手主线进展**：
  - 按 [团队研发功能 PRD V0.4](docs/product/团队研发功能PRD.md)、[研发工作台技术设计 V0.6](docs/technical/研发工作台技术设计.md) 与 [产品设计进度](docs/product-design-progress.md) 沿 `main` 推进（先个人研发工作台，再两账号协作试用）。
  - 个人研发闭环已完成隔离验收（简报与方法版本绑定、显式授权自主运行、固定地图与证据回查、同主线对比与采纳、审批实验与固定成果验收）。
  - 已完成三阶段体验与可靠性加固（2026-11-24）：① DSML 协议泄漏有界原地纠偏（最多 1 次，不重放中毒检查点，在原 `ResearchPermit` 剩余额度下干净续跑）与 `submit_research_delivery` 平铺参数加固；② 实时可观测性升级（后端脱敏流式 `reasoning_delta`、会话与研发标签共用 `LiveRunPanel` 可折叠思考过程与持久化工具步骤时间线、自主推进弹窗三档预设＋联网开关＋折叠安全上限）；③ 研发分支归档恢复（`unarchive` 仅清空 `archived_at`，不复活旧许可/旧实验、不自动切写入分支）与离线零付费 GitHub CI。
  - 已接入联合 BM25、模型请求证据、聊天内资料侧栏、三条空白会话试用题、正式提交预览/只读快照、从提交创建分支与后续写入分支选择（写入分支迁移 `0058`、归档迁移 `0059`）。
  - 空白会话可打开「查看已完成案例」。固定记录来自同一个运维问答试点的联合问答、两个研发分支、真实隔离实验和固定成果验收。模拟资料与确定性模型替身的范围已在界面标明。
  - 已取消或已验收的任务均可从固定提交分叉，显式确认后在原任务和会话开启新一轮。旧许可和历史验收保留，下一次运行由真人发消息或重新授予自主许可。
  - 先前的独立 Milvus 对照仅是历史验证。当前应用已接入 PostgreSQL pgvector、联合 BM25 的 RRF 融合以及可选重排。开发部署的业务库已现场核对为 `0061`，混合检索已启用，Harrier 640 维索引档案处于 `ready`。管理入口为「智能体设置 → 检索与向量」。隔离环境已验证真实嵌入、混合命中和 DeepSeek 基于证据作答，完整工具循环由离线回归覆盖。[检索实现](docs/architecture.md#联合词法与向量检索) · [验收](docs/progress.md#2026-11-25应用-rag-接入验收)
  - 已实现会话项目归类。支持项目内新建、重命名、置顶、拖动或菜单移动会话，以及移除项目后将会话退回未分组。[0060 验收](docs/progress.md#2026-11-25会话项目归类及恢复验证)已在隔离环境通过。
  - 智能体设置的技能页可从已验收运维案例生成带证据的方法提案。人工查看差异后可发布或驳回，实际发布沿用原 Skill 管理和运行冻结。隔离环境已验收；正式技能包尚无此发布。[方法提案验收](docs/progress.md#2026-11-25从已验收运维试点形成技能提案)
  - **主线 A（Hermes 完整在线自进化体系，`0062`–`0063`，已通过隔离验收与独立代码审查）**：已落地在线六大支柱（双槽记忆与硬容量上限、工具白名单隔离后台复盘与高信号/阈值/验收合并触发、会话级 Prompt Cache 冻结快照与 `revocation_epoch` 撤回穿透、确定性安全扫描与 `Pin/Lock` 保护、Skill 不可变快照回滚 + 持久遥测 + 零付费 Curator 整理与安全归档、Learning Journey 统一时间线与单项回滚），前台意图 `v6` 已剥离旧自动记忆提取（兼容 `v5` 历史冻结）；前端沉淀 5 个共用组件并打通长期记忆页、智能体设置→技能页与聊天「本次任务设置」快照刷新，通过桌面与 390px Chromium 截图验收。[0062 复核](artifacts/verification/hermes-evolution-0062-review/summary.md) · [0063 验收](artifacts/verification/hermes-evolution-0063/summary.md)
  - **主线 B（Playwright MCP 动态浏览器取证，`0064`，已通过隔离验收与独立代码审查）**：参考 DeepSeek Harness（DSH）浏览器子系统设计，预装锁版本 `@playwright/mcp@0.0.86` 于容器内；落地正常聊天与研发模式共用的 7 个只读内建工具（`browser` 工具组，子 Agent 暂不开放）、容器 HTTP/CONNECT 出站代理与公网 DNS/SSRF 校验、`launch`（容器无头隔离，默认）+ `attach`（本机可见浏览器 CDP，默认关闭、PG advisory lock 跨 worker 独占、退出不关窗）双模式、不可变 `SourceVersion(playwright-mcp-rendered-v1)` 与内容寻址 PNG `FileStore`/`RunArtifact` 归档及受控预览接口、Windows 专用调试窗口启动脚本 `scripts/start-local-browser.ps1`；前端接入工具页配置弹窗、聊天「本次任务设置」模式选择与运行截图预览，通过桌面与 390px 真实截图验收。[0064 交付](artifacts/verification/playwright-mcp-0064/summary.md) · [0064 复核](artifacts/verification/playwright-mcp-0064-review/summary.md)
  - **冲刺包 1（RAG 向量知识库自动化与管理 UI，`0065`，✅ 已封板推送 `3eb1fd7` + CI 修复 `4cdddb3` 且正式库升级至 `0065`）**：复用现有 `pgvector` 与单一正文真相源，借鉴 Dify / RAGFlow 落地入库自动 Celery 异步向量化（`rag_index_jobs` 事务外盒、按版本/代次防过期回写、重启等待人工恢复、批量/单篇重试统一提供方门禁）、轻量逻辑知识集（`rag_collections` / `rag_collection_items`）、只读 Chunk 分块预览 + RAGFlow 式单块启停开关（`enabled`）、只读在线召回测试 Hit Testing（BM25 名次交错 / 向量 / RRF / 重排防呆与分阶段耗时）、以及聊天底栏一键挂载「全库 / 知识集」（`rag_scope` / `collection_ids` 同步收敛检索与证据复核，默认保持旧幂等指纹兼容）；并清理了过时根目录 `demo/` 与 `scripts/seed_demo.py`。[0065 交付](artifacts/verification/rag-workbench-0065/summary.md) · [0065 复核](artifacts/verification/rag-workbench-0065-review/summary.md)
  - **冲刺包 2 · 阶段 1（聊天流式体验与任务/诊断页重构，0 迁移，✅ 已通过独立审查并封板 `09fef9c`）**：普通聊天改为无边框流式 Markdown + 思考过程自动收折（尊重用户手动切换）+ 可展开脱敏工具/命令步骤与耗时；研发与后台任务保留结构化状态卡；任务详情消除空交付噪音，长结果改为清晰内滚+一键展开全高，工具活动支持正/倒序、仅出错筛选与脱敏目标/耗时展示；诊断页扁平化四格 KPI 速跳、统一不定长列表限高内滚并默认隐藏流式 `delta` 碎片（不影响分页与全量导出）。[验收与 10 张截图](artifacts/verification/chat-runs-ux/summary.md)
  - **冲刺包 2 · 联调前热修与生命周期收敛（0 迁移，✅ 已验证封板）**：修复输入框草稿保存闪烁并切换 `Enter` 发送/`Shift+Enter` 换行；统一 `ResourceService.ensure` 与 `MCPCalls.decide` 的控制锁顺序（先 `lock_control` 再 `runs FOR UPDATE`）根治暂停/取消 500 PG AB-BA 死锁；补齐 `RunQuestion` 终态白名单 `declined` 并落地**路线 A2**（删除会话解耦全库引用图，秒级解绑 `SourceUse`，无主临时材料与已删会话临时草稿交由后台 `AssetCleaner.reconcile()` 精准回收）；修复聊天 `waiting_input` 重复任务卡、浏览器未启用提示与系统维护页空闲心跳误报；改用 `sessionmaker` 实例级幂等标记修复 SQLAlchemy 2.1 `event.contains` 地址复用导致的 CI 偶发引用追踪失效。
  - **下一步接续重点（2026-11-27 生产收口与面试对齐期）**：按 [roadmap-2026-11.md](docs/plans/roadmap-2026-11.md) 推进：**② 冲刺包 2 · 阶段 2（下一立即启动）**：重启开发容器使 `0061`–`0065` + 新 UI 生效，用真实 DeepSeek 跑通研发全流程（含 Windows 本机 Chrome CDP 实测）并产出 30–50 题 RAG 对比评测报告与 5 类故障注入可靠性报告 → **③ 冲刺包 3**：最小 JWT 鉴权/资源越权隔离/Redis 限流 + `ModelGateway` 429 重试/Fallback + Dependabot 高危清理 → **④ 冲刺包 4**：重写 `README.md` + 图文 `SHOWCASE.md` 后正式封箱转入面试。
- **既有未闭环工程项**：2026-10-31 APP-02～APP-05 独立验收仍有未通过项，记录在 [复核报告](docs/delivery/app02-app05-acceptance-20261031.md) 与 `docs/development-issues.md`（`DEV-121`），APP-01/06/07 继续暂缓。

## 二、当前技术栈与运行基线

- **后端**：Python 3.12 / FastAPI / SQLAlchemy / Alembic / LangChain `create_agent` / LangGraph / Deep Agents 0.7.23 / Celery + Redis / PostgreSQL 17（`pg_search`）。
- **前端**：React + TypeScript + Vite + Tailwind CSS v4（保留 SPA）。
- **运行模式与固定容器清单（严禁另起临时 DB/Redis 容器）**：
  - 开发模式（`compose.dev.yaml`）复用既有容器：`knowledgeops-agent-dev-1`（应用与测试执行容器）、`knowledgeops-agent-postgres-1`（PG 17 + `pg_search` + `pgvector`）、`knowledgeops-agent-redis-1`（Redis 7.4），以及按需启停的 GPU 嵌入容器 `knowledgeops-agent-tei-embedding-1`（离线测试不依赖）。
  - 测试统一使用现有 Postgres 内的 `knowledgeops_test`（含临时 schema，用后清理）与现有 Redis 的 15 号逻辑库（`redis://redis:6379/15`）；若既有容器处于停止态仅允许启动并复用，严禁 `docker run` 新建临时数据库或 Redis 容器。
  - 容器内用根 `.venv`（`/workspace/.venv/bin/python`）通过 `python scripts/dev.py` 统一启动 API、worker 和 Vite；单独测试/脚本用 `python scripts/dev.py --run ...`（Pytest/Vitest/Playwright 严禁并行，Vitest 必须 `--maxWorkers=1`）。
  - 生产部署：`compose.yaml` 拆分为 `api`、`worker`、`web`（Nginx 静态托管 + 反代）、`postgres`、`redis` 与一次性 `init`。
- **数据与存储分工**：
  - PG 三库隔离：`knowledgeops`（业务 + LangGraph 检查点）、`knowledgeops_accounting`（独立模型账本）、`knowledgeops_test`（测试库）。
  - 工作区文件在 `data/agent-workspace`（`knowledge/documents` 为可编辑视图，`knowledge/versions` 等为不可变正文）；PG 存元数据、版本、引用、草稿、运行与派生索引；Redis 仅作 Celery broker、SSE 实时桥与确定检查点的可丢弃缓存。
- **执行与安全边界**：
  - 当前通用任务使用 worker 内 `LocalShellBackend`（逻辑工作区隔离，不宣称 OS/容器级隔离）；研发实验使用独立交换目录 + 受信固定脚本摘要校验（默认关闭）。
  - 暂不引入多租户、对象存储容器、递归委派或独立记忆队列；MCP 默认服务为空；真实模型开关由 `.env` 的 `MODEL_CALLS_ENABLED` 控制。

## 三、核心文档入口

- 工程规则：[AGENTS.md](AGENTS.md)
- 全部文档导航：[docs/README.md](docs/README.md)
- 数据库主干与核心状态机（改表/改流程必读）：[docs/database-and-state-machines.md](docs/database-and-state-machines.md)
- 完整架构实现说明：[docs/architecture.md](docs/architecture.md)
- 实时进展与问题台账：[docs/progress.md](docs/progress.md)、[docs/development-issues.md](docs/development-issues.md)
