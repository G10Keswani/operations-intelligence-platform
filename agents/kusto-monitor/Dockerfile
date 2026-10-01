ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY kusto_monitor ./kusto_monitor
COPY checks.yaml .

RUN useradd --create-home agent
USER agent

# Container Apps Job runs this once per schedule tick.
ENTRYPOINT ["python", "-m", "kusto_monitor.agent"]
CMD ["--once"]
