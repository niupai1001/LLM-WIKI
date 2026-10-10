FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY station ./station
COPY config ./config
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "station.web:app", "--host", "0.0.0.0", "--port", "8765"]
