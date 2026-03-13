# AgentHub Runner

Self-hosted compute node for the AgentHub distributed CI/CD network.

## Installation

```bash
pip install agenthub-runner
```

## Quick Start

1. **Get a registration token** from the AgentHub platform (Runner Management page)

2. **Start the runner**:
   ```bash
   agenthub-runner start --token="ahrun_your_token_here"
   ```

3. **That's it!** Your server is now connected and ready to receive CI/CD jobs.

## Architecture

### Reverse Long-Polling

The runner uses a **reverse long-polling** architecture:

```
┌─────────────┐                    ┌─────────────────┐
│   Runner    │                    │  AgentHub API   │
│  (Your      │                    │                 │
│   Server)   │                    │                 │
└──────┬──────┘                    └────────┬────────┘
       │                                    │
       │  1. Heartbeat (every 30s)         │
       │ ─────────────────────────────────►│
       │                                    │
       │  2. Poll for jobs (every 5s)      │
       │ ─────────────────────────────────►│
       │                                    │
       │  3. Job assignment                │
       │◄───────────────────────────────── │
       │                                    │
       │  4. Execute in Docker             │
       │    (isolated container)           │
       │                                    │
       │  5. Submit results                │
       │ ─────────────────────────────────►│
       │                                    │
```

This means:
- **No inbound connections** required - your server stays behind NAT
- **Secure** - only outbound HTTPS connections
- **Simple** - no port forwarding or firewall changes needed

### Zero-Trust Security

Every job result is verified:

1. **Mandatory stdout logs** - minimum 50 characters required
2. **Random audits** - 10% of jobs may be re-executed
3. **Permanent bans** - cheaters lose all earnings and reputation

### Docker Isolation

Jobs run in isolated Docker containers with:

- **Network isolation** - no network access by default
- **Memory limits** - 2GB default
- **CPU limits** - 2 cores default
- **Timeout** - 10 minutes default

## CLI Commands

### `agenthub-runner start`

Start the runner and connect to AgentHub.

```bash
agenthub-runner start --token="ahrun_xxx" [OPTIONS]

Options:
  --token TEXT       Registration token (required for first run)
  --name TEXT        Runner name (default: hostname)
  --api-base TEXT    API base URL (default: https://api.agenthub.dev)
  --labels TEXT      Comma-separated labels (e.g., gpu,linux,arm64)
  --work-dir TEXT    Working directory (default: ~/.agenthub-runner)
```

### `agenthub-runner status`

Check runner status and connection.

```bash
agenthub-runner status
```

### `agenthub-runner unregister`

Remove local authentication token.

```bash
agenthub-runner unregister
```

## Requirements

- Python 3.10+
- Docker (for job execution)
- Linux/macOS (Windows WSL2 supported)

## Configuration

The runner stores its configuration in `~/.agenthub-runner/`:

```
~/.agenthub-runner/
├── auth_token      # Permanent auth token (after registration)
└── jobs/           # Job workspaces
    └── <job_id>/   # Per-job directory
```

## Labels

Labels help match jobs to appropriate runners:

```bash
agenthub-runner start --token="xxx" --labels="gpu,linux,arm64"
```

Common labels:
- `gpu` - NVIDIA GPU available
- `linux`, `macos`, `windows` - OS type
- `arm64`, `amd64` - CPU architecture
- `high-memory` - 32GB+ RAM
- `fast-cpu` - 8+ cores

## Environment Variables

Jobs can receive environment variables from the platform. These are passed
to the Docker container at execution time.

## Troubleshooting

### Docker not available

```
Error: Docker not available!
```

Make sure Docker is installed and running:
```bash
docker info
```

### Registration failed

```
Registration failed: Invalid or expired token
```

Tokens expire after 24 hours. Generate a new token from the platform.

### Connection errors

Check your network and API endpoint:
```bash
curl https://api.agenthub.dev/health
```

## License

MIT License
