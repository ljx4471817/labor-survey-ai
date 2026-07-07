"""merge_query_with_history 多轮上下文合并测试。

规则（来自 ADR 0007）：
- msg >= 8 字 → 原样返回，message 已是清晰 query
- msg < 8 字 + history 有 user → "last_user msg"（保留首轮上下文）
- msg < 8 字 + 无 history → 原样返回（不拼，按单轮处理）
- strip 前后空白
"""
from app.models.schemas import ChatMessage
from app.rag.pure import merge_query_with_history


def test_merge_long_msg_returns_as_is():
    # msg >= 8 字 → 不动
    msg = "F27 的自营职业怎么算？"  # 13 字
    history = [ChatMessage(role="user", content="上一个问题")]
    assert merge_query_with_history(msg, history) == msg


def test_merge_short_msg_with_history_prepends():
    # msg < 8 字 → 拼上最后一个 user 消息
    msg = "合同呢？"  # 4 字
    house = [
        ChatMessage(role="assistant", content="F27 是…"),
        ChatMessage(role="user", content="自营职业怎么算"),
    ]
    result = merge_query_with_history(msg, house)
    # 必须包含 last_user 内容 + msg
    assert isinstance(result, str)
    assert "自营职业怎么算" in result
    assert "合同呢" in result


def test_merge_short_msg_no_history_unchanged():
    # msg < 8 字 + 无 history → 不拼
    msg = "F27 呢？"  # 4 字
    assert merge_query_with_history(msg, []) == msg


def test_merge_strips_whitespace():
    # strip 后长度判定
    msg = "   F27 呢？   "  # strip 后 = 5 字 → 走拼历史
    history = [ChatMessage(role="user", content="自营职业")]
    result = merge_query_with_history(msg, history)
    assert "自营职业" in result
    assert "F27" in result


def test_merge_only_assistant_in_history_unchanged():
    # history 只有 assistant → 无 user → 不拼
    msg = "呢？"  # 1 字
    house = [ChatMessage(role="assistant", content="F27 是…")]
    assert merge_query_with_history(msg, house) == msg


def test_merge_uses_last_user_not_first():
    # 多个 user → 取最后一个
    msg = "呢？"  # 1 字
    house = [
        ChatMessage(role="user", content="第一个问题"),
        ChatMessage(role="user", content="第二个问题"),
    ]
    result = merge_query_with_history(msg, house)
    assert "第二个问题" in result
    assert "第一个问题" not in result