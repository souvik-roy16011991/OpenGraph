#!/usr/bin/env python3
"""
Interactive CLI for querying the Knowledge Graph Engine.

Usage:
    python scripts/query_cli.py                          # interactive REPL
    python scripts/query_cli.py --query "How do I onboard a new client?"
    python scripts/query_cli.py --query "..." --json     # output raw JSON
    python scripts/query_cli.py stats                    # show graph stats
    python scripts/query_cli.py search "compliance"      # keyword/semantic search
    python scripts/query_cli.py --kb-config /path/to/kb  # query a different kb-config
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import typer
from rich import print as rprint
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(help="Interactive CLI for the KB Knowledge Graph Engine.")
console = Console()
logging.basicConfig(level=logging.WARNING)


def _install_kb_config(kb_config: Optional[Path], domain: Optional[str], no_llm_profile: bool = True) -> None:
    """Resolve + install the active kb-config before any pipeline import."""
    from src.kb_config import load_kb_config, set_active_kb_config
    kb_path = kb_config or os.environ.get("KB_CONFIG_PATH") or (Path(__file__).parent.parent / "kb-config")
    cfg = load_kb_config(kb_path, domain_hint=domain, use_llm_profile=not no_llm_profile)
    set_active_kb_config(cfg)


def _load_agent():
    """Load the KG and build the agent."""
    from src.agent.graph import KBGraphAgent
    from src.graph_builder.builder import KnowledgeGraph

    console.print("[dim]Loading knowledge graph…[/dim]", end="")
    kg = KnowledgeGraph.load()
    console.print(f" [green]✓[/green] ({kg.stats()['total_nodes']} nodes)")
    return KBGraphAgent.from_graph(kg), kg


def _display_result(state: dict, raw_json: bool = False) -> None:
    if raw_json:
        # Remove non-serialisable fields
        safe = {k: v for k, v in state.items() if k not in ("gathered_context",)}
        console.print_json(json.dumps(safe, default=str, indent=2))
        return

    console.print()
    console.print(Panel(
        f"[bold cyan]Intent:[/bold cyan] {state.get('intent', 'N/A')}  "
        f"[bold cyan]Focus:[/bold cyan] {state.get('kb_focus', 'N/A')}  "
        f"[bold cyan]Topics:[/bold cyan] {', '.join(state.get('extracted_topics', []))}",
        title="[bold]Query Classification[/bold]",
        expand=False,
    ))

    # Steps
    steps = state.get("steps", [])
    if steps:
        tbl = Table(title="Steps", show_header=True, expand=False)
        tbl.add_column("#", style="bold cyan", width=4)
        tbl.add_column("Title", style="bold")
        tbl.add_column("System / Tool", style="green")
        for s in steps[:15]:
            tool_name = ""
            if s.get("tools"):
                tool_name = s["tools"][0].get("tool_name", "") if s["tools"] else ""
            elif s.get("system_used"):
                tool_name = s["system_used"]
            tbl.add_row(str(s.get("step_number", "?")), s.get("title", ""), tool_name)
        console.print(tbl)

    # Tools
    tools = state.get("tools_referenced", [])
    if tools:
        tbl2 = Table(title=f"Tools Referenced ({len(tools)})", show_header=True, expand=False)
        tbl2.add_column("Tool", style="bold yellow")
        tbl2.add_column("Provider", style="cyan")
        tbl2.add_column("Purpose", style="dim", max_width=50)
        for t in tools[:12]:
            tbl2.add_row(
                t.get("tool_name", t.get("heading", "")),
                t.get("provider", ""),
                t.get("purpose", "")[:80],
            )
        console.print(tbl2)

    # Main response
    console.print()
    console.print(Panel(Markdown(state.get("response", "_No response generated._")),
                         title="[bold green]Response[/bold green]"))

    # Follow-ups
    follow_ups = state.get("follow_up_suggestions", [])
    if follow_ups:
        console.print()
        console.print("[bold]Follow-up suggestions:[/bold]")
        for i, q in enumerate(follow_ups, 1):
            console.print(f"  {i}. [cyan]{q}[/cyan]")
    console.print()


@app.command()
def query(
    query_text: Optional[str] = typer.Option(None, "--query", "-q", help="Query string"),
    raw_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive REPL mode"),
    kb_config: Optional[Path] = typer.Option(None, "--kb-config", help="Path to kb-config folder"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Free-text domain name"),
    no_llm_profile: bool = typer.Option(True, "--no-llm-profile/--llm-profile", help="Skip LLM domain profile polish (default: skip)"),
):
    """Query the knowledge graph with a natural language question."""
    _install_kb_config(kb_config, domain, no_llm_profile)
    agent, kg = _load_agent()

    if interactive or not query_text:
        # REPL mode
        console.print()
        console.print(Panel(
            "[bold green]KB Knowledge Graph Interactive Query[/bold green]\n"
            "Type your question and press Enter. Type [bold cyan]exit[/bold cyan] to quit.\n"
            "Type [bold cyan]stats[/bold cyan] to see graph statistics.",
            title="Welcome",
        ))
        while True:
            try:
                q = console.input("\n[bold cyan]Query>[/bold cyan] ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[yellow]Goodbye![/yellow]")
                break
            if not q:
                continue
            if q.lower() in ("exit", "quit", "q"):
                console.print("[yellow]Goodbye![/yellow]")
                break
            if q.lower() == "stats":
                _show_stats(kg)
                continue
            if q.lower().startswith("search "):
                term = q[7:].strip()
                _run_search(kg, term)
                continue

            with console.status("[bold green]Thinking…[/bold green]"):
                state = agent.query(q)
            _display_result(state, raw_json)
    else:
        with console.status("[bold green]Thinking…[/bold green]"):
            state = agent.query(query_text)
        _display_result(state, raw_json)


@app.command()
def stats(
    kb_config: Optional[Path] = typer.Option(None, "--kb-config", help="Path to kb-config folder"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Free-text domain name"),
):
    """Show knowledge graph statistics."""
    _install_kb_config(kb_config, domain)
    from src.graph_builder.builder import KnowledgeGraph
    kg = KnowledgeGraph.load()
    _show_stats(kg)


def _show_stats(kg) -> None:
    s = kg.stats()
    tbl = Table(title="Knowledge Graph Statistics", show_header=True)
    tbl.add_column("Metric", style="cyan")
    tbl.add_column("Value", style="green", justify="right")
    tbl.add_row("Total Nodes", str(s["total_nodes"]))
    tbl.add_row("Total Edges", str(s["total_edges"]))
    tbl.add_section()
    for k, v in sorted(s["nodes_by_type"].items()):
        tbl.add_row(f"  Nodes [{k}]", str(v))
    tbl.add_section()
    for k, v in sorted(s["edges_by_type"].items()):
        tbl.add_row(f"  Edges [{k}]", str(v))
    console.print(tbl)


@app.command()
def search(
    term: str = typer.Argument(..., help="Search term"),
    top_k: int = typer.Option(10, help="Number of results"),
    kb_config: Optional[Path] = typer.Option(None, "--kb-config", help="Path to kb-config folder"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Free-text domain name"),
):
    """Search for nodes by keyword/semantic similarity."""
    _install_kb_config(kb_config, domain)
    from src.graph_builder.builder import KnowledgeGraph
    kg = KnowledgeGraph.load()
    _run_search(kg, term, top_k)


def _run_search(kg, term: str, top_k: int = 10) -> None:
    results = kg.hybrid_search(term, top_k=top_k)
    tbl = Table(title=f"Search: '{term}'", show_header=True)
    tbl.add_column("Score", style="cyan", justify="right", width=7)
    tbl.add_column("Type", style="yellow", width=10)
    tbl.add_column("KB", style="dim", width=10)
    tbl.add_column("Heading", style="bold")
    for nid, score in results:
        node = kg.nodes.get(nid)
        if node:
            tbl.add_row(
                f"{score:.3f}",
                node.node_type.value,
                node.kb_source.value,
                node.heading[:80],
            )
    console.print(tbl)


@app.command()
def node(
    node_id: str = typer.Argument(..., help="Node ID to inspect"),
    kb_config: Optional[Path] = typer.Option(None, "--kb-config", help="Path to kb-config folder"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Free-text domain name"),
):
    """Inspect a specific node and its connections."""
    _install_kb_config(kb_config, domain)
    from src.graph_builder.builder import KnowledgeGraph
    kg = KnowledgeGraph.load()
    n = kg.get_node(node_id)
    if not n:
        console.print(f"[red]Node '{node_id}' not found.[/red]")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]{n.heading}[/bold]\n"
        f"[dim]ID:[/dim] {n.node_id}\n"
        f"[dim]Type:[/dim] {n.node_type.value} | [dim]KB:[/dim] {n.kb_source.value}\n"
        f"[dim]Summary:[/dim] {n.content_summary[:300]}",
        title=f"Node: {node_id}",
    ))

    edges = kg.get_edges(node_id)
    if edges:
        tbl = Table(title="Edges", show_header=True)
        tbl.add_column("Direction", width=8)
        tbl.add_column("Type", style="cyan")
        tbl.add_column("Connected Node", style="bold")
        for e in edges[:20]:
            direction = "→" if "target" in e else "←"
            other = e.get("target") or e.get("source", "?")
            other_node = kg.get_node(other)
            other_label = other_node.heading[:60] if other_node else other
            tbl.add_row(direction, e.get("edge_type", "?"), other_label)
        console.print(tbl)


if __name__ == "__main__":
    app()
