"""2026-06-21 起停用：路由注册在 main.py 已注释。本模块代码完整保留，供未来恢复。

原功能：/api/voice/stream 端点 — 浏览器 PCM 16k → 讯飞实时转写 → 流式识别结果。
"""
from __future__ import annotations

import asyncio
import json

import websocket
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.api._xunfei_auth import build_xunfei_asr_url
from app.core.config import settings

router = APIRouter()

EXPECTED_FRAME_BYTES = 1280  # 浏览器按 40ms / 1280B 发送 PCM16LE

# 与讯飞握手时的合法业务码
_OK_CODE = "0"
# 客户端 WS 协议字符串（浏览器 mic.js / 小程序共用）
CLIENT_END_SENTINEL = "__end__"
# 内部队列哨兵：触发 xunfei_to_client 退出
_QUEUE_CLOSE = object()


def _extract_text(payload: dict) -> str:
    """从讯飞识别结果 JSON 抽出已识别全文。路径：data.cn.st.rt[].ws[].cw[].w。"""
    try:
        st = payload["data"]["cn"]["st"]
    except (KeyError, TypeError):
        return ""
    parts: list[str] = []
    for sentence in st.get("rt", []):
        for word in sentence.get("ws", []):
            for cw in word.get("cw", []):
                w = cw.get("w", "")
                if w:
                    parts.append(w)
    return "".join(parts)


def _is_error_msg(data: dict) -> bool:
    """判定一条讯飞消息是否为可上报前端的错误。"""
    if data.get("type") == "error":
        return True
    code = data.get("code")
    return "code" in data and code is not None and code != _OK_CODE and code != 0


def _send_end(xf_ws, session_id: str | None) -> None:
    """给讯飞发 end 信号。已发过则跳过，避免重连后重复发送。"""
    payload: dict = {"end": True}
    if session_id:
        payload["sessionId"] = session_id
    try:
        xf_ws.send(json.dumps(payload, ensure_ascii=False), opcode=websocket.ABNF.OPCODE_TEXT)
    except Exception:
        pass


@router.websocket("/api/voice/stream")
async def voice_stream(client_ws: WebSocket) -> None:
    await client_ws.accept()

    if not (settings.xunfei_app_id and settings.xunfei_api_key and settings.xunfei_api_secret):
        await client_ws.send_text(json.dumps(
            {"type": "error", "code": "NO_CRED", "msg": "讯飞凭据未配置"},
            ensure_ascii=False,
        ))
        await client_ws.close()
        return

    url = build_xunfei_asr_url(
        app_id=settings.xunfei_app_id,
        api_key=settings.xunfei_api_key,
        api_secret=settings.xunfei_api_secret,
        pd_domain=settings.xunfei_asr_domain,
    )
    logger.info("连接讯飞 ASR: host=office-api-ast-dx.iflyaisol.com")

    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    xf_started = asyncio.Event()
    xf_session_id: str | None = None
    end_sent = False

    def set_sid(sid: str) -> None:
        nonlocal xf_session_id
        xf_session_id = sid

    def push(item: dict) -> None:
        try:
            out_queue.put_nowait(item)
        except asyncio.QueueFull:
            logger.warning("讯飞结果队列满，丢弃一条")

    def on_open(ws):
        logger.info("讯飞 WS 已建立")

    def on_message(ws, message):
        if isinstance(message, bytes):
            return
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        # 握手阶段返回的 sessionId
        if data.get("msg_type") == "action" and isinstance(data.get("data"), dict):
            sid = data["data"].get("sessionId")
            if sid:
                set_sid(sid)
                xf_started.set()
        loop.call_soon_threadsafe(push, data)

    def on_error(ws, error):
        msg = str(error)
        # websocket-client 在 server 主动关闭（code=1000）时也会触发 on_error，
        # 错误信息里含 "opcode=8"（close frame），不算错误。
        if "opcode=8" in msg or "1000" in msg:
            logger.info(f"讯飞 WS 正常关闭: {msg[:80]}")
            return
        logger.error(f"讯飞 WS 错误: {msg}")
        loop.call_soon_threadsafe(push, {"type": "error", "msg": msg})

    def on_close(ws, code, msg):
        logger.info(f"讯飞 WS 关闭: code={code} msg={msg}")
        loop.call_soon_threadsafe(out_queue.put_nowait, _QUEUE_CLOSE)

    xf_ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    runner = loop.run_in_executor(None, xf_ws.run_forever)

    async def client_to_xunfei() -> None:
        """浏览器 → 讯飞：转 PCM 帧，发结束信号。"""
        nonlocal end_sent
        try:
            while True:
                msg = await client_ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    xf_ws.send(msg["bytes"], opcode=websocket.ABNF.OPCODE_BINARY)
                elif msg.get("text") == CLIENT_END_SENTINEL:
                    try:
                        await asyncio.wait_for(xf_started.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        logger.warning("未收到讯飞 sessionId，直接发 end")
                    _send_end(xf_ws, xf_session_id)
                    end_sent = True
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"client→讯飞 异常: {e}")
        finally:
            # 用户提前松手 / 浏览器断开，强制结束讯飞会话（只发一次）
            if not end_sent:
                _send_end(xf_ws, xf_session_id)
                end_sent = True

    async def xunfei_to_client() -> None:
        """讯飞 → 浏览器：透传 + 抽出文本 + 标记最终结果。"""
        last_text = ""
        try:
            while True:
                data = await out_queue.get()
                if data is _QUEUE_CLOSE:
                    break

                text = _extract_text(data)
                is_last = bool(data.get("data", {}).get("ls", False))
                if text and text != last_text:
                    last_text = text
                    await client_ws.send_text(json.dumps({
                        "type": "partial",
                        "text": text,
                        "final": is_last,
                    }, ensure_ascii=False))
                elif is_last:
                    # 没有新文本但 ls=true，仍通知前端
                    await client_ws.send_text(json.dumps({
                        "type": "partial",
                        "text": last_text,
                        "final": True,
                    }, ensure_ascii=False))

                if _is_error_msg(data):
                    await client_ws.send_text(json.dumps({
                        "type": "error",
                        "code": data.get("code"),
                        "msg": data.get("desc") or data.get("msg") or "讯飞错误",
                    }, ensure_ascii=False))
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"讯飞→client 异常: {e}")

    try:
        await asyncio.gather(client_to_xunfei(), xunfei_to_client())
    finally:
        try:
            xf_ws.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(runner, timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            await client_ws.close()
        except Exception:
            pass