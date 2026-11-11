FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb
COPY deploy/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock
COPY pyproject.toml README.md LICENSE /app/
COPY kanban_mcp /app/kanban_mcp
RUN pip install --no-build-isolation --no-deps /app && groupadd --gid 10001 kanban && useradd --uid 10001 --gid 10001 --no-create-home kanban && mkdir /data && chown kanban:kanban /data && chmod 700 /data
COPY --chmod=0644 deploy/operations.py deploy/web.py deploy/team_control.py /opt/kanban/
RUN chmod 0755 /opt/kanban
ENV KANBAN_BACKEND=sqlite KANBAN_SQLITE_PATH=/data/kanban.db \
    KANBAN_PROJECT_DIR=/workspace/project PYTHONUNBUFFERED=1 HOME=/tmp
LABEL ai.yolofi.kanban.mcp-protocol="1" \
    org.opencontainers.image.source="https://github.com/multidimensionalcats/kanban-mcp" \
    org.opencontainers.image.revision="49061b866ad6020b9eee9d9c43fa304061952960"
USER 10001:10001
WORKDIR /data
CMD ["sh", "-c", "python /opt/kanban/operations.py initialize && exec gunicorn --chdir /opt/kanban --bind 0.0.0.0:5000 --workers 1 --threads 4 web:app"]
