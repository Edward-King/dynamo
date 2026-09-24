FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# config.json is intentionally NOT copied into the image -- the admin API
# key belongs in the environment (ADMIN_PROXY_API_KEY / ADMIN_PROXY_BASE_URL),
# never baked into a layer. A container started without those env vars and
# without a mounted config.json fails fast with a clear error from
# load_config() at startup.

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin adminproxy \
    && chown -R adminproxy:adminproxy /app

USER adminproxy

EXPOSE 8200

# proxy_host defaults to 127.0.0.1 (correct for local dev outside a
# container); bind 0.0.0.0 inside the container so the published port is
# actually reachable through Docker's port mapping.
ENV ADMIN_PROXY_HOST=0.0.0.0

ENTRYPOINT ["python", "main.py"]
