FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY webhook.py template_card.jpg ./

# Cloud hosts pass the port in $PORT
CMD uvicorn webhook:app --host 0.0.0.0 --port ${PORT:-8000}
