#!/usr/bin/env python3
"""
misaka-relay — VPS 端 WebSocket 反向隧道中继服务

架构：
  [nginx :80] --/ws/--> [relay.py :8443] <-- WebSocket -- [弹幕库 wstunnel client]
  [nginx :80] --/api/notification/--> [relay.py TCP :9001] --> 隧道 --> [弹幕库 :7768]

协议：
  1. 弹幕库建立控制 WS:  ws://VPS/ws/ctrl/{key}
  2. TCP 连接到 9001 时，relay 通过控制 WS 发送:
       {"type": "new_conn", "id": "<uuid>"}
  3. 弹幕库新建数据 WS:  ws://VPS/ws/data/{key}/{id}
  4. relay 将 TCP 连接 ↔ 数据 WS 双向桥接
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
RELAY_TCP_PORT: int = int(os.environ.get("TUNNEL_PORT", "9001"))
WS_PORT: int = 8443

# 控制连接：key -> WebSocket（同一时间只有一个弹幕库连接）
_ctrl_ws: Optional[web.WebSocketResponse] = None

# 等待数据连接：conn_id -> asyncio.Future[web.WebSocketResponse]
_pending_data: dict[str, asyncio.Future] = {}


def _check_key(key: str) -> bool:
    if not WEBHOOK_KEY:
        log.warning("WEBHOOK_KEY 未配置，拒绝所有连接")
        return False
    # 使用常量时间比较，防止时序攻击（Timing Attack）
    return hmac.compare_digest(key, WEBHOOK_KEY)


# ──────────────────────────────────────────────────────────────
# WebSocket 路由处理
# ──────────────────────────────────────────────────────────────

async def handle_ctrl(request: web.Request) -> web.WebSocketResponse:
    """弹幕库控制连接：接受一个持久 WebSocket，用于通知新连接"""
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
            # 控制连接只接收方向不需要处理数据（弹幕库只监听）
    finally:
        if _ctrl_ws is ws:
            _ctrl_ws = None
        log.info("控制连接断开")

    return ws


async def handle_data(request: web.Request) -> web.WebSocketResponse:
    """弹幕库数据连接：每个 TCP 连接一个 WebSocket"""
    key = request.match_info["key"]
    conn_id = request.match_info["conn_id"]
    if not _check_key(key):
        return web.Response(status=403, text="Forbidden")

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    fut = _pending_data.get(conn_id)
    if fut is None or fut.done():
        log.warning("收到未知 conn_id 的数据连接: %s", conn_id)
        await ws.close()
        return ws

    fut.set_result(ws)
    log.debug("数据连接就绪: %s", conn_id)

    # 保持连接直到对端关闭（由 _bridge_tcp_ws 负责关闭）
    async for msg in ws:
        if msg.type == WSMsgType.ERROR:
            break

    return ws


# ──────────────────────────────────────────────────────────────
# TCP 服务器（接收 nginx 转发来的回调请求）
# ──────────────────────────────────────────────────────────────

async def _bridge_tcp_ws(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ws: web.WebSocketResponse,
) -> None:
    """双向桥接：TCP ↔ WebSocket"""

    async def tcp_to_ws():
        try:
            while not reader.at_eof():
                chunk = await reader.read(65536)
                if not chunk:
                    break
                await ws.send_bytes(chunk)
        except Exception:
            pass
        finally:
            await ws.close()

    async def ws_to_tcp():
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    writer.write(msg.data)
                    await writer.drain()
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    await asyncio.gather(tcp_to_ws(), ws_to_tcp(), return_exceptions=True)


async def handle_tcp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """处理 nginx 转发来的 TCP 连接"""
    global _ctrl_ws

    peer = writer.get_extra_info("peername")
    log.info("TCP 连接: %s", peer)

    if _ctrl_ws is None or _ctrl_ws.closed:
        log.warning("无控制连接，拒绝 TCP 请求 (弹幕库未连接)")
        writer.close()
        return

    conn_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _pending_data[conn_id] = fut

    try:
        # 通知弹幕库建立数据连接
        await _ctrl_ws.send_json({"type": "new_conn", "id": conn_id})

        # 等待弹幕库建立数据 WebSocket（最多 10 秒）
        try:
            data_ws = await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            log.warning("等待数据连接超时: %s", conn_id)
            writer.close()
            return

        log.info("开始桥接 TCP ↔ WS: %s", conn_id)
        await _bridge_tcp_ws(reader, writer, data_ws)
        log.debug("桥接结束: %s", conn_id)

    except Exception as e:
        log.error("TCP 处理异常: %s", e)
        writer.close()
    finally:
        _pending_data.pop(conn_id, None)


# ──────────────────────────────────────────────────────────────
# 应用入口
# ──────────────────────────────────────────────────────────────

async def main() -> None:
    if not WEBHOOK_KEY:
        log.error("WEBHOOK_KEY 环境变量未设置，退出")
        raise SystemExit(1)

    # 启动 aiohttp WebSocket 服务
    app = web.Application()
    app.router.add_get("/ws/ctrl/{key}", handle_ctrl)
    app.router.add_get("/ws/data/{key}/{conn_id}", handle_data)

    runner = web.AppRunner(app)
    await runner.setup()
    ws_site = web.TCPSite(runner, "127.0.0.1", WS_PORT)
    await ws_site.start()
    log.info("WebSocket 服务已启动: 127.0.0.1:%d", WS_PORT)

    # 启动 TCP 服务（接收 nginx 转发的回调）
    tcp_server = await asyncio.start_server(handle_tcp, "127.0.0.1", RELAY_TCP_PORT)
    log.info("TCP 中继服务已启动: 127.0.0.1:%d", RELAY_TCP_PORT)
    log.info("等待弹幕库控制连接...")

    async with tcp_server:
        await tcp_server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())

