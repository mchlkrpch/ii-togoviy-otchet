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
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY parse_structure.py native_structure.py structure_semantics.py structure_edits.py visualize_structure.py ./
COPY --from=frontend /build/dist ./frontend/dist
ENV AUDIT_INPUT_DIR=/data/input AUDIT_DATA_DIR=/data/state PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8765"]
