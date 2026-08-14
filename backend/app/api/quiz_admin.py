# -*- coding: utf-8 -*-

"""测验：管理端 API（import / extract / keypoints / generate / questions / publish / targets / stats / kb-search / scenes / delete）。



权限：require_quiz_admin（系统管理员或省级/市级业务管理员）；只读统计 require_quiz_stats（PRD 权限系统改造）。

异步任务：进程内内存任务表 + 线程池（单进程 uvicorn 假设，PRD v3 6.4）。

"""

from __future__ import annotations



import json

import os

import threading

import uuid

from datetime import datetime, timedelta, timezone



from typing import Literal

from pydantic import BaseModel

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from urllib.parse import quote

from loguru import logger



from pathlib import Path



from app.core.config import PROJECT_ROOT

from app.core.constants import QUIZ_DEFAULT_VALID_DAYS, QUIZ_MAX_FILE_MB

from app.infra.auth import get_current_user, in_scope, require_quiz_admin, require_quiz_stats, require_system_admin

from app.models.schemas.quiz_admin import (
    ExtractRequest,
    GenerateRequest,
    KeypointReviewRequest,
    PublishRequest,
    QuestionReviewRequest,
    QuestionSelectRequest,
    QuizDeleteRequest,
    SceneAddRequest,
    SceneToggleRequest,
)

from app.persistence import quiz_db, whitelist_db

from app.services import quiz_llm

from app.services.quiz_extract import ALLOWED_DOC_EXTENSIONS, extract_file_text, run_extraction

from app.services.quiz_generator import generate_questions, is_expired, match_kb, options_to_json



router = APIRouter()



UTC8 = timezone(timedelta(hours=8))

TMP_DIR = PROJECT_ROOT / "backend" / "data" / "quizzes" / "tmp"

FAQ_PATH = PROJECT_ROOT / "knowledge-base" / "qa" / "faq.json"

MAX_FILE_BYTES = QUIZ_MAX_FILE_MB * 1024 * 1024



# --- 异步任务（内存） --------------------------------------------------------

_TASKS: dict[str, dict] = {}

_TASK_LOCK = threading.Lock()





def _now() -> str:

    return datetime.now(UTC8).isoformat(timespec="seconds")





def _submit_task(kind: str, fn, *args) -> str:

    task_id = "TASK" + uuid.uuid4().hex[:8].upper()

    with _TASK_LOCK:

        _TASKS[task_id] = {"status": "processing", "kind": kind}



    def _run() -> None:

        try:

            result = fn(*args)

            with _TASK_LOCK:

                _TASKS[task_id] = {"status": "done", "kind": kind, "result": result}

        except Exception as e:  # noqa: BLE001

            logger.exception(f"quiz task {task_id} failed")

            with _TASK_LOCK:

                _TASKS[task_id] = {"status": "error", "kind": kind, "error": str(e)}



    threading.Thread(target=_run, daemon=True).start()

    return task_id





def _task_status(task_id: str) -> dict:

    with _TASK_LOCK:

        task = _TASKS.get(task_id)

    if not task:

        raise HTTPException(404, "任务不存在")

    return dict(task)





def _parse_valid_until(raw: str | None, now_iso: str) -> str:

    """解析有效期：ISO 或 YYYY-MM-DD；缺省 = now + 7 天。必须晚于 now。"""

    if not raw:

        return (datetime.now(UTC8) + timedelta(days=QUIZ_DEFAULT_VALID_DAYS)).isoformat(timespec="seconds")

    s = raw.strip()

    try:

        dt = datetime.fromisoformat(s)

    except ValueError:

        dt = datetime.fromisoformat(s + "T23:59:59+08:00")

    if dt.tzinfo is None:

        dt = dt.replace(tzinfo=UTC8)

    if is_expired(dt.isoformat(timespec="seconds"), now_iso):

        raise HTTPException(422, "有效期必须晚于当前时间")

    return dt.isoformat(timespec="seconds")





def _load_faq() -> list[dict]:

    try:

        return json.loads(FAQ_PATH.read_text(encoding="utf-8"))

    except (FileNotFoundError, OSError, json.JSONDecodeError):

        return []





# --- 导入 --------------------------------------------------------------------



