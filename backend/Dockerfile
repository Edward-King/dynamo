FROM python:3.12-slim

WORKDIR /app

COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY photoshare/ ./photoshare/
COPY config.prod.yaml .

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin photoshare \
    && mkdir -p /photos_root /var/lib/photoshare/cache /var/log/photoshare \
    && chown -R photoshare:photoshare /app /photos_root /var/lib/photoshare /var/log/photoshare

USER photoshare

EXPOSE 8000

# The entry point keeps --config as the public interface.  It internally
# propagates that path to Uvicorn worker subprocesses via PHOTOSHARE_CONFIG_PATH.
ENTRYPOINT ["python", "-m", "photoshare", "--config", "config.prod.yaml"]
