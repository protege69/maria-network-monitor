FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache \
        python3 \
        py3-pip \
        iputils \
        net-snmp-tools \
    && python3 -m venv /opt/maria-venv \
    && /opt/maria-venv/bin/pip install --no-cache-dir \
        routeros-api==0.21.0 \
        paho-mqtt==2.1.0 \
        aioipp==0.19.3

WORKDIR /app

COPY maria_monitor.py /app/maria_monitor.py
COPY dashboard.py /app/dashboard.py
COPY frontend /app/frontend
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
