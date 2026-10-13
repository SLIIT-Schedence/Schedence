# Build from the repository root so the shared packages can be copied:
#   docker build -f services/identity-service/Dockerfile -t schedence-identity-service .
# Provide configuration as environment variables from the platform's secret store.
# Never copy a .env file into the image. In AWS, use an IAM role instead of AWS_PROFILE.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# Shared packages first, from local paths (they are not on PyPI).
COPY packages/db-core /tmp/db-core
COPY packages/auth-core /tmp/auth-core
RUN pip install /tmp/db-core /tmp/auth-core && rm -rf /tmp/db-core /tmp/auth-core

COPY services/identity-service/pyproject.toml /app/pyproject.toml
RUN pip install --group /app/pyproject.toml:runtime

COPY services/identity-service/app /app/app
COPY services/identity-service/alembic /app/alembic
COPY services/identity-service/alembic.ini /app/alembic.ini

RUN useradd --system --uid 10001 schedence
USER schedence
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
