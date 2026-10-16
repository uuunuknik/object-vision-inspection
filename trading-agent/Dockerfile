FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.29" "pydantic>=2"
COPY agent.py notify.py ./
ENV DB_PATH=/data/agent.db PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
# One worker only: the risk checks and the daily LINE summary must run in a single process.
CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
