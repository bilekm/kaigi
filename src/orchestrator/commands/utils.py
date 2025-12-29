"""Shared utility functions for CLI commands."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import click


def _auto_start_agents(workflow, use_json: bool) -> None:
    """Auto-start any agents that aren't already running.

    This is a shared utility used by both converse and start-all commands.

    Args:
        workflow: The conversation workflow with agents to start
        use_json: If True, suppress progress output
    """
    from orchestrator.lib.agent_client import is_agent_running
    from orchestrator.lib.settings import get_agent_config
    from orchestrator.lib.agent_server import get_socket_path

    started = []

    for agent in workflow.agents:
        agent_id = agent.agent or agent.id

        if is_agent_running(agent_id):
            continue

        # Get agent config
        config = get_agent_config(agent_id)
        if not config:
            if not use_json:
                click.echo(f"Warning: Agent '{agent_id}' not found in settings, will spawn on demand")
            continue

        if not use_json:
            click.echo(f"Starting agent '{agent_id}' in background...")

        # Start in background (double-fork daemon)
        pid = os.fork()
        if pid == 0:
            os.setsid()
            pid2 = os.fork()
            if pid2 == 0:
                # Grandchild - run the server
                from orchestrator.lib.agent_server import run_agent_server

                sys.stdin.close()
                with open("/dev/null", "r") as null:
                    os.dup2(null.fileno(), 0)

                log_path = get_socket_path(agent_id).with_suffix(".log")
                with open(log_path, "w") as log:
                    os.dup2(log.fileno(), 1)
                    os.dup2(log.fileno(), 2)

                run_agent_server(agent_id, config.command, env=config.env, spawn_mode=config.spawn_mode)
                sys.exit(0)
            else:
                os._exit(0)
        else:
            os.waitpid(pid, 0)
            started.append(agent_id)

    # Wait for agents to be ready
    if started:
        if not use_json:
            click.echo("Waiting for agents to initialize...")

        max_wait = 30  # seconds
        start_time = time.time()

        while time.time() - start_time < max_wait:
            all_ready = True
            for agent_id in started:
                if not is_agent_running(agent_id):
                    all_ready = False
                    break

            if all_ready:
                break

            time.sleep(0.5)

        if not use_json:
            ready_count = sum(1 for aid in started if is_agent_running(aid))
            click.echo(f"Started {ready_count}/{len(started)} agents")
            click.echo()
