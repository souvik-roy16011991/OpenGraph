#!/usr/bin/env python3
"""
CLI script to build (or rebuild) the knowledge graph from any kb-config folder.

Usage:
    python scripts/build_graph.py
    python scripts/build_graph.py --kb-config /path/to/my-kb --domain "pharmaceutical products"
    python scripts/build_graph.py --no-llm           # skip LLM cross-KB linking
    python scripts/build_graph.py --no-embeddings    # skip embedding generation
    python scripts/build_graph.py --no-llm-profile   # skip LLM domain profiling
    python scripts/build_graph.py --verbose
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

app = typer.Typer(help="Build the KB Knowledge Graph from source JSON files.")
console = Console()


@app.command()
def build(
    kb_config: Optional[Path] = typer.Option(
        None, "--kb-config", help="Path to kb-config folder (default: ./kb-config or $KB_CONFIG_PATH)",
    ),
    domain: Optional[str] = typer.Option(
        None, "--domain", help="Free-text domain name, e.g. 'pharmaceutical products'",
    ),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM cross-KB linking (uses seed mappings only)"),
    no_embeddings: bool = typer.Option(False, "--no-embeddings", help="Skip embedding/FAISS generation"),
    no_llm_profile: bool = typer.Option(False, "--no-llm-profile", help="Skip LLM domain profile polish"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    """Build the knowledge graph from source KB JSON files."""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )

    # Resolve and install the active kb-config BEFORE importing pipeline modules.
    import os
    from src.kb_config import load_kb_config, set_active_kb_config
    kb_path = kb_config or os.environ.get("KB_CONFIG_PATH") or (Path(__file__).parent.parent / "kb-config")
    cfg = load_kb_config(kb_path, domain_hint=domain, use_llm_profile=not no_llm_profile)
    set_active_kb_config(cfg)

    from src.config import FAISS_INDEX_PATH, GRAPH_PICKLE_PATH, NODE_REGISTRY_PATH

    console.rule("[bold blue]KB Knowledge Graph Builder[/bold blue]")
    console.print(f"  kb-config:    [cyan]{cfg.root}[/cyan]")
    console.print(f"  Domain:       [cyan]{cfg.profile.domain_display_name}[/cyan] "
                  f"([dim]{cfg.profile.domain_name}[/dim])")
    console.print(f"  Organization: [cyan]{cfg.profile.organization_name}[/cyan]")
    console.print(f"  Knowledge KB: [cyan]{cfg.knowledge_kb_path}[/cyan]")
    console.print(f"  Tool KB:      [cyan]{cfg.tool_kb_path}[/cyan]")
    console.print(f"  Output:       [cyan]{GRAPH_PICKLE_PATH}[/cyan]")
    console.print()

    start = time.perf_counter()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Building graph…", total=None)

        from src.graph_builder.builder import build_graph
        kg = build_graph(
            use_llm_cross_links=not no_llm,
            skip_embeddings=no_embeddings,
        )
        progress.update(task, description="Done!")

    elapsed = time.perf_counter() - start
    stats = kg.stats()

    console.print()
    console.rule("[bold green]Build Complete[/bold green]")

    # Stats table
    tbl = Table(title="Graph Statistics", show_header=True)
    tbl.add_column("Metric", style="cyan")
    tbl.add_column("Value", style="green", justify="right")
    tbl.add_row("Total Nodes", str(stats["total_nodes"]))
    tbl.add_row("Total Edges", str(stats["total_edges"]))
    tbl.add_section()
    for k, v in sorted(stats["nodes_by_type"].items()):
        tbl.add_row(f"  Nodes [{k}]", str(v))
    tbl.add_section()
    for k, v in sorted(stats["edges_by_type"].items()):
        tbl.add_row(f"  Edges [{k}]", str(v))
    tbl.add_section()
    tbl.add_row("Build time", f"{elapsed:.1f}s")
    console.print(tbl)

    console.print()
    console.print(f"[bold]Graph saved to:[/bold] {GRAPH_PICKLE_PATH}")
    console.print(f"[bold]Node registry:  [/bold] {NODE_REGISTRY_PATH}")
    if not no_embeddings:
        console.print(f"[bold]FAISS index:    [/bold] {FAISS_INDEX_PATH}")
    console.print()
    console.print("[green]Graph ready. Start the API with:[/green]")
    console.print("  [bold cyan]python -m uvicorn src.api.server:app --reload --port 8000[/bold cyan]")


if __name__ == "__main__":
    app()
