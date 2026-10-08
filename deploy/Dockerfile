FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
# The Parquet snapshot is baked into the image (put the 22 files in deploy-data/ before deploying), so the server never downloads at runtime.
COPY deploy-data /data
ENV CFPB_DATA_DIR=/data
RUN python -m cfpb_complaints.mcp_server --check
ENV CFPB_ALLOWED_HOSTS=cfpb-complaints-analysis.fly.dev CFPB_MEMORY_LIMIT=350MB CFPB_THREADS=1 CFPB_QUERY_TIMEOUT=50 CFPB_RATE_PER_MIN=40
USER nobody
EXPOSE 8080
CMD ["python", "-m", "cfpb_complaints.mcp_server", "--http"]
