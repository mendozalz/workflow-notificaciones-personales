FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY listener.py .

RUN mkdir -p /data
ENV STATE_PATH=/data/seen.json
CMD ["python", "-u", "listener.py"]
