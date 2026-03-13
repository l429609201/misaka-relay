FROM alpine:3.23

ARG WSTUNNEL_VERSION=10.5.2
ARG TARGETARCH

ENV TZ=Asia/Shanghai
ENV WEBHOOK_KEY=""
ENV TUNNEL_PORT=9001

RUN apk add --no-cache tzdata nginx curl tar gettext && \
    # 下载 wstunnel 二进制
    ARCH=$(case "${TARGETARCH}" in \
        amd64) echo "amd64" ;; \
        arm64) echo "arm64" ;; \
        *) echo "amd64" ;; \
    esac) && \
    curl -fsSL "https://github.com/erebe/wstunnel/releases/download/v${WSTUNNEL_VERSION}/wstunnel_${WSTUNNEL_VERSION}_linux_${ARCH}.tar.gz" \
        -o /tmp/wstunnel.tar.gz && \
    tar -xzf /tmp/wstunnel.tar.gz -C /usr/local/bin/ wstunnel && \
    chmod +x /usr/local/bin/wstunnel && \
    rm -rf /tmp/* /var/cache/apk/*

COPY --chmod=755 ./rootfs /

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]

