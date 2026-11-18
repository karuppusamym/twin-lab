# Loan Desk demo copy: one image, one process, one port (server-rendered pages).
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY db/schema.sql db/schema.sql
COPY app app
# Runs as an arbitrary numeric UID with group 0 (OpenShift style); the database file lives outside
# the image (DATABASE_URL=sqlite:////path), on a volume the lab also reads and writes.
RUN useradd --create-home --uid 10001 --gid 0 loandesk \
    && chown -R 10001:0 /home/loandesk && chmod -R g=u /home/loandesk
ENV HOME=/home/loandesk
USER 10001
ENV LOANDESK_SCHEMA_PATH=/srv/db/schema.sql
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
