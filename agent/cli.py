"""RTEC ReAct Agent CLI."""

import os
import click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.markup import escape
import json

from .config import AgentConfig, APPS_DIR, NVIDIA_BASE_URL
from .core.agent import RTECAgent
from .core.qa_agent import QAAgent
from .core.session import RouterSession
from .tools import (
    generate_gold,
    get_vocabulary,
    list_apps,
    get_syntax_docs,
)


def _make_config(
    model: str,
    max_iter: int,
    nvidia: bool,
    api_key: str | None,
    base_url: str | None,
    **kwargs,
) -> AgentConfig:
    """Build AgentConfig, resolving NVIDIA shortcut when requested."""
    if nvidia:
        resolved_key = api_key or os.environ.get("NVIDIA_API_KEY")
        if not resolved_key:
            raise click.UsageError(
                "--nvidia requires NVIDIA_API_KEY env var or --api-key"
            )
        return AgentConfig(
            model=model,
            max_iterations=max_iter,
            api_key=resolved_key,
            base_url=NVIDIA_BASE_URL,
            **kwargs,
        )
    return AgentConfig(
        model=model,
        max_iterations=max_iter,
        api_key=api_key or None,
        base_url=base_url or None,
        **kwargs,
    )


def _resolve_request(app: str, request: str) -> tuple[str, bool]:
    """Resolve a request string to actual NL text.

    If `request` matches a key in the app's vocabulary patterns, return the
    corresponding NL description (looked up from vocabulary.yaml). Otherwise
    return the raw string unchanged.

    Returns:
        (resolved_text, was_lookup) — was_lookup is True when the request was
        a pattern key, False when it was passed verbatim.
    """
    try:
        vocab = get_vocabulary(app)
        if request in vocab.patterns:
            return vocab.patterns[request].strip(), True
    except Exception:
        pass
    return request, False


console = Console()


def print_thinking(text: str):
    """Display agent's thinking."""
    console.print(Panel(
        Markdown(text),
        title="🧠 Thinking",
        border_style="blue"
    ))


def print_tool_call(name: str, args: dict):
    """Display a tool call."""
    args_str = json.dumps(args, indent=2) if args else "{}"
    console.print(Panel(
        f"[bold]{name}[/bold]\n{args_str}",
        title="🔧 Tool Call",
        border_style="yellow"
    ))


def print_tool_result(name: str, result: str):
    """Display tool result."""
    # Truncate long results
    if len(result) > 1000:
        result = result[:1000] + "\n... (truncated)"
    
    try:
        # Try to pretty-print JSON
        parsed = json.loads(result)
        result = json.dumps(parsed, indent=2)
    except:
        pass
    
    console.print(Panel(
        escape(result),   # don't let '[fluent=value]' be parsed as Rich markup
        title=f"📋 Result: {name}",
        border_style="green"
    ))


@click.group()
def cli():
    """RTEC ReAct Agent - Generate event descriptions from natural language."""
    pass


@cli.command()
def apps():
    """List available applications."""
    app_list = list_apps()
    if not app_list:
        console.print("[yellow]No applications found.[/yellow]")
        console.print(f"Add applications to: {APPS_DIR}")
    else:
        console.print("[bold]Available applications:[/bold]")
        for app in app_list:
            console.print(f"  • {app}")


@cli.command()
@click.argument('app')
def vocab(app: str):
    """Show vocabulary for an application."""
    try:
        v = get_vocabulary(app)
        console.print(Panel(
            f"[bold]Events:[/bold] {', '.join(v.events)}\n"
            f"[bold]Fluents:[/bold] {', '.join(v.fluents)}",
            title=f"Vocabulary: {app}"
        ))
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@cli.command()
def syntax():
    """Show RTEC syntax documentation."""
    docs = get_syntax_docs()
    console.print(Markdown(docs))


@cli.command()
@click.argument('app')
def gold(app: str):
    """Generate gold standard intervals from expert rules."""
    console.print(f"[yellow]Generating gold intervals for '{app}'...[/yellow]")
    try:
        result = generate_gold(app)
        console.print(f"[green]{result}[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")



