FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.12-slim
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
USER 10001
EXPOSE 8000
# One worker on purpose: the watch state and WebSocket hub live in this process.
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
