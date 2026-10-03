FROM python:3.12-slim

ARG OPIP_RELEASE_SHA=UNPINNED
LABEL org.opencontainers.image.revision="${OPIP_RELEASE_SHA}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tradingview-alert-example.json .
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
