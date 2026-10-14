FROM python:3.11-slim
WORKDIR /app

# ffmpeg: decodes whatever audio container/codec the browser's mic recorder
# sends (webm/opus, etc.) before faster-whisper/ctranslate2 sees it. Placed
# before the pip layers, as its own RUN step, so it's cached independently
# and a requirements-*.txt change never invalidates this apt layer.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Split into several small pinned-version files, each installed as its own
# RUN step (via pip-install-retry.sh), so a mid-download network failure only
# costs a retry of that one small group instead of the entire dependency set
# — Docker caches every step that completed successfully, and each step
# retries itself a few times internally before failing the build. See
# requirements-core.txt for the rationale behind the split.
#
# Order matters here: transitive-1/3/4 are mostly "leaf" packages (few
# dependencies of their own); transitive-2/core/heavy contain the "hub"
# packages (langgraph-checkpoint, langchain, chromadb, ...) that depend on
# many of those leaves. Installing leaves first means that by the time a hub
# package is processed, its dependencies are already present — so a
# corrupted download of one of them gets our per-package retry+resume
# protection instead of surfacing as an uncontrolled, unprotected dependency
# cascade off of a hub package's plain install.
COPY pip-install-retry.sh pip_wheel_fetch.py ./
RUN chmod +x pip-install-retry.sh

COPY requirements-core.txt requirements-heavy.txt requirements-transitive-1.txt requirements-transitive-2.txt requirements-transitive-3.txt requirements-transitive-4.txt requirements-ml.txt requirements-voice.txt requirements-eval.txt ./

RUN ./pip-install-retry.sh requirements-transitive-1.txt
RUN ./pip-install-retry.sh requirements-transitive-3.txt
RUN ./pip-install-retry.sh requirements-transitive-4.txt
RUN ./pip-install-retry.sh requirements-transitive-2.txt
RUN ./pip-install-retry.sh requirements-core.txt
RUN ./pip-install-retry.sh requirements-heavy.txt
RUN ./pip-install-retry.sh requirements-ml.txt
RUN pip install --default-timeout=120 --retries=10 -r requirements-voice.txt
RUN ./pip-install-retry.sh requirements-eval.txt

COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
