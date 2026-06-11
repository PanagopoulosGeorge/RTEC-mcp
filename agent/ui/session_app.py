"""Split-pane session UI: chat (left) + metrics dashboard (right)."""

from __future__ import annotations

import json

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Static

from ..config import AgentConfig
from ..core.observability import FluentStatus, SessionTracker
from ..core.session import RouterSession
from ..tools import get_vocabulary, read_rules


class ChatPane(VerticalScroll):
    """Scrollable chat log."""

    DEFAULT_CSS = """
    ChatPane {
        height: 1fr;
        border: solid $primary;
        padding: 0 1;
    }
    """

    def append(self, kind: str, body: str) -> None:
        styles = {
            "user": "bold green",
            "assistant": "white",
            "thinking": "dim italic",
            "tool_call": "yellow",
            "tool_result": "cyan",
            "system": "dim",
        }
        label = {
            "user": "You",
            "assistant": "Agent",
            "thinking": "Thinking",
            "tool_call": "Tool",
            "tool_result": "Result",
            "system": "System",
        }.get(kind, kind.title())
        preview = body if len(body) <= 1200 else body[:1200] + "\n… (truncated)"
        self.mount(Static(Text(f"[{label}]\n{preview}", style=styles.get(kind, ""))))
        self.scroll_end(animate=False)