@router.post("/quiz/import")
def quiz_import(
    title: str = Form(..., min_length=1, max_length=80),
    scene: str = Form(..., min_length=1, max_length=30),
    month: str | None = Form(None, pattern=r"^\d{4}-\d{2}$"),
    file: UploadFile = File(...),
    phone: str = Depends(require_quiz_admin),
) -> dict:
    """导入上层文件（.doc/.docx/.wps/.pdf/.pptx，≤20MB）→ 新建测验（标题+场景+可选月份）。"""
    filename = (file.filename or "").strip()
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(400, "仅支持 .doc/.docx/.wps/.pdf/.pptx 文件")
    raw = file.file.read()
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(400, f"文件超过 {QUIZ_MAX_FILE_MB}MB 上限")
    # 每次导入创建独立测验（多场景不再按月份防重）
    month_val = (month or "").strip() or None
    quiz_id = quiz_db.create_quiz(title=title.strip(), scene=scene.strip(), created_by=phone, month=month_val)
    if scene.strip() not in {s["name"] for s in quiz_db.list_scenes(include_inactive=True)}:
        quiz_db.add_scene(scene.strip())  # 场景不存在时自动登记（幂等）
    import_id = quiz_db.create_import(quiz_id, month_val, filename, len(raw), phone)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = TMP_DIR / f"{import_id}{ext}"
    tmp_path.write_bytes(raw)
    logger.info(f"quiz import: {import_id} quiz={quiz_id} scene={scene} size={len(raw)} by={phone[:3]}****")
    return {"import_id": import_id, "quiz_id": quiz_id, "filename": filename, "title": title, "scene": scene}





# --- 提取（异步） -------------------------------------------------------------



def _mock_llm_chat(messages: list[dict], **kwargs) -> str:

    """确定性 mock LLM（QUIZ_MOCK_LLM=1 时用于自动化 E2E，不调真实 DeepSeek）。"""

    sys_prompt = (messages[0]["content"] or "") if messages else ""

    if "提取" in sys_prompt:

        return (

            '[{"section": "审核要点", "content": "将家务劳动者误判为就业是常见错误", '

            '"common_error": "误判为就业", "suggest_quiz": true}, '

            '{"section": "问卷要点", "content": "调查参考周为8月3-9日", '

            '"common_error": "搞错参考周", "suggest_quiz": true}]'

        )

    return (

        '{"question": "家务劳动者无收入应判定为？", '

        '"options": {"A": "就业人口", "B": "失业人口", "C": "非劳动力", "D": "在职未就业"}, '

        '"answer": "C", "explanation": "根据审核要点，无收入家务劳动应判为非劳动力。"}'

    )





def _get_llm_chat():
    """QUIZ_MOCK_LLM=1 时返回 mock，否则返回测验独立 LLM chat（不影响对话模型）。"""
    if os.environ.get("QUIZ_MOCK_LLM") == "1":
        return _mock_llm_chat
    return quiz_llm.chat






def _do_extract(import_id: str, quiz_id: str, keypoint_count: int | None = None) -> dict:

    """后台线程：docx → 文本 → LLM 要点 → KB 关联 → 入库。"""

    llm_chat = _get_llm_chat()



    imp = quiz_db.get_import(import_id)

    ext = Path((imp or {}).get("filename", ".docx")).suffix

    tmp_path = TMP_DIR / f"{import_id}{ext}"

    text = extract_file_text(str(tmp_path))

    # 提取输出可能 >2000 tokens，提高上限避免截断导致 JSON 非法

    keypoints = run_extraction(text, lambda msgs: llm_chat(msgs, max_tokens=4000, timeout=90), keypoint_count=keypoint_count)

    matched = 0

    for kp in keypoints:

        m = match_kb(kp["content"])

        if m:

            kp.update({

                "kb_faq_id": m["faq_id"],

                "kb_question": m["question"],

                "kb_score": m["score"],

                "kb_match_status": "matched",

            })

            matched += 1

    quiz_db.replace_keypoints(quiz_id, keypoints)

    quiz_db.update_import(import_id, status="extracted", raw_text_length=len(text))

    tmp_path.unlink(missing_ok=True)

    return {"quiz_id": quiz_id, "keypoints": len(keypoints), "requested": keypoint_count, "matched": matched}





