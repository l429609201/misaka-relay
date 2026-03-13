FROM alpine:3.23

ENV TZ=Asia/Shanghai
ENV WEBHOOK_KEY=""
ENV TUNNEL_PORT=9001

RUN apk add --no-cache tzdata nginx python3 py3-pip gettext \
    && pip install --no-cache-dir --break-system-packages aiohttp \
    && rm -rf /var/cache/apk/* /root/.cache

COPY --chmod=755 ./rootfs /

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]