class DashboardPane(Static):
    """Right-hand metrics panel."""

    DEFAULT_CSS = """
    DashboardPane {
        width: 62;
        min-width: 50;
        border: solid $accent;
        padding: 0 1;
    }
    """

    def __init__(self, tracker: SessionTracker, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tracker = tracker

    # Width of each F1 cell in the history table: "0.000↑" = 6 chars
    _CELL_W = 7

    @staticmethod
    def _cell(rec) -> str:
        """Format one F1 cell: colored value + trend arrow."""
        color = (
            "green" if rec.f1 >= 0.95
            else ("yellow" if rec.f1 >= 0.5 else "white")
        )
        if rec.delta is None:
            arrow, ac = "·", "dim"
        elif rec.delta > 1e-9:
            arrow, ac = "↑", "green"
        elif rec.delta < -1e-9:
            arrow, ac = "↓", "red"
        else:
            arrow, ac = "→", "dim"
        return f"[{color}]{rec.f1:.3f}[/{color}][{ac}]{arrow}[/{ac}]"

    def refresh_metrics(self) -> None:
        t = self.tracker
        lines: list[str] = []

        # ── header ──────────────────────────────────────────────────────────
        done = t.pass_count
        total = t.total_fluents or len(t.fluent_progress)
        busy = "[yellow]busy[/yellow]" if t.busy else "[dim]idle[/dim]"
        lines.append(
            f"[bold]Session[/bold]  {done}/{total} passed  "
            f"{len(t.runs)} run(s)  {busy}"
        )
        lines.append("")

        # ── fluent × iteration table ─────────────────────────────────────────
        # Collect ordered fluent names that appear in history, keeping catalog order.
        history_fluents = t.fluents_with_history()

        if not history_fluents:
            lines.append("[dim]No evaluations yet.[/dim]")
            lines.append("[dim]Send a /build <fluent> request to start.[/dim]")
        else:
            # Group eval records per fluent → ordered list of FluentEvalRecord
            by_fluent: dict[str, list] = {}
            for rec in t.eval_history:
                by_fluent.setdefault(rec.fluent, []).append(rec)

            # Max eval depth across all fluents → column count
            max_evals = max(len(v) for v in by_fluent.values())

            # Column header: eval index 1 … N
            COL_NAME = 16
            col_hdr = "".join(f"{i+1:>{self._CELL_W}}" for i in range(max_evals))
            lines.append(
                f"[bold]{'fluent':<{COL_NAME}}[/bold][dim]{col_hdr}[/dim]"
            )
            lines.append("[dim]" + "─" * (COL_NAME + self._CELL_W * max_evals) + "[/dim]")

            for fname in history_fluents:
                recs = by_fluent.get(fname, [])
                prog = t.fluent_progress.get(fname)
                status_icon = {
                    FluentStatus.PASS: "[green]✓[/green]",
                    FluentStatus.FAIL: "[red]✗[/red]",
                    FluentStatus.IN_PROGRESS: "[yellow]…[/yellow]",
                    FluentStatus.PENDING: "[dim]·[/dim]",
                }.get(prog.status if prog else FluentStatus.PENDING, " ")

                # Run-boundary separators: mark the start of each new run with │
                cells = []
                prev_run = None
                for rec in recs:
                    sep = "[dim]│[/dim]" if prev_run and rec.run_number != prev_run else " "
                    cells.append(sep + self._cell(rec))
                    prev_run = rec.run_number
                # Pad to max_evals if shorter
                cells += ["[dim]      ·[/dim]"] * (max_evals - len(recs))

                name_col = (fname[:COL_NAME - 2] + "…") if len(fname) > COL_NAME - 1 else fname
                lines.append(
                    f"{status_icon}{name_col:<{COL_NAME - 1}}"
                    + "".join(cells)
                )

            lines.append("")

            # ── monotonic summary (one line per fluent that has > 1 eval) ──
            mono_lines = []
            for fname in history_fluents:
                f1s = [r.f1 for r in by_fluent[fname]]
                if len(f1s) < 2:
                    continue
                mono = all(f1s[i] >= f1s[i - 1] - 1e-9 for i in range(1, len(f1s)))
                tag = "[green]↑ mono[/green]" if mono else "[red]↓ regr[/red]"
                mono_lines.append(f"  {fname}: {tag}")
            if mono_lines:
                lines.append("[bold]Monotonic improvement[/bold]")
                lines.extend(mono_lines)

        self.update("\n".join(lines))


class SessionDashboardApp(App):
    """Interactive RTEC session with live observability dashboard."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        height: 1fr;
    }
    #left {
        width: 1fr;
        layout: vertical;
    }
    #prompt {
        dock: bottom;
        height: 3;
        border: solid $primary-darken-2;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        app_name: str,
        config: AgentConfig,
        resolve_request,
    ) -> None:
        super().__init__()
        self.app_name = app_name
        self.config = config
        self._resolve_request = resolve_request
        catalog: list[str] = []
        try:
            catalog = list(get_vocabulary(app_name).patterns.keys())
        except Exception:
            pass
        self.tracker = SessionTracker(app=app_name, fluent_catalog=catalog)
        self._router: RouterSession | None = None
        self._seed_generated_fluents()

    def _seed_generated_fluents(self) -> None:
        try:
            rules = read_rules(self.app_name, "generated")
            self.tracker.mark_generated_fluents(rules)
            for name in self.tracker.fluent_progress:
                prog = self.tracker.fluent_progress[name]
                if prog.status == FluentStatus.IN_PROGRESS and prog.best_f1 >= 0.95:
                    prog.status = FluentStatus.PASS
        except Exception:
            pass

    def _make_router(self) -> RouterSession:
        return RouterSession(
            self.app_name,
            self.config,
            on_thinking=lambda t: self._on_thinking(t),
            on_tool_call=lambda n, a: self._on_tool_call(n, a),
            on_tool_result=lambda n, r: self._on_tool_result(n, r),
            on_eval=lambda i, r, f: self._on_eval(i, r, f),
            on_iteration=lambda i: self._on_iteration(i),
            on_build_start=lambda req, key: self._on_build_start(req, key),
            resolve_request=self._resolve_request,
        )

    def _on_build_start(self, request: str, fluent_key: str | None) -> None:
        self.tracker.start_build(request, fluent_key=fluent_key)
        self.call_from_thread(self._refresh_dashboard)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield ChatPane(id="chat")
                yield Input(placeholder="Message, /build <fluent>, /ask …, quit", id="prompt")
            yield DashboardPane(self.tracker, id="dashboard")
        yield Footer()

    def on_mount(self) -> None:
        self._router = self._make_router()
        chat = self.query_one("#chat", ChatPane)
        chat.append(
            "system",
            f"RTEC session — app={self.app_name}, model={self.config.model}. "
            "Natural language routes to build or ask. "
            "Use /build gap or /ask how is gap defined?",
        )
        self.query_one("#dashboard", DashboardPane).refresh_metrics()

    def _refresh_dashboard(self) -> None:
        self.query_one("#dashboard", DashboardPane).refresh_metrics()

    def _on_thinking(self, text: str) -> None:
        self.call_from_thread(self._append_chat, "thinking", text)

    def _on_tool_call(self, name: str, args: dict) -> None:
        body = f"{name}\n{json.dumps(args, indent=2)}"
        if name == "compile_rules" and "rules" in args:
            body = f"{name}\n(rules payload: {len(args['rules'])} chars)"
        self.call_from_thread(self._append_chat, "tool_call", body)

    def _on_tool_result(self, name: str, result: str) -> None:
        if len(result) > 800:
            result = result[:800] + "\n…"
        self.call_from_thread(self._append_chat, "tool_result", f"{name}\n{result}")

    def _on_eval(self, iteration: int, report, scoped) -> None:
        self.tracker.record_eval(iteration, report, scoped)
        self.call_from_thread(self._refresh_dashboard)

    def _on_iteration(self, iteration: int) -> None:
        self.call_from_thread(
            self._append_chat, "system", f"— iteration {iteration} —"
        )

    def _append_chat(self, kind: str, text: str) -> None:
        self.tracker.add_chat(kind, text)
        self.query_one("#chat", ChatPane).append(kind, text)

    @on(Input.Submitted, "#prompt")
    def handle_input(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.lower() in ("quit", "exit", "q"):
            self.exit()
            return
        self._append_chat("user", text)
        self.run_turn(text)

    @work(thread=True, exclusive=True)
    def run_turn(self, text: str) -> None:
        assert self._router is not None
        result = self._router.dispatch(text)

        if result.route == "build":
            try:
                rules = read_rules(self.app_name, "generated")
                self.tracker.mark_generated_fluents(rules)
            except Exception:
                pass
            state = result.state
            if state and state.converged and state.last_eval:
                msg = f"Converged — F1={state.last_eval.micro_f1:.3f}"
            elif state and state.last_eval:
                msg = f"Finished — F1={state.last_eval.micro_f1:.3f} (not converged)"
            else:
                msg = "Build finished (no evaluation)"
            self.tracker.finish_build(
                bool(state and state.converged),
                fluent_key=result.fluent_key,
            )
            self.call_from_thread(self._append_chat, "assistant", msg)
        else:
            tag = "forced" if result.forced else "auto"
            self.call_from_thread(
                self._append_chat,
                "assistant",
                f"(ask/{tag})\n{result.answer or '_(no answer)_'}",
            )
        self.call_from_thread(self._refresh_dashboard)


def run_session_dashboard(app_name: str, config: AgentConfig, resolve_request) -> None:
    """Launch the Textual session UI."""
    SessionDashboardApp(app_name, config, resolve_request).run()
