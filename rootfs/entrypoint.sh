#!/bin/sh
set -e

if [ -z "$WEBHOOK_KEY" ]; then
    echo "[ERROR] WEBHOOK_KEY 环境变量未设置，隧道无法启动"
    echo "[INFO]  仅启动 nginx（微信 API 代理）"
    exec nginx -g "daemon off;"
fi

echo "============================================"
echo "  Misaka Relay"
echo "============================================"
echo "  隧道端口: ${TUNNEL_PORT:-9001}"
echo "  认证密钥: ${WEBHOOK_KEY:0:4}****"
echo "============================================"

# 启动 relay.py（后台）
echo "[INFO] 启动 relay.py ..."
TUNNEL_PORT="${TUNNEL_PORT:-9001}" python3 /relay.py &
RELAY_PID=$!

# 等待 relay 就绪
sleep 1
if ! kill -0 $RELAY_PID 2>/dev/null; then
    echo "[ERROR] relay.py 启动失败"
    exit 1
fi
echo "[INFO] relay.py 已就绪 (PID: $RELAY_PID)"

# 用 envsubst 替换 nginx 配置中的 WEBHOOK_KEY
if [ -f /etc/nginx/http.d/default.conf.template ]; then
    envsubst '${WEBHOOK_KEY}' < /etc/nginx/http.d/default.conf.template > /etc/nginx/http.d/default.conf
fi

# 启动 nginx（前台）
echo "[INFO] 启动 nginx ..."
exec nginx -g "daemon off;"


