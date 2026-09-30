FROM python:3.12-alpine3.24
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apk add --no-cache tesseract-ocr tesseract-ocr-data-chi_sim \
    tesseract-ocr-data-eng poppler-utils libreoffice-impress font-wqy-zenhei
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
RUN apk add --no-cache libreoffice-writer
COPY . .
RUN adduser -D app && mkdir -p /app/local-data && chown -R app:app /app
USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
