FROM ghcr.io/home-assistant/base:latest

RUN apk add --no-cache python3

WORKDIR /app

COPY rootfs /

RUN chmod a+x /etc/services.d/iqfanda/run