ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    bash \
    dbus \
    iproute2 \
    iw \
    jq \
    yq \
    python3 \
    networkmanager-cli \
    dnsmasq

COPY rootfs /

RUN chmod a+x \
    /etc/services.d/iqf-host-agent/run \
    /etc/services.d/iqf-host-agent/finish \
    /etc/services.d/iqf-host-api/run \
    /etc/services.d/iqf-host-api/finish
