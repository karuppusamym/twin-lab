# Twin Lab lab agent: one image, run beside every demo app.
# Build from the repository root (labctl is copied in for its contract models):
#   docker build -f lab/lab-agent/Dockerfile -t twinlab/lab-agent:dev .
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/opt/twinlab/lab-agent:/opt/twinlab/labctl \
    LAB_PORT=9000 \
    DB_PROXY_PORT=15432 \
    STATE_DIR=/var/lib/twinlab
WORKDIR /opt/twinlab
COPY lab/lab-agent/requirements.txt lab-agent/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r lab-agent/requirements.txt 2>/dev/null \
    || pip install --no-cache-dir --no-deps -r lab-agent/requirements.txt
COPY lab/labctl/labctl labctl/labctl
COPY lab/lab-agent/lab_agent lab-agent/lab_agent
# OpenShift runs the container as an arbitrary UID with group 0, so what the agent writes belongs
# to group 0 with the owner's rights (g=u). USER is numeric so `runAsNonRoot` can be checked.
# /var/lib/twinlab-db holds a SQLite lab's database file on a volume the demo app mounts too; a
# new volume takes this folder's permissions, open to the app's user (which may differ).
RUN useradd --create-home --uid 10002 --gid 0 labagent \
    && mkdir -p /var/lib/twinlab /var/lib/twinlab-db \
    && chown -R 10002:0 /var/lib/twinlab /var/lib/twinlab-db /home/labagent \
    && chmod -R g=u /var/lib/twinlab /home/labagent \
    && chmod 0777 /var/lib/twinlab-db \
    && python -c "import lab_agent.app, lab_agent.sqlite_init, labctl.models"
ENV HOME=/home/labagent
USER 10002
VOLUME ["/var/lib/twinlab"]
EXPOSE 9000 15432
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,urllib.request as u; r=u.Request('http://127.0.0.1:%s/_lab/health' % os.environ.get('LAB_PORT','9000'), headers={'X-Lab-Token': os.environ.get('LAB_TOKEN','')}); sys.exit(0 if u.urlopen(r, timeout=4).status == 200 else 1)"
CMD ["python", "-m", "lab_agent"]
