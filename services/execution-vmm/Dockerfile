FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y git build-essential

# Copy protocol package
COPY packages/protocol /packages/protocol
RUN pip install /packages/protocol

# Copy service
COPY services/execution-vmm /app
RUN pip install .

EXPOSE 8002

CMD ["python", "src/agenthub_execution_vmm/main.py"]