@router.post("/quiz/extract")

def quiz_extract(req: ExtractRequest, phone: str = Depends(require_quiz_admin)) -> dict:

    quiz = quiz_db.get_quiz(req.quiz_id)

    if not quiz:

        raise HTTPException(404, "测验不存在")

    if quiz["status"] not in ("draft", "reviewing"):

        raise HTTPException(409, "测验已下发或归档，不可提取")

    imp = quiz_db.latest_import_for_quiz(req.quiz_id)

    if not imp:

        raise HTTPException(409, "该测验还没有导入记录")

    task_id = _submit_task("extract", _do_extract, imp["id"], req.quiz_id, req.keypoint_count)

    return {"task_id": task_id, "status": "processing"}





@router.get("/quiz/extract/status/{task_id}")

def quiz_extract_status(task_id: str, phone: str = Depends(require_quiz_admin)) -> dict:

    return _task_status(task_id)





# --- 要点 --------------------------------------------------------------------



@router.get("/quiz/keypoints")

def quiz_keypoints(quiz_id: str = Query(...), phone: str = Depends(require_quiz_admin)) -> dict:

    return {"items": quiz_db.list_keypoints(quiz_id)}





@router.post("/quiz/keypoint/review")

def quiz_keypoint_review(req: KeypointReviewRequest, phone: str = Depends(require_quiz_admin)) -> dict:

    kp = quiz_db.get_keypoint(req.keypoint_id)

    if not kp:

        raise HTTPException(404, "要点不存在")

    if req.action == "approve":

        quiz_db.update_keypoint(req.keypoint_id, reviewed_by=phone, status="approved")

    elif req.action == "reject":

        quiz_db.update_keypoint(req.keypoint_id, reviewed_by=phone, status="rejected")

    else:  # edit：应用编辑后视为确认

        if not req.edits:

            raise HTTPException(422, "edit 需要 edits 字段")

        quiz_db.update_keypoint(req.keypoint_id, reviewed_by=phone, status="approved", **req.edits)

    return {"ok": True}





# --- 出题（异步） -------------------------------------------------------------



def _do_generate(quiz_id: str, keypoints: list[dict], phone: str) -> dict:
    llm_chat = _get_llm_chat()
    questions, errors = generate_questions(
        keypoints, lambda msgs: llm_chat(msgs, max_tokens=1500, timeout=90)
    )
    # over_limit 仅作生成提示（软校验），不入库；按 seq 汇总，前端会话内展示 ⚠
    over_limit: dict[str, list[str]] = {}
    store_items: list[dict] = []
    for i, q in enumerate(questions, start=1):
        ol = q.get("over_limit") or []
        if ol:
            over_limit[str(i)] = ol
        store_q = {k: v for k, v in q.items() if k != "over_limit"}
        store_items.append({**store_q, "options": options_to_json(q["options"]), "created_by": phone})
    quiz_db.replace_questions(quiz_id, store_items)
    return {
        "quiz_id": quiz_id,
        "questions": len(questions),
        "keypoints": len(keypoints),
        "errors": errors,
        "over_limit": over_limit,
    }





@router.post("/quiz/generate")

def quiz_generate(req: GenerateRequest, phone: str = Depends(require_quiz_admin)) -> dict:
    quiz = quiz_db.get_quiz(req.quiz_id)
    if not quiz:
        raise HTTPException(404, "测验不存在")
    if quiz["status"] not in ("draft", "reviewing"):
        raise HTTPException(409, "测验已下发或归档，不可生成题目")
    kps = [kp for kp in quiz_db.list_keypoints(req.quiz_id) if kp["id"] in req.keypoint_ids]
    if not kps:
        raise HTTPException(422, "未选择有效要点")
    task_id = _submit_task("generate", _do_generate, req.quiz_id, kps, phone)
    return {"task_id": task_id, "status": "processing"}





@router.get("/quiz/generate/status/{task_id}")

def quiz_generate_status(task_id: str, phone: str = Depends(require_quiz_admin)) -> dict:

    return _task_status(task_id)





# --- 题目 --------------------------------------------------------------------



@router.get("/quiz/questions")

def quiz_questions(quiz_id: str = Query(...), phone: str = Depends(require_quiz_admin)) -> dict:

    return {"items": quiz_db.list_questions(quiz_id)}





