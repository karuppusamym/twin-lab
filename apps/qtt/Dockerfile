# QTT demo copy: one image, one process, one port (server-rendered pages and the JSON API).
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY db/schema.sql db/schema.sql
COPY app app
RUN useradd --create-home --uid 10001 qtt
USER qtt
ENV QTT_SCHEMA_PATH=/srv/db/schema.sql
# DATABASE_URL (postgresql://user:password@host:5432/db) is required at start-up; it is not baked in.
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
