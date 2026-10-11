FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY buriline ./buriline
RUN pip install --no-cache-dir .
ENV BURILINE_DATA_DIR=/data BURILINE_AUDIT_FILE=/data/audit.jsonl BURILINE_OLLAMA_URL=http://ollama:11434
VOLUME ["/data"]
EXPOSE 8800
CMD ["buriline", "serve", "--host", "0.0.0.0", "--port", "8800"]
