FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY ops/autoscaler/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY ops/autoscaler/autoscaler.py /app/autoscaler.py

CMD ["python", "/app/autoscaler.py"]
