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
    py3-pip \
    py3-pyserial \
    py3-websocket-client \
    yq

RUN python3 -m pip install \
    --no-cache-dir \
    --target /opt/iqfanda-python \
    pymodbus==3.14.0

ENV PYTHONPATH="/opt/iqfanda-python"

WORKDIR /app

COPY rootfs /

RUN chmod a+x /etc/services.d/iqfanda/run
