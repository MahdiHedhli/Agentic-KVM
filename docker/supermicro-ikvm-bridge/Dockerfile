FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    default-jre \
    fluxbox \
    icedtea-netx \
    novnc \
    websockify \
    x11vnc \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m bridge

COPY docker/supermicro-ikvm-bridge/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

USER bridge
WORKDIR /home/bridge

EXPOSE 6080

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
