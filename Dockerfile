FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 yourtube \
    && mkdir -p /data && chown yourtube:yourtube /data

ENV YTKB_DATA_DIR=/data
EXPOSE 8765
USER yourtube
CMD ["python", "dashboard.py", "--no-open", "--host", "0.0.0.0"]
