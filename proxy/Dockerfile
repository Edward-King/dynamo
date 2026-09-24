FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# config.json is intentionally NOT copied into the image -- secrets belong
# in the environment (GALLERY_PROXY_API_KEY / GALLERY_PROXY_BASE_URL), not
# baked into a layer. A container started without those env vars and
# without a mounted config.json will fail fast with a clear error from
# load_config() at startup, rather than silently serving with no key.

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin galleryproxy \
    && chown -R galleryproxy:galleryproxy /app

USER galleryproxy

EXPOSE 8100

# proxy_host defaults to 127.0.0.1 (correct for local dev outside a
# container), which would make the service unreachable through Docker's
# port mapping even with EXPOSE/-p set. Bind 0.0.0.0 inside the container so
# the published port actually works; override at runtime if you need a
# different bind address.
ENV GALLERY_PROXY_HOST=0.0.0.0

ENTRYPOINT ["python", "main.py"]
