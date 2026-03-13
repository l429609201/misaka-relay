FROM alpine:3.23

ENV TZ=Asia/Shanghai
ENV WEBHOOK_KEY=""
ENV TUNNEL_PORT=9001

RUN apk add --no-cache tzdata nginx python3 py3-aiohttp gettext \
    && rm -rf /var/cache/apk/*

COPY --chmod=755 ./rootfs /

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]