@router.post("/quiz/question/review")

def quiz_question_review(req: QuestionReviewRequest, phone: str = Depends(require_quiz_admin)) -> dict:

    q = quiz_db.get_question(req.question_id)

    if not q:

        raise HTTPException(404, "题目不存在")

    if req.action == "approve":

        quiz_db.update_question(req.question_id, status="approved", selected=1)  # approved => in publish set

    elif req.action == "reject":

        quiz_db.update_question(req.question_id, status="draft", selected=0)  # 打回修改，退出下发

    else:  # edit：应用编辑后视为确认

        if not req.edits:

            raise HTTPException(422, "edit 需要 edits 字段")

        quiz_db.update_question(req.question_id, status="approved", selected=1, **req.edits)  # 编辑保存即拟下发

    return {"ok": True}





@router.post("/quiz/question/select")
def quiz_question_select(req: QuestionSelectRequest, phone: str = Depends(require_quiz_admin)) -> dict:
    """勾选/取消题目（试卷题）；仅 draft/reviewing 可调整。"""
    q = quiz_db.get_question(req.question_id)
    if not q:
        raise HTTPException(404, "题目不存在")
    quiz = quiz_db.get_quiz(q["quiz_id"])
    if not quiz or quiz["status"] not in ("draft", "reviewing"):
        raise HTTPException(409, "仅草稿/审核中的测验可调整勾选")
    quiz_db.update_question(req.question_id, selected=1 if req.selected else 0)
    return {"ok": True, "selected": req.selected}


# --- 下发 --------------------------------------------------------------------



@router.post("/quiz/publish")

def quiz_publish(req: PublishRequest, phone: str = Depends(require_quiz_admin)) -> dict:

    quiz = quiz_db.get_quiz(req.quiz_id)

    if not quiz:

        raise HTTPException(404, "测验不存在")

    now = _now()

    actor = whitelist_db.get_user(phone)

    out_of_scope = []

    for tp in req.targets:

        tu = whitelist_db.get_user_any(tp)

        if not tu or not in_scope(actor, tu):

            out_of_scope.append(tp[:3] + "****" + tp[-4:])

    if out_of_scope:

        raise HTTPException(422, out_of_scope)



    if req.action == "publish":

        if quiz["status"] not in ("draft", "reviewing"):

            raise HTTPException(409, "测验已下发或归档")

        questions = quiz_db.list_questions(req.quiz_id)
        selected_qs = [q for q in questions if q.get("selected")]
        if not selected_qs:
            raise HTTPException(422, "请先确认要下发的题目（确认后即拟下发）")
        if any(q["status"] != "approved" for q in selected_qs):
            raise HTTPException(422, "存在未审核通过的勾选题目，无法下发")

        valid_until = _parse_valid_until(req.valid_until, now)

        quiz_db.set_targets(req.quiz_id, req.targets)

        quiz_db.update_quiz(req.quiz_id, status="published", valid_from=now, valid_until=valid_until)

        logger.info(f"quiz publish: {req.quiz_id} targets={len(req.targets)} valid_until={valid_until}")

        return {"ok": True, "action": "publish", "total_users": len(req.targets), "valid_until": valid_until}

    elif req.action == "append":

        if quiz["status"] not in ("published", "expired"):

            raise HTTPException(409, "仅已下发测验可追加")

        added = quiz_db.add_targets(req.quiz_id, req.targets)

        return {"ok": True, "action": "append", "added": added}

    else:  # remove

        if quiz["status"] not in ("published", "expired"):

            raise HTTPException(409, "仅已下发测验可移除")

        removed = quiz_db.remove_targets(req.quiz_id, req.targets)

        return {"ok": True, "action": "remove", "removed": removed}





# --- 目标 / 统计 / 辅助 --------------------------------------------------------



ROLE_LABELS = {"省级": "省级管理者", "市级": "市级管理者", "区县": "区县管理者", "调查员": "调查员"}

ROLE_PRIORITY = {"省级": 0, "市级": 1, "区县": 2, "调查员": 3}

STATS_COLUMNS = ["省", "市", "县", "调查小区", "姓名", "联系电话", "管理员层级", "作答情况"]





@router.get("/quiz/targets")

