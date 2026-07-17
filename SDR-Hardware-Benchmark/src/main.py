"""
SDR Hardware Benchmark — CLI Entry Point

Enhanced CLI with category selection, system profiling, multiple output
formats, and baseline comparison.

DynamiX Labs
"""

import argparse
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)

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

    # List categories and exit
    if args.list_categories:
        print("\nAvailable Benchmark Categories:")
        print("─" * 40)
        for cat in get_categories():
            print(f"  • {cat}")
        print()
        return

    # System profiling
    profile = None
    if args.profile:
        from src.benchmarks.system_profiler import SystemProfiler
        print("\n🔍 Profiling system hardware...")
        profile = SystemProfiler.profile()
        print(profile.summary())
        print()

    # Validate categories
    if args.categories:
        valid = set(get_categories())
        for cat in args.categories:
            if cat not in valid:
                print(f"Error: Unknown category '{cat}'. "
                      f"Use --list-categories to see options.")
                sys.exit(1)

    # Run benchmarks
    print(f"\n⚡ Initializing DSP Benchmark "
          f"(Samples: {args.samples}, Iterations: {args.iterations})")

    benchmark = DSPBenchmark(n_samples=args.samples, iterations=args.iterations)

    cat_label = ", ".join(args.categories) if args.categories else "ALL"
    print(f"📊 Running benchmarks [{cat_label}]...\n")

    report = benchmark.run_all(categories=args.categories)

    # Terminal output
    from src.benchmarks.report_generator import ReportGenerator

    print(ReportGenerator.terminal_report(report, profile))

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
        print(ReportGenerator.compare(report, args.compare))

    print("\n✅ Benchmark complete.")


if __name__ == "__main__":
    main()
