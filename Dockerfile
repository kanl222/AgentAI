FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home agent && mkdir -p /app/data && chown agent:agent /app/data
COPY instructions ./instructions
COPY skills ./skills
COPY web ./web
COPY scripts ./scripts
COPY examples ./examples
USER agent
CMD ["uvicorn", "agentai.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