def quiz_targets(q: str | None = Query(None, max_length=30), phone: str = Depends(require_quiz_admin)) -> dict:

    """下发对象：全部 active 白名单用户，按 市 → 角色 分组（含各角色总人数；明细随响应返回，前端折叠展示）。"""

    actor = whitelist_db.get_user(phone)

    users = [u for u in whitelist_db.list_all(active_only=True) if in_scope(actor, u)]

    if q:

        qq = q.strip()

        users = [u for u in users if qq in (u.get("name") or "") or qq in (u.get("phone") or "")]

    cities: dict[str, dict] = {}

    for u in users:

        city = (u.get("city") or "未分区").strip()

        role = u.get("admin_level") or "调查员"

        g = cities.setdefault(city, {"city": city, "roles": {}})

        rg = g["roles"].setdefault(role, {"role": role, "label": ROLE_LABELS.get(role, role), "count": 0, "users": []})

        rg["count"] += 1

        rg["users"].append({"phone": u["phone"], "name": u["name"], "county": (u.get("county") or "").strip()})

    cities_list = [

        {"city": c, "roles": sorted(g["roles"].values(), key=lambda r: ROLE_PRIORITY.get(r["role"], 9))}

        for c, g in sorted(cities.items())

    ]

    return {"total": len(users), "cities": cities_list}





def _stats_rows(quiz_id: str, region: str | None, actor: dict | None = None) -> tuple[list[dict], dict, int, int]:

    """完成率明细行 + 区县汇总 + 总体 started/completed（region 过滤后）。"""

    quiz = quiz_db.get_quiz(quiz_id)

    if not quiz:

        raise HTTPException(404, "测验不存在")

    quiz_db.sync_expired()

    quiz_db.cleanup_expired()

    questions_total = quiz_db.count_questions(quiz_id)

    target_phones = quiz_db.list_target_phones(quiz_id)

    user_map = {u["phone"]: u for u in whitelist_db.list_all(active_only=False) if u["phone"] in target_phones}



    by_region: dict[str, dict] = {}

    rows: list[dict] = []

    started = completed = 0

    for p in target_phones:

        u = user_map.get(p)

        if not u:

            continue

        if actor is not None and not in_scope(actor, u):

            continue

        county = (u.get("county") or "未分区").strip()

        if region and region not in (u.get("city") or "") and region not in county:

            continue

        ans = quiz_db.count_answers(quiz_id, p)

        corr = quiz_db.count_correct(quiz_id, p)

        done = questions_total > 0 and ans >= questions_total

        if ans > 0:

            started += 1

        if done:

            completed += 1

        rows.append({

            "phone": p,

            "name": u.get("name", ""),

            "province": u.get("province", ""),

            "city": u.get("city", ""),

            "county": county,

            "community": (u.get("community") or "").strip(),

            "admin_level": u.get("admin_level") or "调查员",

            "status": "已完成" if done else ("进行中" if ans > 0 else "未开始"),

            "answered": ans,

            "score": corr,

            "total": questions_total,

            "submitted_at": quiz_db.latest_answer_ts(quiz_id, p),

        })

        by_region.setdefault(county, {"region": county, "total": 0, "completed": 0})

        by_region[county]["total"] += 1

        if done:

            by_region[county]["completed"] += 1

    for r in by_region.values():

        r["rate"] = round(r["completed"] / r["total"], 4) if r["total"] else 0.0

    return rows, by_region, started, completed





@router.get("/quiz/whoami")

def quiz_whoami(phone: str = Depends(require_quiz_stats)) -> dict:

    user = whitelist_db.get_user(phone) or {}

    return {"admin_level": user.get("admin_level", ""), "sys_role": user.get("sys_role", "")}


class QuizLlmConfigRequest(BaseModel):
    """测验模型切换请求：provider = minimax / dashscope / deepseek（仅系统管理员）。"""

    provider: Literal["minimax", "dashscope", "deepseek"]


def _quiz_llm_payload(cfg: dict) -> dict:
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "display_name": quiz_llm.DISPLAY.get(cfg["provider"], cfg["provider"]),
        "updated_at": cfg.get("updated_at"),
        "updated_by": cfg.get("updated_by"),
        "updated_by_name": cfg.get("updated_by_name"),
    }


