# -*- coding: utf-8 -*-
"""scripts/compare_models.py — 用 eval_set 对多个 LLM 做 A/B（同检索、同 prompt、同评分）。

用法：
    python scripts/compare_models.py --models minimax,qwen3.5-flash,deepseek
    python scripts/compare_models.py --models minimax,qwen3.5-flash --limit 25
    python scripts/compare_models.py --probe qwen3.5-flash,qwen-flash,qwen3.6-flash   # 探测可用模型 ID

说明：只复刻 chat 主链路（越界/模糊本地拦截 -> 检索 -> LLM 生成），
评分复用 run_eval.evaluate_item（与正式回归同一套规则）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts'))

from types import SimpleNamespace  # noqa: E402

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / '.env')  # 加载根目录 .env（Key 不落盘、不打印）

from app.api.chat import (  # noqa: E402
    AMBIGUOUS_REPLY,
    OUT_OF_SCOPE_REPLY,
    _build_history_context,
    _detect_refusal,
)
from app.rag.prompts import SYSTEM_PROMPT, USER_TEMPLATE, format_kb_results  # noqa: E402
from app.rag.pure import is_ambiguous, is_in_scope, merge_query_with_history  # noqa: E402
from app.rag.retriever import retrieve  # noqa: E402
from run_eval import evaluate_item  # noqa: E402

EVAL_PATH = ROOT / 'knowledge-base' / 'qa' / 'eval_set.json'

DASHSCOPE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
MINIMAX_URL = 'https://api.minimaxi.com/v1/chat/completions'
DEEPSEEK_URL = 'https://api.deepseek.com/v1/chat/completions'

# 单价（元/百万 token），仅估算用；DeepSeek 涨价后请按实际填 --price
DEFAULT_PRICE = {
    'minimax': (0.0, 0.0),      # Token Plan 套餐内，按 0 估算
    'deepseek': (2.0, 8.0),
    'qwen': (0.2, 2.0),         # Qwen3.5-Flash 量级
}


def _provider_cfg(model: str) -> dict | None:
    """按模型名/别名解析调用配置；返回 None 表示跳过（缺 key）。"""
    key = os.environ.get
    if model == 'minimax':
        k = key('MINIMAX_API_KEY', '')
        if not k:
            print(f'  [skip] {model}: 缺 MINIMAX_API_KEY')
            return None
        return {'name': 'minimax', 'url': MINIMAX_URL, 'model': 'MiniMax-M2.7-highspeed', 'key': k}
    if model == 'deepseek':
        k = key('DEEPSEEK_API_KEY', '')
        if not k:
            print(f'  [skip] {model}: 缺 DEEPSEEK_API_KEY')
            return None
        return {'name': 'deepseek', 'url': DEEPSEEK_URL, 'model': key('DEEPSEEK_MODEL', 'deepseek-v4-flash'), 'key': k}
    # 其它视为 DashScope qwen 系列
    k = key('DASHSCOPE_API_KEY', '')
    if not k:
        print(f'  [skip] {model}: 缺 DASHSCOPE_API_KEY')
        return None
    return {'name': 'qwen', 'url': DASHSCOPE_URL, 'model': model, 'key': k}


def call_model(cfg: dict, messages: list[dict], retries: int = 3) -> tuple[str, dict]:
    """调用 LLM，网络抖动时重试（指数退避）；最终失败抛异常由上层记录。"""
    last: Exception | None = None
    for i in range(retries):
        try:
            r = requests.post(
                cfg['url'],
                headers={'Authorization': 'Bearer ' + cfg['key'], 'Content-Type': 'application/json'},
                json={'model': cfg['model'], 'messages': messages, 'temperature': 0.3, 'max_tokens': 2000, 'stream': False},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            content = data['choices'][0]['message'].get('content') or ''
            return content, data.get('usage', {})
        except requests.exceptions.RequestException as e:
            last = e
            if i < retries - 1:
                time.sleep(5 * (i + 1))
    raise last  # type: ignore[misc]


def run_one(cfg: dict, item: dict) -> dict:
    msg = item['question']
    raw_history = item.get('history') or []
    history = [SimpleNamespace(**m) if isinstance(m, dict) else m for m in raw_history]
    if not is_in_scope(msg):
        return {'mode': 'out_of_scope', 'answer': OUT_OF_SCOPE_REPLY, 'sources': []}
    if not history and is_ambiguous(msg):
        return {'mode': 'ambiguous', 'answer': AMBIGUOUS_REPLY, 'sources': []}
    merged = merge_query_with_history(msg, history)
    sources = retrieve(merged, top_k=5)
    kb_block = format_kb_results(sources)
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': USER_TEMPLATE.format(
            kb_results=kb_block,
            history_context=_build_history_context(history),
            user_message=msg,
        )},
    ]
    try:
        answer, usage = call_model(cfg, messages)
    except Exception as e:
        return {'mode': 'error', 'answer': '', 'sources': sources, 'usage': {}, 'error': str(e)}
    mode = 'out_of_kb' if _detect_refusal(answer) else 'rag'
    return {'mode': mode, 'answer': answer, 'sources': sources, 'usage': usage}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--models', default='minimax,qwen3.5-flash,deepseek')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--start', type=int, default=0, help='从第 N 题开始（0-based）')
    ap.add_argument('--end', type=int, default=None, help='到第 N 题结束（不含）')
    ap.add_argument('--probe', action='store_true', help='只探测模型 ID 是否可用（发 1 条消息）')
    ap.add_argument('--price', default=None, help='deepseek 单价 "输入,输出" 元/百万 token（覆盖默认）')
    ap.add_argument('--out', default=None, help='结果 JSON 落盘路径（可断点续看）')
    args = ap.parse_args()

    if args.price:
        pin, pout = args.price.split(',')
        DEFAULT_PRICE['deepseek'] = (float(pin), float(pout))

    models = [m.strip() for m in args.models.split(',') if m.strip()]

    # 探测模式
    if args.probe:
        for m in models:
            cfg = _provider_cfg(m)
            if not cfg:
                continue
            try:
                content, usage = call_model(cfg, [{'role': 'user', 'content': '你好，回复"OK"两个字。'}])
                print(f'  [ok] {m}: {content!r} usage={usage}')
            except Exception as e:
                print(f'  [fail] {m}: {e}')
        return 0

    items = json.loads(EVAL_PATH.read_text(encoding='utf-8'))
    items = items[args.start:] if args.start else items
    if args.end is not None:
        items = items[: args.end - args.start]
    if args.limit:
        items = items[: args.limit]
    n_llm = sum(1 for it in items if it.get('type') in ('in_kb', 'out_of_kb', 'trap'))
    print(f'总题数 {len(items)}（其中走 LLM 的 {n_llm} 题；ambiguous 无历史时本地拦截不计 LLM）\n')

    for m in models:
        cfg = _provider_cfg(m)
        if not cfg:
            continue
        t0 = time.time()
        results = []
        usage_sum = {'prompt_tokens': 0, 'completion_tokens': 0}
        all_results = []
        for i, item in enumerate(items):
            out = run_one(cfg, item)
            if out.get('mode') == 'error':
                results.append({'id': item['id'], 'type': item['type'], 'passed': False, 'reason': [{'check': 'api_error', 'passed': False, 'detail': out.get('error', '')[:200]}]})
            else:
                ev = evaluate_item(item, out)
                u = out.get('usage') or {}
                usage_sum['prompt_tokens'] += u.get('prompt_tokens', 0)
                usage_sum['completion_tokens'] += u.get('completion_tokens', 0)
                results.append({'id': item['id'], 'type': item['type'], 'passed': ev['passed'], 'reason': ev['checks']})
            all_results.append({'idx': i, 'id': item['id'], 'answer': out.get('answer', '')})
            if args.out:
                Path(args.out).write_text(json.dumps({'model': m, 'results': results, 'answers': all_results}, ensure_ascii=False, indent=1), encoding='utf-8')
        ok = sum(1 for r in results if r['passed'])
        n_err = sum(1 for r in results if r.get('reason') and r['reason'] and r['reason'][0].get('check') == 'api_error')
        by_type: dict[str, list[bool]] = defaultdict(list)
        for r in results:
            by_type[r['type']].append(r['passed'])
        pin, pout = DEFAULT_PRICE.get(cfg['name'], (0, 0))
        cost = (usage_sum['prompt_tokens'] * pin + usage_sum['completion_tokens'] * pout) / 1_000_000
        dt = time.time() - t0
        print(f'== {m} ({cfg["model"]}) ==')
        print(f'  通过 {ok}/{len(results)} = {ok / len(results):.1%}   API错误 {n_err}   用时 {dt:.0f}s')
        for t, flags in sorted(by_type.items()):
            print(f'    {t}: {sum(flags)}/{len(flags)}')
        print(f'  tokens: in={usage_sum["prompt_tokens"]} out={usage_sum["completion_tokens"]} 估算成本约{cost:.3f}元')
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
