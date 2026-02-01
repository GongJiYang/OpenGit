FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y git build-essential

# Copy protocol package
COPY packages/protocol /packages/protocol
RUN pip install /packages/protocol

# Copy service
COPY services/semantic-store /app
RUN pip install .

EXPOSE 8001

CMD ["python", "src/agenthub_semantic_store/main.py"]
