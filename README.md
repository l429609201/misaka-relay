# Misaka Danmu VPS Relay

在 [wxchat-Docker](https://github.com/DDSRem-Dev/wxchat-Docker) 基础上扩展，集成 wstunnel WebSocket 隧道，让没有公网 IP 的弹幕库通过 VPS 接收 webhook 回调。

## 架构

```
[微信/Telegram/Emby 回调]
  → [VPS nginx :52000]
      ├── /wstunnel/*        → wstunnel server :8443 (WebSocket 隧道端点)
      ├── /api/notification/ → localhost:9001 (反向隧道 → 本地弹幕库)
      ├── /api/webhook/      → localhost:9001 (反向隧道 → 本地弹幕库)
      └── /cgi-bin/*         → qyapi.weixin.qq.com (企业微信 API 代理)

[本地弹幕库]
  → wstunnel client 连接 VPS :52000
  → 反向隧道: VPS:9001 → 本地:7768
```

## VPS 端部署

### 方式一：Docker Compose（推荐）

```compose
services:
  misaka-relay:
    image: l429609201/misaka-relay:latest
    container_name: misaka-relay
    restart: unless-stopped
    ports:
      - "52000:80"  # 外部端口:容器端口（可修改 52000 为其他端口）
    environment:
      # 必填：与弹幕库 Webhook API Key 保持一致
      - WEBHOOK_KEY=your_webhook_api_key_here
      # 时区设置
      - TZ=Asia/Shanghai
```


### 方式二：Docker Run

```bash
docker run -d \
  --name misaka-relay \
  --restart unless-stopped \
  -p 52000:80 \
  -e WEBHOOK_KEY=your_webhook_api_key_here \
  -e TUNNEL_PORT=9001 \
  -e TZ=Asia/Shanghai \
  l429609201/misaka-relay:latest
```

---
## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `WEBHOOK_KEY` | ✅ | 空 | 隧道认证密钥，与弹幕库 API Key 一致 |
| `TUNNEL_PORT` | ❌ | `9001` | VPS 端反向隧道监听端口 |

---

## 注意事项

外部请求通过 VPS 的 **52000 端口**，经 Docker 映射到容器内部 **80 端口**，再通过 wstunnel 反向隧道转发至本地弹幕库的 **7768 端口**。

