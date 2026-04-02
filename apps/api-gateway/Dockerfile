FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y git build-essential

# Copy entire project logic because Gateway imports everything as libraries
COPY packages /app/packages
COPY services /app/services
COPY skills /app/skills
COPY bots /app/bots
COPY apps/api-gateway /app/apps/api-gateway

# Install dependencies
RUN pip install fastapi uvicorn pydantic pydantic-settings python-multipart gitpython requests httpx numpy zhipuai qdrant-client mem0ai slowapi sqlmodel 'bcrypt>=4.0.1,<5.0.0' passlib python-jose[cryptography] apscheduler sniffio psycopg2-binary aiohttp alembic pyyaml jinja2

# Install local monorepo packages required at runtime
RUN pip install /app/packages/protocol /app/services/git-core /app/services/semantic-store /app/services/execution-vmm

# Set Working Directory to Gateway
WORKDIR /app/apps/api-gateway

# Ensure Alembic files are present in image
COPY apps/api-gateway/alembic.ini /app/apps/api-gateway/alembic.ini
COPY apps/api-gateway/alembic /app/apps/api-gateway/alembic

# Expose Port
EXPOSE 8000

# Copy entrypoint and set permissions
COPY apps/api-gateway/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Keep runtime working directory aligned with api-gateway module path
WORKDIR /app/apps/api-gateway

# Expose Port
EXPOSE 8000

# Use entrypoint (with placeholders)
ENTRYPOINT ["/app/entrypoint.sh"]
