FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim tesseract-ocr-chi-tra && rm -rf /var/lib/apt/lists/*
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY station ./station
COPY config ./config
COPY tests/fixtures ./tests/fixtures
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "station.web:app", "--host", "0.0.0.0", "--port", "8765"]
