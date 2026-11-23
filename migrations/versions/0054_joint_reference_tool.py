"""为下一次运行启用联合资料检索，保留已冻结的运行许可和历史策略。"""

from alembic import op
from sqlalchemy.orm import Session

from knowledgeops_agent.harness.tools.policy import read_policy, save_policy

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None
TOOL = "builtin:search_references"


def upgrade():
    with Session(bind=op.get_bind()) as session:
        current = read_policy(session)
        if TOOL not in current["enabled"]:
            updated = {**current, "enabled": {**current["enabled"], TOOL: True}}
            save_policy(session, current, updated)
            session.flush()


def downgrade():
    # 保留配置选择和不可变策略修订，旧版本不会装配尚不存在的目录项。
    pass
