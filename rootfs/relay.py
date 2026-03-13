#!/usr/bin/env python3
"""
misaka-relay — VPS 端 WebSocket 反向隧道中继服务

架构：
  [nginx :80] --/ws/--> [relay.py :8443] <-- WebSocket -- [弹幕库 tunnel_service]
  [nginx :80] --/api/notification/--> [relay.py HTTP :9001] --> 隧道 --> [弹幕库 :7768]

协议（HTTP over WebSocket）：
  1. 弹幕库建立控制 WS:  ws://VPS/ws/ctrl/{key}
  2. 回调 HTTP 请求到达 relay :9001 时，relay 通过控制 WS 发送完整请求信息:
       {"type":"new_conn","id":"<uuid>","method":"GET","path":"/api/...","headers":{...},"body":"<hex>"}
  3. 弹幕库新建数据 WS:  ws://VPS/ws/data/{key}/{id}
  4. 弹幕库把完整 HTTP 响应通过数据 WS 发回:
       {"status":200,"headers":{...},"body":"<hex>"}
  5. relay 还原为 HTTP 响应返回给 nginx
"""
import asyncio
import hmac
import json
import logging
import os
import uuid
from typing import Optional

from aiohttp import web, WSMsgType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [relay] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("relay")

WEBHOOK_KEY: str = os.environ.get("WEBHOOK_KEY", "")
RELAY_HTTP_PORT: int = int(os.environ.get("TUNNEL_PORT", "9001"))
WS_PORT: int = 8443

# 控制连接（同一时间只有一个弹幕库连接）
_ctrl_ws: Optional[web.WebSocketResponse] = None

# 等待数据连接：conn_id -> asyncio.Future[(web.WebSocketResponse, asyncio.Event)]
_pending_data: dict[str, asyncio.Future] = {}


def _check_key(key: str) -> bool:
    if not WEBHOOK_KEY:
        log.warning("WEBHOOK_KEY 未配置，拒绝所有连接")
        return False
    return hmac.compare_digest(key, WEBHOOK_KEY)


# ──────────────────────────────────────────────────────────────
# WebSocket 路由（弹幕库侧，:8443 via nginx /ws/）
# ──────────────────────────────────────────────────────────────

async def handle_ctrl(request: web.Request) -> web.WebSocketResponse:
    """弹幕库控制连接：持久 WebSocket，用于通知新回调请求"""
    global _ctrl_ws
    key = request.match_info["key"]
    if not _check_key(key):
        return web.Response(status=403, text="Forbidden")

    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    if _ctrl_ws is not None and not _ctrl_ws.closed:
        await _ctrl_ws.close()
    _ctrl_ws = ws
    log.info("控制连接已建立，弹幕库在线")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        if _ctrl_ws is ws:
            _ctrl_ws = None
        log.info("控制连接断开")

    return ws


async def handle_data(request: web.Request) -> web.WebSocketResponse:
    """弹幕库数据连接：每个回调请求对应一个短暂 WebSocket"""
    key = request.match_info["key"]
    conn_id = request.match_info["conn_id"]
    if not _check_key(key):
        return web.Response(status=403, text="Forbidden")

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    fut = _pending_data.get(conn_id)
    if fut is None or fut.done():
        log.warning("收到未知 conn_id 的数据连接: %s", conn_id[:8])
        await ws.close()
        return ws

    close_event = asyncio.Event()
    fut.set_result((ws, close_event))
    log.debug("数据连接就绪: %s", conn_id[:8])

    # 等待 handle_callback 用完后发出关闭信号，不在此处 receive()（避免并发冲突）
    await close_event.wait()

    return ws


# ──────────────────────────────────────────────────────────────
# HTTP 服务（接收 nginx proxy_pass 转发的回调，:9001）
# ──────────────────────────────────────────────────────────────

