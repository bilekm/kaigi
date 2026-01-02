"""Agent management commands - agents group and subcommands."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click

from kaigi.lib.errors import OrchestratorError


def json_option(f):
    """Add --json option to a command."""
    return click.option(
        "--json",
        "use_json",
        is_flag=True,
        help="Output in JSON format",
    )(f)


# === Agent Group ===

@click.group()
def agents() -> None:
    """Manage AI agent configurations."""
    pass


# === Agent Settings Commands ===

@agents.command("list")
@json_option
def agents_list(use_json: bool) -> None:
    """List all configured AI agents."""
    from kaigi.lib.settings import (
        list_available_agents,
        get_global_settings_path,
        get_project_settings_path,
    )

    agents = list_available_agents()

    if use_json:
        output = {
            "agents": [
                {
                    "name": name,
                    "command": config.command,
                    "description": config.description,
                    "timeout": config.timeout,
                }
                for name, config in agents
            ],
            "global_settings": str(get_global_settings_path()),
            "project_settings": str(get_project_settings_path()) if get_project_settings_path() else None,
        }
        click.echo(json.dumps(output, indent=2))
    else:
        global_path = get_global_settings_path()
        project_path = get_project_settings_path()

        click.echo("Agent Settings:")
        click.echo(f"  Global: {global_path}")
        if project_path:
            click.echo(f"  Project: {project_path}")
        click.echo()

        if not agents:
            click.echo("No agents configured.")
            click.echo()
            click.echo("Create ~/.kaigi/agents.yaml with:")
            click.echo()
            click.echo("  agents:")
            click.echo("    claude:")
            click.echo('      command: claude')
            click.echo('      args: ["-p", "{{prompt}}", "--output-format", "text"]')
            return

        click.echo(f"{'NAME':<15} {'COMMAND':<15} {'DESCRIPTION':<40}")
        click.echo("-" * 70)
        for name, config in agents:
            desc = config.description[:40] if config.description else ""
            click.echo(f"{name:<15} {config.command:<15} {desc:<40}")


@agents.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing settings")
@click.option("--project", is_flag=True, help="Create in .kaigi/ (project-level)")
def agents_init(force: bool, project: bool) -> None:
    """Create a default agents.yaml settings file.

    By default creates global settings in ~/.kaigi/agents.yaml.
    Use --project to create project-level settings in .kaigi/agents.yaml.
    Project settings override global settings.
    """
    from kaigi.lib.settings import get_global_settings_path, ensure_global_settings_dir

    if project:
        # Create in current directory
        project_dir = Path.cwd() / ".kaigi"
        project_dir.mkdir(parents=True, exist_ok=True)
        settings_path = project_dir / "agents.yaml"
    else:
        ensure_global_settings_dir()
        settings_path = get_global_settings_path()

    if settings_path.exists() and not force:
        click.echo(f"Settings file already exists: {settings_path}")
        click.echo("Use --force to overwrite.")
        sys.exit(1)

    default_settings = '''\
# Orchestrator Agent Settings
# Configure AI agents that can be used in conversation workflows.
#
# Usage in workflow.yaml:
#   agents:
#     - id: my-agent
#       agent: claude    # Reference by name
#       model: opus-4    # Override model (optional)
#       persona: "..."
#
# Environment variables can be referenced with ${VAR_NAME} syntax.

agents:
  # Claude Code CLI
  claude:
    command: claude
    args: ["-p", "{{prompt}}", "--output-format", "text"]
    timeout: 300
    spawn_mode: true
    description: "Claude Code CLI"
    # model: claude-sonnet-4-5  # Default model (optional)

  # GitHub Copilot CLI
  # Requires: npm install -g @github/copilot && GitHub auth
  copilot:
    command: copilot
    args: ["-p", "{{prompt}}"]
    timeout: 300
    spawn_mode: true
    description: "GitHub Copilot CLI"
    # model: gemini-3-pro-preview  # Select model

  # GLM via Z.AI (OpenAI-compatible endpoint)
  # Requires: export ZAI_API_KEY=your-key
  # glm:
  #   command: claude
  #   args: ["-p", "{{prompt}}", "--output-format", "text"]
  #   env:
  #     ANTHROPIC_BASE_URL: "https://api.z.ai/api/coding/paas/v4"
  #     ANTHROPIC_AUTH_TOKEN: "${ZAI_API_KEY}"
  #   timeout: 300
  #   description: "GLM-4.7 via Z.AI"
'''

    settings_path.write_text(default_settings)
    click.echo(f"Created: {settings_path}")
    click.echo()
    if project:
        click.echo("Project-level settings created.")
        click.echo("These override global settings (~/.kaigi/agents.yaml).")
    else:
        click.echo("Configure your agents by editing the file.")
        click.echo("Use --project to create project-specific settings.")


@agents.command("show")
@click.argument("name")
@json_option
def agents_show(name: str, use_json: bool) -> None:
    """Show details for a specific agent."""
    from kaigi.lib.settings import get_agent_config

    config = get_agent_config(name)

    if not config:
        if use_json:
            click.echo(json.dumps({"error": f"Agent '{name}' not found"}))
        else:
            click.echo(f"Agent '{name}' not found.")
        sys.exit(1)

    if use_json:
        output = {
            "name": name,
            "command": config.command,
            "args": config.args,
            "env": {k: "***" if "TOKEN" in k or "KEY" in k else v for k, v in config.env.items()},
            "timeout": config.timeout,
            "description": config.description,
            "model": config.model,
        }
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(f"Agent: {name}")
        click.echo(f"  Command: {config.command}")
        click.echo(f"  Args: {' '.join(config.args)}")
        if config.model:
            click.echo(f"  Model: {config.model}")
        click.echo(f"  Timeout: {config.timeout}s")
        if config.description:
            click.echo(f"  Description: {config.description}")
        if config.env:
            click.echo("  Environment:")
            for k, v in config.env.items():
                # Mask sensitive values
                display_v = "***" if "TOKEN" in k or "KEY" in k else v
                click.echo(f"    {k}: {display_v}")


# === Persistent Agent Commands ===

def _detect_terminal_emulator() -> list[str] | None:
    """Detect available terminal emulator."""
    # Common terminal emulators in order of preference
    terminals = [
        (["gnome-terminal", "--"], "gnome-terminal"),
        (["konsole", "-e"], "konsole"),
        (["xfce4-terminal", "-e"], "xfce4-terminal"),
        (["xterm", "-e"], "xterm"),
        (["terminator", "-e"], "terminator"),
        (["alacritty", "-e"], "alacritty"),
        (["kitty", "--"], "kitty"),
    ]

    for cmd, name in terminals:
        if shutil.which(name):
            return cmd

    return None


def _start_agent_in_terminal(agent_id: str, command: str, args: list[str], env: dict[str, str]) -> bool:
    """Start an agent in a new visible terminal window."""
    import shlex

    terminal_cmd = _detect_terminal_emulator()
    if not terminal_cmd:
        click.echo("Error: No terminal emulator found.")
        click.echo("Install gnome-terminal, konsole, xterm, or similar.")
        return False

    # Build the command to run in the terminal
    # We need to run the orchestrator agents start command in foreground
    # Use full path since terminal may not have the same PATH
    orchestrator_path = shutil.which("orchestrator")
    if orchestrator_path:
        agent_cmd = [orchestrator_path, "agents", "start", agent_id]
    else:
        # Fallback: use python -m
        agent_cmd = [sys.executable, "-m", "orchestrator", "agents", "start", agent_id]

    # Build full command
    full_cmd = terminal_cmd + agent_cmd

    try:
        # Start the terminal (it will run independently)
        subprocess.Popen(
            full_cmd,
            env={**dict(os.environ), **env},
            start_new_session=True,
        )
        return True
    except Exception as e:
        click.echo(f"Error starting terminal: {e}")
        return False


@agents.command("start")
@click.argument("name")
@click.option("--id", "agent_id", help="Custom agent ID (default: same as name)")
@click.option("--background", "-b", is_flag=True, help="Run in background (daemonize)")
@click.option("--terminal", "-t", is_flag=True, help="Open in new visible terminal window")
def agents_start(name: str, agent_id: str | None, background: bool, terminal: bool) -> None:
    """Start a persistent agent server.

    Starts the agent in interactive mode and exposes it via Unix socket.
    Other processes can then communicate with it instantly.

    Examples:

        # Start Claude agent in foreground (current terminal)
        orchestrator agents start claude

        # Start in background (hidden)
        orchestrator agents start claude --background

        # Start in new visible terminal window
        orchestrator agents start claude --terminal

        # Start with custom ID
        orchestrator agents start claude --id claude-1
    """
    from kaigi.lib.settings import get_agent_config
    from kaigi.lib.agent_client import is_agent_running

    config = get_agent_config(name)
    if not config:
        click.echo(f"Agent '{name}' not found in settings.")
        click.echo("Run 'orchestrator agents list' to see available agents.")
        sys.exit(1)

    agent_id = agent_id or name

    if is_agent_running(agent_id):
        click.echo(f"Agent '{agent_id}' is already running.")
        click.echo("Use 'orchestrator agents stop {agent_id}' to stop it first.")
        sys.exit(1)

    # Build command - for persistent agent (interactive mode):
    # - Remove -p/--print (non-interactive flag)
    # - Remove {{prompt}} placeholder
    # - Remove --output-format (only works with -p)
    command = config.command
    env = config.env
    args = []
    skip_next = False
    for arg in config.args:
        if skip_next:
            skip_next = False
            continue
        # Skip -p/--print and its argument (the prompt placeholder)
        if arg in ("-p", "--print"):
            skip_next = True
            continue
        # Skip --output-format (only for non-interactive mode)
        if arg == "--output-format":
            skip_next = True
            continue
        # Skip any arg containing the prompt placeholder
        if "{{prompt}}" in arg:
            continue
        args.append(arg)

    if terminal:
        # Open in new terminal window
        click.echo(f"Opening terminal for agent '{agent_id}'...")

        if _start_agent_in_terminal(agent_id, command, args, env):
            click.echo(f"Terminal opened for '{agent_id}'")
            # Wait a moment and check if it started
            time.sleep(3)
            if is_agent_running(agent_id):
                click.echo(f"Agent '{agent_id}' is running.")
            else:
                click.echo(f"Agent may still be initializing. Check the terminal window.")
        else:
            sys.exit(1)

    elif background:
        # Daemonize
        from kaigi.lib.agent_server import get_socket_path, get_pid_path

        click.echo(f"Starting agent '{agent_id}' in background...")

        # Fork and run the server
        pid = os.fork()
        if pid == 0:
            # Child - become session leader and fork again
            os.setsid()
            pid2 = os.fork()
            if pid2 == 0:
                # Grandchild - run the server
                from kaigi.lib.agent_server import run_agent_server

                # Redirect stdio
                sys.stdin.close()
                with open("/dev/null", "r") as null:
                    os.dup2(null.fileno(), 0)

                log_path = get_socket_path(agent_id).with_suffix(".log")
                with open(log_path, "w") as log:
                    os.dup2(log.fileno(), 1)
                    os.dup2(log.fileno(), 2)

                run_agent_server(agent_id, command, args=args, env=env, spawn_mode=config.spawn_mode)
                sys.exit(0)
            else:
                # Second parent - exit
                os._exit(0)
        else:
            # First parent - wait for child
            os.waitpid(pid, 0)

            # Wait a moment for server to start
            time.sleep(2)

            if is_agent_running(agent_id):
                socket_path = get_socket_path(agent_id)
                click.echo(f"Agent '{agent_id}' started.")
                click.echo(f"Socket: {socket_path}")
            else:
                log_path = get_socket_path(agent_id).with_suffix(".log")
                click.echo(f"Agent failed to start. Check log: {log_path}")
                sys.exit(1)
    else:
        # Foreground - run directly
        from kaigi.lib.agent_server import run_agent_server, get_socket_path

        click.echo(f"Starting agent '{agent_id}' in foreground...")
        click.echo(f"Socket: {get_socket_path(agent_id)}")
        click.echo("Press Ctrl+C to stop.")
        click.echo()

        try:
            run_agent_server(agent_id, command, args=args, env=env, spawn_mode=config.spawn_mode)
        except KeyboardInterrupt:
            click.echo("\nAgent stopped.")


@agents.command("start-all")
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--background", "-b", is_flag=True, help="Run all in background (default)")
@click.option("--terminal", "-t", is_flag=True, help="Open each agent in visible terminal")
def agents_start_all(workflow_file: Path | None, background: bool, terminal: bool) -> None:
    """Start all agents defined in a workflow file.

    If no workflow file is specified, uses .kaigi/workflow.yaml.

    Examples:

        # Start all agents in background (default)
        orchestrator agents start-all

        # Start all agents in visible terminals
        orchestrator agents start-all --terminal

        # Start agents from specific workflow
        orchestrator agents start-all examples/my-workflow.yaml --terminal
    """
    from kaigi.services.parser import parse_workflow_file
    from kaigi.models.workflow import ConversationWorkflow
    from kaigi.lib.settings import get_agent_config
    from kaigi.lib.agent_client import is_agent_running
    from kaigi.lib.agent_server import get_socket_path

    # Default to project config
    if workflow_file is None:
        workflow_file = Path.cwd() / ".kaigi" / "workflow.yaml"
        if not workflow_file.exists():
            click.echo("No workflow file specified and .kaigi/workflow.yaml not found.")
            click.echo("Usage: orchestrator agents start-all <workflow-file>")
            sys.exit(1)

    try:
        workflow = parse_workflow_file(workflow_file)
    except Exception as e:
        click.echo(f"Error parsing workflow: {e}")
        sys.exit(1)

    if not isinstance(workflow, ConversationWorkflow):
        click.echo("Workflow must be a conversation workflow (mode: conversation)")
        sys.exit(1)

    click.echo(f"Workflow: {workflow.name}")
    click.echo(f"Agents: {', '.join(a.agent or a.id for a in workflow.agents)}")
    click.echo()

    started = []
    skipped = []
    failed = []

    for agent in workflow.agents:
        agent_id = agent.agent or agent.id

        if is_agent_running(agent_id):
            click.echo(f"  [{agent_id}] Already running")
            skipped.append(agent_id)
            continue

        config = get_agent_config(agent_id)
        if not config:
            click.echo(f"  [{agent_id}] Not found in settings - skipped")
            failed.append(agent_id)
            continue

        # Filter args: remove -p/--print, {{prompt}}, and --output-format
        filtered_args = []
        skip_next = False
        for arg in config.args:
            if skip_next:
                skip_next = False
                continue
            # Skip -p/--print and its argument
            if arg in ("-p", "--print"):
                skip_next = True
                continue
            # Skip --output-format (only for non-interactive mode)
            if arg == "--output-format":
                skip_next = True
                continue
            # Skip any arg containing the prompt placeholder
            if "{{prompt}}" in arg:
                continue
            filtered_args.append(arg)

        if terminal:
            click.echo(f"  [{agent_id}] Opening terminal...")
            if _start_agent_in_terminal(agent_id, config.command, filtered_args, config.env):
                started.append(agent_id)
            else:
                failed.append(agent_id)
        else:
            # Background mode (default)
            click.echo(f"  [{agent_id}] Starting in background...")

            pid = os.fork()
            if pid == 0:
                os.setsid()
                pid2 = os.fork()
                if pid2 == 0:
                    from kaigi.lib.agent_server import run_agent_server

                    sys.stdin.close()
                    with open("/dev/null", "r") as null:
                        os.dup2(null.fileno(), 0)

                    log_path = get_socket_path(agent_id).with_suffix(".log")
                    with open(log_path, "w") as log:
                        os.dup2(log.fileno(), 1)
                        os.dup2(log.fileno(), 2)

                    run_agent_server(agent_id, config.command, args=filtered_args, env=config.env, spawn_mode=config.spawn_mode)
                    sys.exit(0)
                else:
                    os._exit(0)
            else:
                os.waitpid(pid, 0)
                started.append(agent_id)

    # Wait for agents to be ready
    if started:
        click.echo()
        click.echo("Waiting for agents to initialize...")

        max_wait = 30
        start_time = time.time()

        while time.time() - start_time < max_wait:
            ready = sum(1 for aid in started if is_agent_running(aid))
            if ready == len(started):
                break
            time.sleep(0.5)

        click.echo()

    # Summary
    click.echo("Summary:")
    if started:
        ready_count = sum(1 for aid in started if is_agent_running(aid))
        click.echo(f"  Started: {ready_count}/{len(started)}")
    if skipped:
        click.echo(f"  Already running: {len(skipped)}")
    if failed:
        click.echo(f"  Failed: {len(failed)}")

    # Show running agents
    click.echo()
    click.echo("Running agents:")
    for agent in workflow.agents:
        agent_id = agent.agent or agent.id
        status = "running" if is_agent_running(agent_id) else "not running"
        click.echo(f"  {agent_id}: {status}")


@agents.command("stop")
@click.argument("agent_id", required=False)
@click.option("--all", "stop_all", is_flag=True, help="Stop all running agents")
def agents_stop(agent_id: str | None, stop_all: bool) -> None:
    """Stop a running persistent agent.

    Examples:

        # Stop specific agent
        orchestrator agents stop claude

        # Stop all running agents
        orchestrator agents stop --all
    """
    from kaigi.lib.agent_client import stop_agent, stop_all_agents, list_running_agents

    if stop_all:
        count = stop_all_agents()
        click.echo(f"Stopped {count} agent(s).")
        return

    if not agent_id:
        # Show running agents and ask
        running = list_running_agents()
        if not running:
            click.echo("No agents running.")
            return

        click.echo("Running agents:")
        for agent in running:
            click.echo(f"  {agent['id']} (PID: {agent['pid']})")
        click.echo()
        click.echo("Specify an agent ID or use --all to stop all.")
        return

    from kaigi.lib.agent_client import is_agent_running

    if not is_agent_running(agent_id):
        click.echo(f"Agent '{agent_id}' is not running.")
        sys.exit(1)

    if stop_agent(agent_id):
        click.echo(f"Agent '{agent_id}' stopped.")
    else:
        click.echo(f"Failed to stop agent '{agent_id}'.")
        sys.exit(1)


@agents.command("restart")
@click.argument("name")
@click.option("--id", "agent_id", help="Custom agent ID (default: same as name)")
@click.option("--background", "-b", is_flag=True, help="Run in background (daemonize)")
@click.option("--terminal", "-t", is_flag=True, help="Open in new visible terminal window")
def agents_restart(name: str, agent_id: str | None, background: bool, terminal: bool) -> None:
    """Restart a persistent agent server.

    Stops the agent if running, then starts it again.

    Examples:

        # Restart Claude agent in foreground
        orchestrator agents restart claude

        # Restart in background
        orchestrator agents restart claude --background

        # Restart in new terminal window
        orchestrator agents restart claude --terminal

        # Restart with custom ID
        orchestrator agents restart claude --id claude-1
    """
    from kaigi.lib.agent_client import is_agent_running, stop_agent
    from kaigi.lib.settings import get_agent_config

    config = get_agent_config(name)
    if not config:
        click.echo(f"Agent '{name}' not found in settings.")
        click.echo("Run 'orchestrator agents list' to see available agents.")
        sys.exit(1)

    target_id = agent_id or name

    # Stop if running
    if is_agent_running(target_id):
        click.echo(f"Stopping agent '{target_id}'...")
        if stop_agent(target_id):
            click.echo(f"Agent '{target_id}' stopped.")
        else:
            click.echo(f"Failed to stop agent '{target_id}'.")
            sys.exit(1)
        # Brief pause for cleanup
        import time
        time.sleep(0.5)
    else:
        click.echo(f"Agent '{target_id}' was not running.")

    # Start the agent
    click.echo(f"Starting agent '{target_id}'...")

    # Build command - for persistent agent (interactive mode):
    # - Remove -p/--print (non-interactive flag)
    # - Remove {{prompt}} placeholder
    # - Remove --output-format (only works with -p)
    command = config.command
    env = config.env
    args = []
    skip_next = False
    for arg in config.args:
        if skip_next:
            skip_next = False
            continue
        # Skip -p/--print and its argument (the prompt placeholder)
        if arg in ("-p", "--print"):
            skip_next = True
            continue
        # Skip --output-format (only for non-interactive mode)
        if arg == "--output-format":
            skip_next = True
            continue
        # Skip any arg containing the prompt placeholder
        if "{{prompt}}" in arg:
            continue
        args.append(arg)

    if terminal:
        # Open in new terminal window
        click.echo(f"Opening terminal for agent '{target_id}'...")

        if _start_agent_in_terminal(target_id, command, args, env):
            click.echo(f"Terminal opened for '{target_id}'")
            # Wait a moment and check if it started
            import time
            time.sleep(1)
            if is_agent_running(target_id):
                click.echo(f"Agent '{target_id}' restarted successfully.")
            else:
                click.echo(f"Agent '{target_id}' may not have started properly.")
        else:
            click.echo(f"Failed to open terminal for '{target_id}'.")
            sys.exit(1)
    else:
        # Start in foreground or background
        if background:
            # Daemonize: fork to background
            args = [command] + args
            env_dict = os.environ.copy()
            env_dict.update(env)

            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env_dict,
                start_new_session=True,  # Detach from parent
            )

            # Brief pause to let it start
            import time
            time.sleep(0.5)

            if is_agent_running(target_id):
                click.echo(f"Agent '{target_id}' restarted successfully (PID: {process.pid}).")
            else:
                click.echo(f"Agent '{target_id}' failed to start.")
                sys.exit(1)
        else:
            # Foreground - for restart, we still want to daemonize since the old one is stopped
            # This makes restart behave consistently
            args = [command] + args
            env_dict = os.environ.copy()
            env_dict.update(env)

            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env_dict,
                start_new_session=True,
            )

            import time
            time.sleep(0.5)

            if is_agent_running(target_id):
                click.echo(f"Agent '{target_id}' restarted successfully (PID: {process.pid}).")
                click.echo("Use 'orchestrator agents running' to verify.")
            else:
                click.echo(f"Agent '{target_id}' may not have started properly.")


@agents.command("running")
@json_option
def agents_running(use_json: bool) -> None:
    """List running persistent agent servers."""
    from kaigi.lib.agent_client import list_running_agents

    running = list_running_agents()

    if use_json:
        click.echo(json.dumps(running, indent=2))
        return

    if not running:
        click.echo("No agents running.")
        click.echo()
        click.echo("Start an agent with: orchestrator agents start <name>")
        return

    click.echo(f"{'AGENT ID':<20} {'PID':<10} {'SOCKET'}")
    click.echo("-" * 70)
    for agent in running:
        click.echo(f"{agent['id']:<20} {agent['pid'] or 'N/A':<10} {agent['socket']}")


@agents.command("ping")
@click.argument("agent_id")
def agents_ping(agent_id: str) -> None:
    """Test connectivity to a running agent."""
    import asyncio
    from kaigi.lib.agent_client import AgentClient, AgentNotRunning

    async def do_ping():
        client = AgentClient(agent_id)
        try:
            await client.connect()
            if await client.ping():
                click.echo(f"Agent '{agent_id}' is responsive.")
                return True
            else:
                click.echo(f"Agent '{agent_id}' did not respond.")
                return False
        except AgentNotRunning as e:
            click.echo(f"Agent '{agent_id}' is not running: {e}")
            return False
        finally:
            await client.close()

    success = asyncio.run(do_ping())
    sys.exit(0 if success else 1)


@agents.command("test")
@click.argument("agent_id")
@click.argument("message")
def agents_test(agent_id: str, message: str) -> None:
    """Send a test message to a running agent.

    Examples:

        orchestrator agents test claude "Hello, how are you?"
    """
    import asyncio
    from kaigi.lib.agent_client import send_prompt, AgentNotRunning

    async def do_test():
        try:
            def on_chunk(chunk: str) -> None:
                click.echo(chunk, nl=False)

            click.echo(f"Sending to '{agent_id}'...")
            click.echo("-" * 40)

            response = await send_prompt(agent_id, message, on_chunk=on_chunk)

            click.echo()
            click.echo("-" * 40)
            click.echo(f"Response: {len(response)} chars")
            return True

        except AgentNotRunning as e:
            click.echo(f"Error: {e}")
            return False
        except TimeoutError as e:
            click.echo(f"Timeout: {e}")
            return False

    success = asyncio.run(do_test())
    sys.exit(0 if success else 1)
