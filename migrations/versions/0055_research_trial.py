"""单次试用独立装载材料和知识版本，旧会话及来源保持原作用域。"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("trial_case", sa.String(80)))
    for table in ("sources", "knowledge_documents"):
        op.add_column(table, sa.Column("trial_conversation_id", sa.String(36)))
        op.create_foreign_key(f"fk_{table}_trial", table, "conversations", ["trial_conversation_id"], ["id"])
        op.create_index(f"ix_{table}_trial_conversation_id", table, ["trial_conversation_id"])


def downgrade():
    # 降级前移除产品试用数据需另走依赖保护流程，不能把样例转为全局知识。
    if op.get_bind().scalar(sa.text("SELECT 1 FROM conversations WHERE trial_case IS NOT NULL LIMIT 1")):
        raise RuntimeError("试用数据已存在，请从升级前备份恢复后再降级。")
    for table in ("knowledge_documents", "sources"):
        op.drop_index(f"ix_{table}_trial_conversation_id", table_name=table)
        op.drop_constraint(f"fk_{table}_trial", table, type_="foreignkey")
        op.drop_column(table, "trial_conversation_id")
    op.drop_column("conversations", "trial_case")
