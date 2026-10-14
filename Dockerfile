# Organizer must preload this parent image. Pin their published digest for final submission.
FROM python:3.12-slim-bookworm AS participant
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
WORKDIR /app
ARG TARGETARCH
COPY vendor/uv/ /uv-tools/
RUN python -c 'import pathlib,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); n=next(n for n in z.namelist() if n.endswith(".data/scripts/uv")); pathlib.Path("/usr/local/bin/uv").write_bytes(z.read(n))' "/uv-tools/uv-${TARGETARCH}.whl" \
    && chmod 755 /usr/local/bin/uv && rm -rf /uv-tools
COPY requirements.txt /app/requirements.txt
COPY vendor/wheels/ /wheels/
RUN uv pip install --system --offline --no-cache --no-index --find-links=/wheels -r /app/requirements.txt \
    && rm -rf /wheels
COPY src/chess_sdk/ /app/chess_sdk/
COPY agent.py /app/agent.py
COPY assets/ /app/assets/
USER 65532:65532
ENTRYPOINT ["python", "-m", "chess_sdk", "run", "--agent", "agent:choose"]

# Optional local fixture: both gateway and agent run inside one Linux container.
FROM participant AS local-check
USER 0:0
COPY requirements-check.txt /app/requirements-check.txt
COPY vendor/check-wheels/ /check-wheels/
RUN uv pip install --system --offline --no-cache --no-index --find-links=/check-wheels -r /app/requirements-check.txt \
    && rm -rf /check-wheels
USER 65532:65532
ENTRYPOINT ["python", "-m", "chess_sdk", "check"]

# Default build produces only the participant runtime.
FROM participant AS submission
