FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
ENV DEBIAN_FRONTEND=noninteractive
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg xvfb openbox x11-utils x11vnc novnc websockify pulseaudio pulseaudio-utils fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /tmp/pulse && chown -R pwuser:pwuser /data /tmp/pulse
WORKDIR /app
ENV PATH="/opt/venv/bin:${PATH}"
COPY requirements.lock .
RUN python -m venv --without-pip /opt/venv \
    && pip --python /opt/venv install --no-cache-dir -r requirements.lock
COPY shared ./shared
COPY pipeline ./pipeline
COPY capture ./capture
RUN chmod +x capture/entrypoint.sh
USER pwuser
ENTRYPOINT ["/app/capture/entrypoint.sh"]
