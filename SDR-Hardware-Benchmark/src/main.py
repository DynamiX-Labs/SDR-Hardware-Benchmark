"""
SDR Hardware Benchmark — CLI Entry Point

Enhanced CLI with category selection, system profiling, multiple output
formats, and baseline comparison.

DynamiX Labs
"""

import argparse
import sys
import logging
from logging.handlers import RotatingFileHandler
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

console = Console()

# Configure production-grade logging
file_handler = RotatingFileHandler(
    "sdr_benchmark.log", maxBytes=5 * 1024 * 1024, backupCount=3
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
))

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[
        RichHandler(rich_tracebacks=True, console=console),
        file_handler
    ]
)
log = logging.getLogger("benchmark.main")

from src.benchmarks.dsp_benchmark import DSPBenchmark, get_categories


def main():
    parser = argparse.ArgumentParser(
        description="DynamiX Labs — SDR Hardware Benchmark Suite v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                              # Run all benchmarks
  %(prog)s --categories fft fir agc     # Run only FFT, FIR, AGC categories
  %(prog)s --profile --format markdown  # Full profile + Markdown report
  %(prog)s --compare baseline.json      # Compare against baseline
  %(prog)s --list-categories            # Show available categories
        """,
    )

    parser.add_argument(
        "--samples", type=int, default=65536,
        help="Number of samples to generate (default: 65536)",
    )
    parser.add_argument(
        "--iterations", type=int, default=500,
        help="Number of iterations per test (default: 500)",
    )
    parser.add_argument(
        "--categories", nargs="*", default=None,
        help="Benchmark categories to run (default: all). "
             "Use --list-categories to see options.",
    )
    parser.add_argument(
        "--list-categories", action="store_true",
        help="List all available benchmark categories and exit.",
    )
    parser.add_argument(
        "--profile", action="store_true",
        help="Run system hardware profiling before benchmarks.",
    )
    parser.add_argument(
        "--format", choices=["json", "csv", "markdown", "all"],
        default="all",
        help="Output format (default: all).",
    )
    parser.add_argument(
        "--compare", type=str, default=None,
        help="Path to baseline JSON report for comparison.",
    )
    parser.add_argument(
        "--output-dir", type=str, default=".",
        help="Output directory for report files.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose/debug logging.",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        log.debug("Verbose logging enabled.")

    # List categories and exit
    if args.list_categories:
        table = Table(title="Available Benchmark Categories")
        table.add_column("Category", style="cyan", no_wrap=True)
        for cat in get_categories():
            table.add_row(cat)
        console.print(table)
        return

    # System profiling
    profile = None
    if args.profile:
        from src.benchmarks.system_profiler import SystemProfiler
        with console.status("[bold green]Profiling system hardware...[/bold green]"):
            profile = SystemProfiler.profile()
        console.print(Panel(profile.summary(), title="System Profile", style="blue"))

    # Validate categories
    if args.categories:
        valid = set(get_categories())
        for cat in args.categories:
            if cat not in valid:
                log.error(f"Unknown category '{cat}'. Use --list-categories to see options.")
                sys.exit(1)

    # Run benchmarks
    console.print(f"\n[bold yellow]⚡ Initializing DSP Benchmark[/bold yellow] "
                  f"(Samples: {args.samples}, Iterations: {args.iterations})")

    benchmark = DSPBenchmark(n_samples=args.samples, iterations=args.iterations)

    cat_label = ", ".join(args.categories) if args.categories else "ALL"
    
    with console.status(f"[bold cyan]📊 Running benchmarks \\[{cat_label}]...[/bold cyan]"):
        report = benchmark.run_all(categories=args.categories)

    # Terminal output
    from src.benchmarks.report_generator import ReportGenerator

    console.print("\n" + ReportGenerator.terminal_report(report, profile))

    # Export reports
    from pathlib import Path
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.format in ("json", "all"):
        ReportGenerator.to_json(
            report, profile,
            output_path=str(out_dir / "benchmark_report.json"),
        )

    if args.format in ("csv", "all"):
        ReportGenerator.to_csv(
            report,
            output_path=str(out_dir / "benchmark_report.csv"),
        )

    if args.format in ("markdown", "all"):
        ReportGenerator.to_markdown(
            report, profile,
            output_path=str(out_dir / "benchmark_report.md"),
        )

    # Comparison
    if args.compare:
        console.print(ReportGenerator.compare(report, args.compare))

    console.print("\n[bold green] Benchmark complete.[/bold green]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Benchmark cancelled by user.[/yellow]")
        sys.exit(130)
    except Exception as e:
        log.error(f"Fatal error: {str(e)}", exc_info=True)
        console.print_exception(show_locals=True)
        console.print(f"\n[bold red]Fatal error during benchmark: {str(e)}[/bold red]")
        sys.exit(1)
