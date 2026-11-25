"""评估调度保留当前仍有效的交互用例核对，不再包含已下线的 call_model 辅助实验。"""

from evaluation.optimization import schedule


def test_interactive_schedule_keeps_nine_cases():
    # 八类机制保留原 I08 回归，并新增一份独立材料 I09，不掩盖已用于调试的旧失败。
    assert len(schedule("interactive")) == 9
