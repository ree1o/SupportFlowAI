FROM python:3.12.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && groupadd --system supportflow \
    && useradd --system --gid supportflow supportflow \
    && mkdir -p /var/cache/supportflow/models \
    && chown -R supportflow:supportflow /var/cache/supportflow
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
USER supportflow
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM runtime AS test
USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.txt
COPY pyproject.toml ./
COPY tests ./tests
USER supportflow
CMD ["pytest", "-p", "no:cacheprovider"]
