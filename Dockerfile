FROM python:3.11-slim

WORKDIR /app

# Dependencias primero para aprovechar la caché de capas.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# La BD vive en un volumen para que persista entre reinicios.
ENV DB_PATH=/data/printcost.db \
    CONFIG_PATH=/app/config.yaml
VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