async def handle_callback(request: web.Request) -> web.Response:
    """
    接收 nginx 转发的回调 HTTP 请求，序列化后通过控制 WS 发给弹幕库，
    弹幕库处理完成后通过数据 WS 返回响应，relay 还原为 HTTP 响应。
    """
    global _ctrl_ws

    if _ctrl_ws is None or _ctrl_ws.closed:
        log.warning("无控制连接（弹幕库未连接），返回 503")
        return web.Response(status=503, text="Tunnel not connected")

    body = await request.read()
    conn_id = str(uuid.uuid4())

    req_info = {
        "type": "new_conn",
        "id": conn_id,
        "method": request.method,
        "path": request.path_qs,
        "headers": dict(request.headers),
        "body": body.hex(),
    }

    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _pending_data[conn_id] = fut

    log.info("[%s] 回调 %s %s", conn_id[:8], request.method, request.path_qs)

    close_event: Optional[asyncio.Event] = None
    try:
        await _ctrl_ws.send_json(req_info)
        log.info("[%s] 已通知弹幕库", conn_id[:8])

        # 等待弹幕库建立数据 WS
        try:
            data_ws = await asyncio.wait_for(fut, timeout=15.0)
        except asyncio.TimeoutError:
            log.warning("[%s] 等待数据连接超时，返回 504", conn_id[:8])
            return web.Response(status=504, text="Tunnel timeout")

        data_ws, close_event = data_ws  # fut 结果是 (ws, close_event) 元组

        # 从数据 WS 接收弹幕库的 HTTP 响应
        try:
            msg = await asyncio.wait_for(data_ws.receive(), timeout=30.0)
        except asyncio.TimeoutError:
            log.warning("[%s] 等待响应超时，返回 504", conn_id[:8])
            return web.Response(status=504, text="Response timeout")

        if msg.type != WSMsgType.TEXT:
            log.warning("[%s] 收到非预期消息类型: %s", conn_id[:8], msg.type)
            return web.Response(status=502, text="Bad tunnel response")

        resp_info = json.loads(msg.data)
        status = resp_info.get("status", 200)
        resp_headers = resp_info.get("headers", {})
        resp_body = bytes.fromhex(resp_info.get("body", ""))

        # 过滤 hop-by-hop headers，避免 aiohttp 报错
        skip = {"transfer-encoding", "connection", "keep-alive", "content-encoding",
                "content-length", "server", "date"}
        clean_headers = {k: v for k, v in resp_headers.items() if k.lower() not in skip}

        log.info("[%s] 完成，状态 %d，响应 %d B", conn_id[:8], status, len(resp_body))
        return web.Response(status=status, headers=clean_headers, body=resp_body)

    except Exception as e:
        log.error("[%s] 处理异常: %s", conn_id[:8], e)
        return web.Response(status=502, text="Tunnel error")
    finally:
        _pending_data.pop(conn_id, None)
        if close_event is not None:
            close_event.set()
        if not fut.cancelled() and fut.done() and isinstance(fut.result(), tuple):
            try:
                await fut.result()[0].close()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────
# 应用入口
# ──────────────────────────────────────────────────────────────

async def main() -> None:
    if not WEBHOOK_KEY:
        log.error("WEBHOOK_KEY 环境变量未设置，退出")
        raise SystemExit(1)

    # WS 服务（弹幕库侧）监听 :8443
    ws_app = web.Application()
    ws_app.router.add_get("/ws/ctrl/{key}", handle_ctrl)
    ws_app.router.add_get("/ws/data/{key}/{conn_id}", handle_data)
    ws_runner = web.AppRunner(ws_app)
    await ws_runner.setup()
    await web.TCPSite(ws_runner, "127.0.0.1", WS_PORT).start()
    log.info("WebSocket 服务已启动: 127.0.0.1:%d", WS_PORT)

    # HTTP 服务（接收 nginx 转发的回调）监听 :9001
    http_app = web.Application()
    http_app.router.add_route("*", "/{path_info:.*}", handle_callback)
    http_runner = web.AppRunner(http_app)
    await http_runner.setup()
    await web.TCPSite(http_runner, "127.0.0.1", RELAY_HTTP_PORT).start()
    log.info("HTTP 中继服务已启动: 127.0.0.1:%d", RELAY_HTTP_PORT)
    log.info("等待弹幕库控制连接...")

    # 永久运行
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())