@cli.command()
@click.argument('app')
@click.option('--model', default='gpt-4o', help='LLM model to use')
@click.option('--max-iter', default=10, help='Maximum iterations')
@click.option('--verbose/--quiet', default=True, help='Show detailed output')
@click.option('--nvidia', is_flag=True, help='Use NVIDIA NIM API (reads NVIDIA_API_KEY)')
@click.option('--api-key', default=None, help='Override API key')
@click.option('--base-url', default=None, help='Override API base URL')
def chat(app: str, model: str, max_iter: int, verbose: bool,
         nvidia: bool, api_key: str | None, base_url: str | None):
    """Start interactive chat session with the agent."""

    config = _make_config(
        model=model, max_iter=max_iter, nvidia=nvidia,
        api_key=api_key, base_url=base_url,
        show_thinking=verbose, show_tool_calls=verbose, show_tool_results=verbose,
    )
    
    # Create callbacks based on verbosity
    if verbose:
        agent = RTECAgent(
            config=config,
            on_thinking=print_thinking,
            on_tool_call=print_tool_call,
            on_tool_result=print_tool_result,
        )
    else:
        agent = RTECAgent(config=config)
    
    console.print(Panel(
        f"Working on: [bold]{app}[/bold]\n"
        f"Model: {model}\n"
        f"Max iterations: {max_iter}\n\n"
        "Type your request, or 'quit' to exit.",
        title="🤖 RTEC Agent",
        border_style="cyan"
    ))
    
    while True:
        try:
            user_input = console.input("\n[bold green]You:[/bold green] ").strip()
            
            if user_input.lower() in ('quit', 'exit', 'q'):
                console.print("[yellow]Goodbye![/yellow]")
                break
            if not user_input:
                continue
            
            console.print()
            state = agent.run(app, user_input)
            
            console.print()
            if state.converged:
                console.print(f"[bold green]✅ Converged! F1 = {state.last_eval.micro_f1:.3f}[/bold green]")
            elif state.last_eval:
                console.print(f"[yellow]📊 Current F1 = {state.last_eval.micro_f1:.3f}[/yellow]")
            
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Type 'quit' to exit.[/yellow]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


@cli.command()
@click.argument('app')
def tasks(app: str):
    """List all fluent task descriptions defined in vocabulary.yaml.

    Each entry can be passed directly to 'run' as a pattern key instead of
    typing out the full NL description.

    \b
    Example:
      python -m agent.cli tasks toy
      python -m agent.cli run toy happy   # uses the 'happy' pattern
    """
    try:
        vocab = get_vocabulary(app)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return

    if not vocab.patterns:
        console.print(
            f"[yellow]No patterns defined in {app}/vocabulary.yaml.[/yellow]\n"
            "Add a 'patterns:' block with fluent names as keys and NL "
            "descriptions as values."
        )
        return

    console.print(f"\n[bold]Fluent task descriptions for '{app}':[/bold]\n")
    for key, description in vocab.patterns.items():
        console.print(f"  [bold cyan]{key}[/bold cyan]")
        for line in description.strip().splitlines():
            console.print(f"    [dim]{line}[/dim]")
        console.print()
    console.print(
        "[dim]Run any of these with: "
        f"python -m agent.cli run {app} <key>[/dim]"
    )


@cli.command()
@click.argument('app')
@click.argument('request')
@click.option('--model', default='gpt-4o', help='LLM model to use')
@click.option('--max-iter', default=10, help='Maximum iterations')
@click.option('--nvidia', is_flag=True, help='Use NVIDIA NIM API (reads NVIDIA_API_KEY)')
@click.option('--api-key', default=None, help='Override API key')
@click.option('--base-url', default=None, help='Override API base URL')
@click.option('--debug', is_flag=True, help='Show the redacted feedback the model receives + the convergence verdict each eval')
def run(app: str, request: str, model: str, max_iter: int,
        nvidia: bool, api_key: str | None, base_url: str | None, debug: bool):
    """Run a single request (non-interactive).

    REQUEST may be either a free-form NL sentence or a fluent name that
    matches a key in the app's vocabulary patterns (vocabulary.yaml).
    If it matches a pattern key, the stored NL description is used instead.

    \b
    Examples:
      python -m agent.cli run toy happy
      python -m agent.cli run toy "A person is happy as long as they are rich or at the pub"
      python -m agent.cli run maritime gap --nvidia --model nvidia/llama-3.1-nemotron-70b-instruct
    """

    config = _make_config(
        model=model, max_iter=max_iter, nvidia=nvidia,
        api_key=api_key, base_url=base_url, debug=debug,
    )

    # Resolve pattern key → NL description if applicable.
    nl_request, was_lookup = _resolve_request(app, request)
    if was_lookup:
        console.print(
            f"[dim]→ resolved pattern key [bold]{request!r}[/bold] "
            f"from vocabulary.yaml[/dim]"
        )
        console.print(f"[dim]  {nl_request!r}[/dim]\n")

    agent = RTECAgent(
        config=config,
        on_thinking=print_thinking,
        on_tool_call=print_tool_call,
        on_tool_result=print_tool_result,
    )

    console.print(f"[bold]Running:[/bold] {nl_request}")
    console.print()

    task = f"Generate the `{request}` fluent. {nl_request}" if was_lookup else nl_request
    state = agent.run(app, task)

    console.print()
    if state.converged:
        console.print(f"[bold green]✅ Converged! F1 = {state.last_eval.micro_f1:.3f}[/bold green]")
    elif state.last_eval:
        console.print(f"[yellow]📊 Final F1 = {state.last_eval.micro_f1:.3f}[/yellow]")
        console.print(state.last_eval.summary())


def _make_qa_agent(model: str, max_iter: int, verbose: bool) -> QAAgent:
    config = AgentConfig(model=model, max_iterations=max_iter)
    if verbose:
        return QAAgent(
            config=config,
            on_tool_call=print_tool_call,
            on_tool_result=print_tool_result,
        )
    return QAAgent(config=config)


