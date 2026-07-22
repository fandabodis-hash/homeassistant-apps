FROM ghcr.io/home-assistant/base:latest

RUN apk add --no-cache \
    bash \
    dbus \
    dnsmasq \
    iproute2 \
    iw \
    jq \
    networkmanager-cli \
    python3 \
    yq

WORKDIR /app

COPY rootfs /

RUN chmod a+x /etc/services.d/iqfanda/run
