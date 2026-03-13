#!/bin/sh
set -e

if [ -z "$WEBHOOK_KEY" ]; then
    echo "[ERROR] WEBHOOK_KEY 环境变量未设置，隧道无法启动"
    echo "[INFO]  仅启动 nginx（微信 API 代理）"
    exec nginx -g "daemon off;"
fi

echo "============================================"
echo "  Misaka Danmu VPS Relay"
echo "============================================"
echo "  隧道端口: ${TUNNEL_PORT:-9001}"
echo "  认证密钥: ${WEBHOOK_KEY:0:4}****"
echo "============================================"

# 启动 wstunnel 服务端（后台）
echo "[INFO] 启动 wstunnel server ..."
wstunnel server \
    --restrict-http-upgrade-path-prefix "wstunnel/${WEBHOOK_KEY}" \
    ws://127.0.0.1:8443 &

WSTUNNEL_PID=$!

# 等待 wstunnel 就绪
sleep 1
if ! kill -0 $WSTUNNEL_PID 2>/dev/null; then
    echo "[ERROR] wstunnel 启动失败"
    exit 1
fi
echo "[INFO] wstunnel server 已就绪 (PID: $WSTUNNEL_PID)"

# 用 envsubst 替换 nginx 配置中的环境变量
export TUNNEL_PORT="${TUNNEL_PORT:-9001}"
if [ -f /etc/nginx/http.d/default.conf.template ]; then
    envsubst '${TUNNEL_PORT}' < /etc/nginx/http.d/default.conf.template > /etc/nginx/http.d/default.conf
fi

# 启动 nginx（前台）
echo "[INFO] 启动 nginx ..."
exec nginx -g "daemon off;"

