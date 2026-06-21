"""读环境变量，集中配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    # Chroma
    chroma_dir: Path
    chroma_collection: str

    # Embedding
    embedding_provider: str
    dashscope_api_key: str
    dashscope_model: str
    embedding_url: str

    # LLM
    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_url: str

    # 检索参数
    top_k: int
    similarity_threshold: float

    # DISABLED(voice) 2026-06-21: 讯飞 ASR 字段停用，保留供未来恢复。
    # xunfei_app_id: str
    # xunfei_api_key: str
    # xunfei_api_secret: str
    # xunfei_asr_domain: str  # 业务领域（gov=政务）


def _load() -> Settings:
    provider = os.environ.get("EMBEDDING_PROVIDER", "dashscope").lower()
    if provider == "dashscope":
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        model = os.environ.get("DASHSCOPE_MODEL", "text-embedding-v3")
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    else:
        api_key = os.environ.get("BGE_API_KEY", "")
        model = os.environ.get("BGE_MODEL", "BAAI/bge-large-zh-v1.5")
        url = os.environ.get(
            "BGE_API_URL", "https://api.bge.modelbest.cn/v1/embeddings"
        )

    llm_provider = os.environ.get("LLM_PROVIDER", "deepseek").lower()
    if llm_provider == "deepseek":
        llm_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        llm_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        llm_url = "https://api.deepseek.com/v1/chat/completions"
    elif llm_provider == "dashscope":
        llm_api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        llm_model = os.environ.get("DASHSCOPE_LLM_MODEL", "qwen-plus")
        llm_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    else:
        llm_api_key = ""
        llm_model = ""
        llm_url = ""

    return Settings(
        chroma_dir=Path(os.environ.get("CHROMA_DIR", "backend/data/chroma"))
        if Path(os.environ.get("CHROMA_DIR", "backend/data/chroma")).is_absolute()
        else PROJECT_ROOT / os.environ.get("CHROMA_DIR", "backend/data/chroma"),
        chroma_collection=os.environ.get("CHROMA_COLLECTION", "labor_survey_qa"),
        embedding_provider=provider,
        dashscope_api_key=api_key,
        dashscope_model=model,
        embedding_url=url,
        llm_provider=llm_provider,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_url=llm_url,
        top_k=int(os.environ.get("RETRIEVAL_TOP_K", "5")),
        similarity_threshold=float(os.environ.get("SIMILARITY_THRESHOLD", "0.5")),
        # DISABLED(voice) 2026-06-21: 讯飞字段停用，保留供未来恢复。
        # xunfei_app_id=os.environ.get("XUNFEI_APP_ID", ""),
        # xunfei_api_key=os.environ.get("XUNFEI_API_KEY", ""),
        # xunfei_api_secret=os.environ.get("XUNFEI_API_SECRET", ""),
        # xunfei_asr_domain=os.environ.get("XUNFEI_ASR_DOMAIN", "gov"),
    )


settings = _load()
