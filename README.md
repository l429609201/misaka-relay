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
  → 反向隧道: VPS:9001 → 本地:9000
```

## VPS 端部署

### 方式一：Docker Compose（推荐）

```bash
# 1. 上传项目文件到 VPS
scp -r . user@your-vps:/opt/misaka-relay/

# 2. 修改 docker-compose.yml 中的环境变量
cd /opt/misaka-relay
nano docker-compose.yml  # 修改 WEBHOOK_KEY

# 3. 启动服务
docker compose up -d --build
```

### 方式二：Docker Run

```bash
# 构建镜像
docker build -t misaka-relay:latest .

# 运行容器
docker run -d \
  --name misaka-relay \
  --restart unless-stopped \
  -p 52000:80 \
  -e WEBHOOK_KEY=your_webhook_api_key_here \
  -e TUNNEL_PORT=9001 \
  -e TZ=Asia/Shanghai \
  misaka-relay:latest
```

---

## 本地端连接

在本地安装 wstunnel 后，运行以下命令建立反向隧道：

```bash
wstunnel client \
  -R 'tcp://[::]:9001:localhost:9000' \
  --http-upgrade-path-prefix "wstunnel/YOUR_WEBHOOK_KEY" \
  ws://your-vps-ip:52000
```

**参数说明：**
- `tcp://[::]:9001` - VPS 上监听的反向隧道端口（对应 `TUNNEL_PORT`）
- `localhost:9000` - 本地弹幕库的端口
- `wstunnel/YOUR_WEBHOOK_KEY` - 认证路径，**必须与 `WEBHOOK_KEY` 一致**
- `ws://your-vps-ip:52000` - VPS 地址（统一使用 52000 端口）

---

## 弹幕库配置

所有配置统一使用 VPS 的 **52000 端口**：

**企业微信渠道：**
- `wecom_proxy`（出站代理）：`http://your-vps-ip:52000`
- `server_url`（回调地址）：`http://your-vps-ip:52000`

**Telegram 渠道：**
- `webhook_base_url`：`http://your-vps-ip:52000`

**Emby / Sonarr Webhook：**
- Webhook URL：`http://your-vps-ip:52000/api/webhook/emby?api_key=YOUR_KEY`

---

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `WEBHOOK_KEY` | ✅ | 空 | 隧道认证密钥，与弹幕库 API Key 一致 |
| `TUNNEL_PORT` | ❌ | `9001` | VPS 端反向隧道监听端口 |

---

## 注意事项

- VPS 防火墙需开放 **52000 端口**
- 所有外部请求统一使用 52000 端口访问
- 如 52000 端口被占用，可修改 `docker-compose.yml` 或 `docker run` 命令中的端口映射
- `WEBHOOK_KEY` 不要包含 `/` 等特殊字符

