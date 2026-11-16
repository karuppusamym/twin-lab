# Showcase demo app: one image, one process, one port (front end and API).

# --- front end: npm install and build happen here, never on the host ---------------------------
FROM node:20.18.0-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- API, serving the built front end ----------------------------------------------------------
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY db/schema.sql db/schema.sql
COPY backend/app backend/app
COPY --from=web /web/dist frontend/dist
# OpenShift runs the container as an arbitrary UID with group 0. The app writes nothing to disk;
# its home is group 0 with the owner's rights (g=u) for any library cache. USER is numeric so
# `runAsNonRoot` can be checked.
RUN useradd --create-home --uid 10001 --gid 0 showcase \
    && chown -R 10001:0 /home/showcase && chmod -R g=u /home/showcase
ENV HOME=/home/showcase
USER 10001
WORKDIR /app/backend
# DATABASE_URL (postgresql://user:password@host:5432/db) is required at start-up; it is not baked in.
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=40s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
