"""项目常量集中地。

所有跨模块复用的枚举 / 字符串字面量 / 配置常量都定义在这里。
历史背景：原本散落在 chat.py / admin.py / retriever.py / prompts.py 等多个文件中，
改名需要全仓 grep。集中后用 Pydantic / FastAPI 校验自动生效，IDE 也能自动补全。
"""
from __future__ import annotations

from enum import Enum


# --- 检索模式 (chat.py / admin.py / query_log.py 都用) -------------------

class RetrievalMode(str, Enum):
    """chat 检索的四条结果分支。

    含义见 docs/CONTEXT.md §5。保留为 str 子类是为了让 Pydantic / JSON 序列化
    直接输出字符串而不是 enum repr。
    """
    RAG = "rag"
    OUT_OF_KB = "out_of_kb"
    OUT_OF_SCOPE = "out_of_scope"
    AMBIGUOUS = "ambiguous"


# --- 反馈评级 (feedback.py / admin.py) ---------------------------------

class FeedbackRating(str, Enum):
    """用户对 AI 回复的采纳投票。"""
    UP = "up"
    DOWN = "down"


# --- LLM 拒答模式 (chat.py) ---------------------------------------------

# 命中这些正则表示 LLM 主动拒答（"未找到相关内容"），应该走 OUT_OF_KB 而不是 RAG。
# 必须避免误判事实陈述（如"流动人口登记"），故只匹配"抱歉 + 未找到"组合。
REFUSAL_PATTERNS: tuple[str, ...] = (
    r"抱歉.*?知识库.*?(没有|找不到).*?(找到|收录|涵盖|涉及)",
    r"知识库中(没有|找不到|收录|涵盖|涉及)",
    r"知识库未(找到|收录|涵盖|涉及)",
    r"没有|找不到).*?相关(内容|信息|答案)",
)


# --- 反馈聚合 (admin.py) -------------------------------------------------

MIN_FREQ: int = 3
TOP_DOWN_QUESTIONS: int = 10
TOP_DOWN_KB: int = 5
RECENT_DOWN_PAGE_SIZE: int = 60


# --- KB 文档类型 (rag/prompts.py) ---------------------------------------

class DocType(str, Enum):
    """检索结果中的文档类型，区分 QA 条目和 chunk 条目。"""
    QA = "qa"
    CHUNK = "chunk"


# --- 管理员层级 (whitelist_db / sync script / WhitelistEntry) -----------------

class AdminLevel(str, Enum):
    """白名单用户的管理员层级。决定区域字段是否必填、数据可见范围。"""
    PROVINCE = "省级"
    CITY = "市级"
    DISTRICT = "区县"
    ENUMERATOR = "调查员"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(m.value for m in cls)


# --- 区域 5 级（暂不抽到此处，因为 whitelist_db / query_log 各自的索引用，---
# --- 抽到这里会引入跨 DB 模块的依赖，等真加第 6 级时再统一。详见 CONTEXT.md §9 ---