@cli.command()
@click.argument('app')
@click.argument('question')
@click.option('--model', default='gpt-4o', help='LLM model to use')
@click.option('--max-iter', default=10, help='Maximum iterations')
@click.option('--verbose/--quiet', default=True, help='Show tool calls')
def ask(app: str, question: str, model: str, max_iter: int, verbose: bool):
    """Ask a one-off question about an app's event description (read-only QA)."""
    agent = _make_qa_agent(model, max_iter, verbose)
    console.print(f"[bold]Question:[/bold] {question}\n")
    answer = agent.ask(app, question)
    console.print(Panel(Markdown(answer or "_(no answer)_"),
                        title="💬 Answer", border_style="cyan"))


@cli.command(name='qa')
@click.argument('app')
@click.option('--model', default='gpt-4o', help='LLM model to use')
@click.option('--max-iter', default=10, help='Maximum iterations')
@click.option('--verbose/--quiet', default=True, help='Show tool calls')
def qa(app: str, model: str, max_iter: int, verbose: bool):
    """Interactive read-only QA session about an app (no rule generation)."""
    agent = _make_qa_agent(model, max_iter, verbose)
    config = agent.config

    console.print(Panel(
        f"Asking about: [bold]{app}[/bold]\n"
        f"Model: {config.model}\n\n"
        "Ask a question, or 'quit' to exit.",
        title="💬 RTEC QA",
        border_style="cyan"
    ))

    history = [{"role": "system", "content": agent._get_system_prompt(app)}]
    while True:
        try:
            user_input = console.input("\n[bold green]You:[/bold green] ").strip()
            if user_input.lower() in ('quit', 'exit', 'q'):
                console.print("[yellow]Goodbye![/yellow]")
                break
            if not user_input:
                continue
            console.print()
            answer = agent.ask(app, user_input, history=history)
            console.print(Panel(Markdown(answer or "_(no answer)_"),
                                title="💬 Answer", border_style="cyan"))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Type 'quit' to exit.[/yellow]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


@cli.command()
@click.argument('app')
@click.option('--model', default='gpt-4o', help='LLM model to use')
@click.option('--max-iter', default=10, help='Maximum iterations')
@click.option('--plain', is_flag=True, help='Plain terminal UI (no dashboard)')
@click.option('--verbose/--quiet', default=True, help='Show thinking/tool calls (plain mode only)')
@click.option('--nvidia', is_flag=True, help='Use NVIDIA NIM API (reads NVIDIA_API_KEY)')
@click.option('--api-key', default=None, help='Override API key')
@click.option('--base-url', default=None, help='Override API base URL')
def session(app: str, model: str, max_iter: int, plain: bool, verbose: bool,
            nvidia: bool, api_key: str | None, base_url: str | None):
    """Unified session: auto-routes between the builder and QA agents.

    Default UI: split-pane dashboard (chat left, F1 metrics right).
    Use --plain for the legacy scrolling terminal interface.

    Just talk naturally — questions go to the QA agent, rule-building requests
    go to the builder. Force a route with a /build or /ask prefix.
    """
    config = _make_config(
        model=model, max_iter=max_iter, nvidia=nvidia,
        api_key=api_key, base_url=base_url,
    )

    if not plain:
        try:
            from .ui.session_app import run_session_dashboard
            run_session_dashboard(app, config, _resolve_request)
            return
        except ImportError as e:
            console.print(
                f"[yellow]Dashboard requires textual: {e}[/yellow]\n"
                "[dim]Install with: uv pip install -e \"agent/[dev]\"[/dim]\n"
                "Falling back to plain session.\n"
            )

    if verbose:
        sess = RouterSession(
            app, config,
            on_thinking=print_thinking,
            on_tool_call=print_tool_call,
            on_tool_result=print_tool_result,
        )
    else:
        sess = RouterSession(app, config)

    console.print(Panel(
        f"App: [bold]{app}[/bold]   Model: {model}\n\n"
        "Talk naturally — I route each message to the right agent.\n"
        "Force a route with [bold]/build[/bold] or [bold]/ask[/bold]. "
        "'quit' to exit.",
        title="🤖 RTEC Session (builder + QA)",
        border_style="cyan"
    ))

    while True:
        try:
            user_input = console.input("\n[bold green]You:[/bold green] ").strip()
            if user_input.lower() in ('quit', 'exit', 'q'):
                console.print("[yellow]Goodbye![/yellow]")
                break
            if not user_input:
                continue

            console.print()
            result = sess.dispatch(user_input)

            tag = "forced" if result.forced else "auto"
            console.print(f"[dim]→ routed to {result.route} ({tag})[/dim]")

            if result.route == "build":
                state = result.state
                if state and state.converged:
                    console.print(
                        f"[bold green]✅ Converged! F1 = "
                        f"{state.last_eval.micro_f1:.3f}[/bold green]")
                elif state and state.last_eval:
                    console.print(
                        f"[yellow]📊 Current F1 = "
                        f"{state.last_eval.micro_f1:.3f}[/yellow]")
            else:
                console.print(Panel(
                    Markdown(result.answer or "_(no answer)_"),
                    title="💬 Answer", border_style="cyan"))

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Type 'quit' to exit.[/yellow]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


if __name__ == "__main__":
    cli()
