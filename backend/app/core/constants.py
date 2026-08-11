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


# --- 月度测验系统 (quiz) ---------------------------------------------------

class QuizStatus(str, Enum):
    """测验（套）状态机：draft → reviewing → published → expired → archived。"""
    DRAFT = "draft"
    REVIEWING = "reviewing"
    PUBLISHED = "published"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class QuestionStatus(str, Enum):
    """题目 / 要点审核状态。"""
    DRAFT = "draft"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    REJECTED = "rejected"


class KbMatchStatus(str, Enum):
    """要点与 KB 关联状态。"""
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    MANUAL = "manual"


class QuizSection(str, Enum):
    """要点章节（识别时归并，其它原样保留）。"""
    REVIEW = "审核要点"
    QUESTIONNAIRE = "问卷要点"
    CALIBRATION = "填报口径微调"
    OTHER = "其它"


# 生成 / 提取的硬约束
QUIZ_MAX_QUESTIONS: int = 7          # 每套测验上限
QUIZ_DEFAULT_VALID_DAYS: int = 7     # 默认有效期（天）
QUIZ_KB_MATCH_THRESHOLD: float = 0.6  # KB 关联向量 cosine 阈值
QUIZ_EXTRACT_TIMEOUT_S: int = 120     # 提取/出题任务总超时
QUIZ_RETRY_TIMES: int = 2             # LLM JSON 解析重试次数
QUIZ_QUESTION_MAX_LEN: int = 45   # 题干最大字数（降阅读压力）
QUIZ_OPTION_MAX_LEN: int = 15     # 单选项最大字数
QUIZ_EXPLANATION_MAX_LEN: int = 80  # 解析最大字数
QUIZ_MAX_FILE_MB: int = 10            # docx 上传上限
QUIZ_DATA_RETENTION_MONTHS: int = 12  # 答题记录保留月数
QUIZ_RETENTION_DAYS: int = QUIZ_DATA_RETENTION_MONTHS * 30  # 清理阈值（天）