@router.get("/quiz/llm-config")
def quiz_llm_config_get(phone: str = Depends(require_system_admin)) -> dict:
    """获取测验模块当前 LLM 配置（仅系统管理员；业务管理员零感知）。"""
    return _quiz_llm_payload(quiz_llm.load_config())


@router.post("/quiz/llm-config")
def quiz_llm_config_set(
    req: QuizLlmConfigRequest, phone: str = Depends(require_system_admin)
) -> dict:
    """切换测验模型：先探测可用性（约 1~3s），失败拒绝并返回原因（仅系统管理员）。"""
    try:
        probe_model = quiz_llm.probe(req.provider)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"模型探测失败：{type(e).__name__}: {e}",
        )
    user = get_current_user(phone) or {}
    cfg = quiz_llm.save_config(req.provider, phone, user.get("name") or "")
    payload = _quiz_llm_payload(cfg)
    payload["probe_model"] = probe_model
    return payload





@router.get("/quiz/list")
def quiz_list(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    scene: str | None = Query(None, max_length=30),
    phone: str = Depends(require_quiz_stats),
) -> dict:
    """管理端测验列表（含基础统计）；可按可选月份/场景筛选。"""
    quiz_db.sync_expired()
    items = []
    for q in quiz_db.list_quizzes(month=month, scene=scene):
        total = quiz_db.count_questions(q["id"])
        items.append({
            "quiz_id": q["id"],
            "month": q["month"],
            "scene": q.get("scene") or "",
            "title": q["title"],
            "status": q["status"],
            "valid_from": q.get("valid_from"),
            "valid_until": q.get("valid_until"),
            "questions": total,
            "targets": quiz_db.count_targets(q["id"]),
            "completed": _count_completed(q["id"], total),
            "created_at": q.get("created_at"),
        })
    return {"items": items}





def _count_completed(quiz_id: str, total: int) -> int:

    if total <= 0:

        return 0

    phones = quiz_db.answered_phones(quiz_id)

    return sum(1 for p in phones if quiz_db.count_answers(quiz_id, p) >= total)





@router.get("/quiz/stats")

def quiz_stats(

    quiz_id: str = Query(...),

    region: str | None = Query(None, max_length=30),

    q: str | None = Query(None, max_length=30),

    page: int = Query(1, ge=1),

    page_size: int = Query(50, ge=1, le=500),

    phone: str = Depends(require_quiz_stats),

) -> dict:

    """完成率统计：总体指标 + 按角色汇总 + 按区县汇总 + 分页明细（默认 50/页，支持姓名/电话搜索）。"""

    quiz = quiz_db.get_quiz(quiz_id)

    if not quiz:

        raise HTTPException(404, "测验不存在")

    actor = whitelist_db.get_user(phone)

    rows, by_region, started, completed = _stats_rows(quiz_id, region, actor)

    base_total = len(rows)



    by_role: dict[str, dict] = {}

    for r in rows:

        role = r["admin_level"]

        rg = by_role.setdefault(role, {"role": role, "label": ROLE_LABELS.get(role, role), "total": 0, "started": 0, "completed": 0})

        rg["total"] += 1

        if r["answered"] > 0:

            rg["started"] += 1

        if r["status"] == "已完成":

            rg["completed"] += 1

    for rg in by_role.values():

        rg["rate"] = round(rg["completed"] / rg["total"], 4) if rg["total"] else 0.0



    if q:

        qq = q.strip()

        rows = [r for r in rows if qq in r["name"] or qq in r["phone"]]

    total = len(rows)

    start = (page - 1) * page_size

    page_rows = rows[start : start + page_size]



    user_details = [{

        "phone": r["phone"], "name": r["name"], "province": r["province"], "city": r["city"],

        "county": r["county"], "community": r["community"], "admin_level": r["admin_level"],

        "status": r["status"], "answered": r["answered"], "score": r["score"], "total": r["total"],

        "submitted_at": r["submitted_at"],

    } for r in page_rows]



    return {

        "quiz_id": quiz_id,

        "month": quiz["month"],

        "title": quiz["title"],

        "status": quiz["status"],

        "valid_until": quiz.get("valid_until"),

        "total_users": base_total,

        "started": started,

        "completed": completed,

        "completion_rate": round(completed / base_total, 4) if base_total else 0.0,

        "by_role": sorted(by_role.values(), key=lambda r: ROLE_PRIORITY.get(r["role"], 9)),

        "by_region": sorted(by_region.values(), key=lambda r: -r["total"]),

        "page": page,

        "page_size": page_size,

        "total": total,

        "user_details": user_details,

    }





