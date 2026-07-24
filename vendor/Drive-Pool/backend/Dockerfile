# ── Stage 1: Build — install dependencies into an isolated venv ───────────────
FROM python:3.11-slim AS builder

WORKDIR /app

RUN python -m venv /venv
ENV PATH=/venv/bin:$PATH

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime — copy only the venv and source, nothing else ─────────────
FROM python:3.11-slim

WORKDIR /app

# Carry over the pre-built virtual environment
COPY --from=builder /venv /venv
ENV PATH=/venv/bin:$PATH

# Copy backend source (config/ and the database are handled via Docker volumes)
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
