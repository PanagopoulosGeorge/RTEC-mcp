"""RTEC ReAct Agent CLI."""

import click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
import json

from .config import AgentConfig, APPS_DIR
from .core.agent import RTECAgent
from .tools import generate_gold, get_vocabulary, list_apps, get_syntax_docs


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
        result,
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
            f"[bold]Simple Fluents:[/bold] {', '.join(v.simple_fluents)}\n"
            f"[bold]SD Fluents:[/bold] {', '.join(v.sd_fluents)}",
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
def chat(app: str, model: str, max_iter: int, verbose: bool):
    """Start interactive chat session with the agent."""
    
    config = AgentConfig(
        model=model,
        max_iterations=max_iter,
        show_thinking=verbose,
        show_tool_calls=verbose,
        show_tool_results=verbose,
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
@click.argument('request')
@click.option('--model', default='gpt-4o', help='LLM model to use')
@click.option('--max-iter', default=10, help='Maximum iterations')
def run(app: str, request: str, model: str, max_iter: int):
    """Run a single request (non-interactive)."""
    
    config = AgentConfig(
        model=model,
        max_iterations=max_iter,
    )
    
    agent = RTECAgent(
        config=config,
        on_thinking=print_thinking,
        on_tool_call=print_tool_call,
        on_tool_result=print_tool_result,
    )
    
    console.print(f"[bold]Running:[/bold] {request}")
    console.print()
    
    state = agent.run(app, request)
    
    console.print()
    if state.converged:
        console.print(f"[bold green]✅ Converged! F1 = {state.last_eval.micro_f1:.3f}[/bold green]")
    elif state.last_eval:
        console.print(f"[yellow]📊 Final F1 = {state.last_eval.micro_f1:.3f}[/yellow]")
        console.print(state.last_eval.summary())


if __name__ == "__main__":
    cli()
