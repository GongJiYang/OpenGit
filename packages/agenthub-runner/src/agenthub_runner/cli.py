"""
AgentHub Runner CLI

Command-line interface for the self-hosted compute runner.
"""

import asyncio
import os
import platform
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .runner import RunnerClient
from .docker_executor import DockerExecutor
from . import __version__

# Import psutil with fallback
try:
    import psutil
except ImportError:
    psutil = None

console = Console()


@click.group()
@click.version_option(version=__version__)
def main():
    """AgentHub Runner - Self-hosted compute node for distributed CI/CD."""
    pass


@main.command()
@click.option("--token", required=True, help="Registration token from AgentHub platform")
@click.option("--name", default=None, help="Runner name (default: hostname)")
@click.option("--api-base", default="https://api.agenthub.dev", help="API base URL")
@click.option("--labels", default="", help="Comma-separated labels (e.g., gpu,linux,arm64)")
@click.option("--work-dir", default="~/.agenthub-runner", help="Working directory")
def start(
    token: str,
    name: Optional[str],
    api_base: str,
    labels: str,
    work_dir: str
):
    """
    Start the runner and connect to AgentHub.

    First-time usage requires a registration token from the platform.
    After successful registration, the runner will receive a permanent auth token.
    """
    work_path = Path(work_dir).expanduser()
    work_path.mkdir(parents=True, exist_ok=True)

    # Check if already registered
    auth_file = work_path / "auth_token"
    if auth_file.exists():
        console.print("[yellow]Runner already registered. Starting...[/yellow]")
        auth_token = auth_file.read_text().strip()
    else:
        # Register with one-time token
        console.print(Panel.fit(
            "[bold cyan]AgentHub Runner Registration[/bold cyan]\n"
            f"API: {api_base}",
            border_style="cyan"
        ))

        runner = RunnerClient(api_base=api_base)

        # Gather system info
        docker_version = None
        try:
            import docker
            docker_client = docker.from_env()
            docker_version = docker_client.version()["Version"]
        except Exception:
            console.print("[red]Warning: Docker not available![/red]")

        runner_name = name or platform.node()
        label_list = [l.strip() for l in labels.split(",") if l.strip()]

        # Get memory info
        memory_gb = None
        if psutil:
            memory_gb = round(psutil.virtual_memory().total / (1024**3), 1)

        # Register
        result = asyncio.run(runner.register(
            token=token,
            name=runner_name,
            cpu_cores=os.cpu_count(),
            memory_gb=memory_gb,
            os_type=platform.system(),
            os_version=platform.release(),
            docker_version=docker_version,
            labels=label_list
        ))

        if not result.get("success"):
            console.print(f"[red]Registration failed: {result.get('detail', 'Unknown error')}[/red]")
            sys.exit(1)

        auth_token = result["auth_token"]
        auth_file.write_text(auth_token)
        auth_file.chmod(0o600)

        console.print("[green]Registration successful![/green]")
        console.print(f"Runner ID: {result['runner']['id']}")
        console.print(f"Auth token saved to: {auth_file}")

    # Start the runner loop
    console.print("\n[bold green]Starting runner loop...[/bold green]")
    asyncio.run(run_loop(api_base, auth_token, work_path))


async def run_loop(api_base: str, auth_token: str, work_path: Path):
    """Main runner loop: heartbeat, poll jobs, execute, submit results."""
    runner = RunnerClient(api_base=api_base, auth_token=auth_token)
    executor = DockerExecutor(work_path=work_path)

    console.print("[cyan]Runner started. Polling for jobs...[/cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    heartbeat_interval = 30  # seconds
    poll_interval = 5  # seconds
    last_heartbeat = 0

    while True:
        try:
            import time
            current_time = time.time()

            # Send heartbeat every 30 seconds
            if current_time - last_heartbeat >= heartbeat_interval:
                await runner.heartbeat()
                last_heartbeat = current_time
                console.print("[dim]♥ Heartbeat sent[/dim]")

            # Poll for jobs
            jobs = await runner.poll_jobs(max_jobs=1)

            if jobs:
                job = jobs[0]
                console.print(f"\n[bold yellow]Job received: {job['job_id']}[/bold yellow]")
                console.print(f"  Command: {job.get('test_command', 'N/A')}")

                # Execute job
                result = await executor.execute(job)

                # Submit result
                submit_result = await runner.submit_result(
                    job_id=job["job_id"],
                    exit_code=result["exit_code"],
                    stdout_log=result["stdout"],
                    stderr_log=result["stderr"],
                    test_results=result.get("test_results"),
                    passed=result["exit_code"] == 0
                )

                status = "[green]PASSED[/green]" if result["exit_code"] == 0 else "[red]FAILED[/red]"
                console.print(f"Job completed: {status}")
                console.print(f"  Audit triggered: {submit_result.get('audit_triggered', False)}\n")

            await asyncio.sleep(poll_interval)

        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down...[/yellow]")
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            await asyncio.sleep(10)  # Back off on error


@main.command()
@click.option("--api-base", default="https://api.agenthub.dev", help="API base URL")
def status(api_base: str):
    """Check runner status and connection."""
    work_path = Path("~/.agenthub-runner").expanduser()
    auth_file = work_path / "auth_token"

    if not auth_file.exists():
        console.print("[red]Runner not registered. Run 'agenthub-runner start --token=xxx' first.[/red]")
        return

    auth_token = auth_file.read_text().strip()
    runner = RunnerClient(api_base=api_base, auth_token=auth_token)

    result = asyncio.run(runner.heartbeat())

    table = Table(title="Runner Status")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Server Time", result.get("server_time", "N/A"))
    table.add_row("Next Heartbeat", f"{result.get('next_heartbeat_seconds', 30)}s")

    console.print(table)


@main.command()
def unregister():
    """Remove local authentication (does not disable on platform)."""
    work_path = Path("~/.agenthub-runner").expanduser()
    auth_file = work_path / "auth_token"

    if auth_file.exists():
        auth_file.unlink()
        console.print("[green]Local auth token removed.[/green]")
    else:
        console.print("[yellow]No auth token found.[/yellow]")


if __name__ == "__main__":
    main()
