FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt
COPY prepare_model.py ./
RUN python prepare_model.py
COPY backend/ ./backend/
COPY parse_structure.py native_structure.py structure_semantics.py structure_edits.py visualize_structure.py ./
COPY --from=frontend /build/dist ./frontend/dist
ENV AUDIT_INPUT_DIR=/data/input AUDIT_DATA_DIR=/data/state PYTHONUNBUFFERED=1
ENV AUDIT_SETS_DIR=/data/document_sets HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 AUDIT_MODEL_THREADS=2
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8765"]