@router.get("/quiz/stats/export")

def quiz_stats_export(

    quiz_id: str = Query(...),

    region: str | None = Query(None, max_length=30),

    q: str | None = Query(None, max_length=30),

    phone: str = Depends(require_quiz_stats),

) -> Response:

    """导出完成率 Excel（全部符合条件行，不分页；含搜索/地区过滤）。"""

    quiz = quiz_db.get_quiz(quiz_id)

    if not quiz:

        raise HTTPException(404, "测验不存在")

    rows, _, _, _ = _stats_rows(quiz_id, region, whitelist_db.get_user(phone))

    if q:

        qq = q.strip()

        rows = [r for r in rows if qq in r["name"] or qq in r["phone"]]



    from openpyxl import Workbook



    wb = Workbook()

    ws = wb.active

    ws.title = "完成率"

    ws.append(STATS_COLUMNS)

    for r in rows:

        ws.append([

            r["province"], r["city"], r["county"], r["community"], r["name"], r["phone"],

            ROLE_LABELS.get(r["admin_level"], r["admin_level"]),

            f"{r['status']} {r['answered']}/{r['total']}",

        ])

    from io import BytesIO



    buf = BytesIO()

    wb.save(buf)

    buf.seek(0)

    filename = f"{quiz['month']}_{quiz.get('title', '测验')}_完成率.xlsx"

    return Response(

        content=buf.getvalue(),

        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",

        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},

    )





@router.get("/quiz/kb/search")

def quiz_kb_search(q: str = Query(..., min_length=1, max_length=50), phone: str = Depends(require_quiz_admin)) -> dict:

    """faq.json 子串搜索（要点 KB 匹配失败时手动补关联用）。"""

    qq = q.strip()

    items = []

    for it in _load_faq():

        hay = " ".join([str(it.get("question", "")), " ".join(it.get("keywords", []) or [])])

        if qq in hay:

            items.append({

                "faq_id": str(it.get("id", "")).zfill(3),

                "question": it.get("question", ""),

                "source": it.get("source", ""),

            })

        if len(items) >= 10:

            break

    return {"items": items}




# --- 场景字典 / 删除 ----------------------------------------------------------

@router.get("/quiz/scenes")
def quiz_scenes(include_inactive: bool = Query(False), phone: str = Depends(require_quiz_admin)) -> dict:
    """场景字典列表（含内置默认场景）。"""
    quiz_db.ensure_default_scenes()
    return {"items": quiz_db.list_scenes(include_inactive=include_inactive)}


@router.post("/quiz/scenes")
def quiz_scene_add(req: SceneAddRequest, phone: str = Depends(require_quiz_admin)) -> dict:
    """新增场景（重名返回 created=False，不报错）。"""
    try:
        created = quiz_db.add_scene(req.name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "created": created}


@router.post("/quiz/scenes/toggle")
def quiz_scene_toggle(req: SceneToggleRequest, phone: str = Depends(require_quiz_admin)) -> dict:
    """停用/启用场景（历史测验保留场景名）。"""
    if not quiz_db.set_scene_active(req.name, req.active):
        raise HTTPException(404, "场景不存在")
    return {"ok": True}


@router.post("/quiz/delete")
def quiz_delete(req: QuizDeleteRequest, phone: str = Depends(require_quiz_admin)) -> dict:
    """删除测验（仅 draft/reviewing；连带清要点/题目/目标/答题/导入）。"""
    quiz = quiz_db.get_quiz(req.quiz_id)
    if not quiz:
        raise HTTPException(404, "测验不存在")
    if quiz["status"] not in ("draft", "reviewing"):
        raise HTTPException(409, "仅草稿/审核中的测验可删除")
    quiz_db.delete_quiz(req.quiz_id)
    logger.info(f"quiz delete: {req.quiz_id} by={phone[:3]}****")
    return {"ok": True}

