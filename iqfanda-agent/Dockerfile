FROM ghcr.io/home-assistant/base@sha256:880016115f356afc814398e17ba68a7ee66dfc1b4eaef09755ea2c9fa18ea481

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
