# -*- coding: utf-8 -*-
"""知识包发布用例：产物门禁 + 状态门禁的单一入口（ADR 0028 职责分离）。

发布 / 回滚是业务用例 = 产物完整性校验 + 注册库状态机 + 审计的编排，
不属于存储层也不属于路由层。API 端点与未来 CLI 都应经由此模块，
直接调用 knowledge_registry.publish_release 会绕过产物门禁。
"""
from __future__ import annotations

from app.persistence import knowledge_registry as registry
from app.rag import index_paths
from app.services.release_artifacts import manifest_content_hash, validate_release_dir


class ReleasePublishError(Exception):
    """发布用例错误基类。"""


class ReleaseNotFoundError(ReleasePublishError, ValueError):
    """release 未在注册库登记。"""


class ReleaseArtifactError(ReleasePublishError, ValueError):
    """产物门禁失败基类（目录缺失 / manifest 无效 / hash 不符）。"""


class ReleaseArtifactUnavailable(ReleaseArtifactError):
    """release 目录缺失、manifest 无效或产物 hash 不一致。"""


class ReleaseHashMismatch(ReleaseArtifactError):
    """注册库 content_hash（64 位 hex）与磁盘产物 canonical hash 不符。"""


def validate_release_artifacts(release_id: str) -> dict:
    """产物门禁：存在性 + manifest 完整性 + content_hash 强绑定。

    非 64 位 hex 的历史 content_hash 豁免绑定校验（legacy 兼容，
    2026-10-23 前登记的两条数据）；新登记一律经 --register 生成
    canonical hash，豁免分支随存量数据消亡。
    """
    try:
        release = registry.get_release(release_id)
    except ValueError as exc:
        raise ReleaseNotFoundError(str(exc)) from exc

    root = index_paths.release_dir(release_id)
    try:
        payload = validate_release_dir(root, release_id=release_id)
    except (FileNotFoundError, ValueError) as exc:
        raise ReleaseArtifactUnavailable(f"release 产物不可用：{exc}") from exc

    stored = str(release.get("content_hash") or "").lower()
    is_hex_digest = len(stored) == 64 and all(c in "0123456789abcdef" for c in stored)
    if is_hex_digest and stored != manifest_content_hash(payload):
        raise ReleaseHashMismatch("content_hash 与 release 产物不匹配，疑似手工伪造登记")
    return release


def publish_release(package_id: str, release_id: str, *, actor: str) -> dict:
    """发布：先过产物门禁，再走注册库状态机（build/eval 门禁在 registry 内）。"""
    validate_release_artifacts(release_id)
    return registry.publish_release(package_id, release_id, actor=actor)


def rollback_release(package_id: str, release_id: str, *, actor: str) -> dict:
    """回滚：与发布同一条门禁链（产物必须仍然完整）。"""
    validate_release_artifacts(release_id)
    return registry.rollback_release(package_id, release_id, actor=actor)
