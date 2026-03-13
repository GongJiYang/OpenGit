FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y git build-essential libbcrypt-dev

# Copy entire project logic because Gateway imports everything as libraries
COPY packages /app/packages
COPY services /app/services
COPY apps/api-gateway /app/apps/api-gateway

# Install dependencies
RUN pip install fastapi uvicorn pydantic python-multipart gitpython requests httpx numpy zhipuai qdrant-client e2b-code-interpreter slowapi sqlmodel bcrypt passlib python-jose[cryptography] apscheduler sniffio psycopg2-binary aiohttp
# Ensure bcrypt is properly installed
RUN python -c "import bcrypt; print(bcrypt.__version__)" || pip install --force-reinstall bcrypt

# Install Protocol Package
RUN pip install /app/packages/protocol

# Set Working Directory to Gateway
WORKDIR /app/apps/api-gateway

# Expose Port
EXPOSE 8000

# Run Uvicorn
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
