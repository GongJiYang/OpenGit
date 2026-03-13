"""
AgentHub Runner - Self-hosted compute node for distributed CI/CD

This package provides the runner client that connects to the AgentHub platform
and executes CI/CD jobs on your own infrastructure.

Usage:
    pip install agenthub-runner
    agenthub-runner start --token="your-registration-token"

Architecture:
    - Reverse long-polling: Runner polls platform every 5 seconds for jobs
    - Zero-Trust: All results require meaningful stdout logs
    - Docker isolation: Jobs run in isolated containers with resource limits
"""

__version__ = "0.1.0"

from .runner import RunnerClient
from .docker_executor import DockerExecutor

__all__ = ["RunnerClient", "DockerExecutor", "__version__"]
