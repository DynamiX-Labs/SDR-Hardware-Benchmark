"""
Advanced Report Generator for DSP Benchmark Suite
Generates rich terminal output, Markdown, JSON, and CSV reports.

Inspired by SatDump's benchmark render system.
DynamiX Labs
"""

import json
import logging
from typing import List, Dict, Optional, TYPE_CHECKING
from dataclasses import dataclass
from pathlib import Path

if TYPE_CHECKING:
    from .dsp_benchmark import DSPBenchmarkReport, BenchmarkResult
    from .system_profiler import SystemProfile

log = logging.getLogger("benchmark.report")


# ── Performance Tier Classification ──────────────────────────────────────

TIER_THRESHOLDS = {
    "FFT": {"excellent": 0.5, "good": 2.0, "fair": 10.0},
    "FIR": {"excellent": 0.3, "good": 1.5, "fair": 8.0},
    "Costas": {"excellent": 1.0, "good": 5.0, "fair": 20.0},
    "Gardner": {"excellent": 0.5, "good": 3.0, "fair": 15.0},
    "Decimate": {"excellent": 0.2, "good": 1.0, "fair": 5.0},
    "AGC": {"excellent": 5.0, "good": 20.0, "fair": 100.0},
    "DEFAULT": {"excellent": 1.0, "good": 5.0, "fair": 25.0},
}


def classify_tier(result_name: str, mean_ms: float) -> str:
    """Classify a benchmark result into performance tiers."""
    thresholds = TIER_THRESHOLDS.get("DEFAULT")
    for key, thresh in TIER_THRESHOLDS.items():
        if key != "DEFAULT" and key.lower() in result_name.lower():
            thresholds = thresh
            break

    if mean_ms <= thresholds["excellent"]:
        return "★ Excellent"
    elif mean_ms <= thresholds["good"]:
        return "● Good"
    elif mean_ms <= thresholds["fair"]:
        return "◐ Fair"
    else:
        return "○ Slow"


def overall_readiness(results: list) -> str:
    """Assess if the system is ready for real-time satellite DSP."""
    tiers = [classify_tier(r.name, r.mean_ms) for r in results]
    excellent = sum(1 for t in tiers if "Excellent" in t)
    good = sum(1 for t in tiers if "Good" in t)
    total = len(tiers)

    if total == 0:
        return "No data"
    ratio = (excellent + good) / total
    if ratio >= 0.8:
        return "SATELLITE-READY ✓"
    elif ratio >= 0.5:
        return "ADEQUATE"
    else:
        return "NEEDS OPTIMIZATION"


# ── Report Generators ────────────────────────────────────────────────────

class ReportGenerator:
    """Generates benchmark reports in multiple formats."""

    @staticmethod
    def terminal_report(report: "DSPBenchmarkReport",
                        profile: Optional["SystemProfile"] = None) -> str:
        """Generate rich terminal output with performance tiers."""
        lines = []

        # Header
        lines.append("")
        lines.append("-" * 80)
        lines.append("                    DynamiX Labs - DSP Benchmark Report")
        lines.append("-" * 80)
        lines.append(f"  Platform:  {report.platform[:62]:<62}  ")
        lines.append(f"  NumPy:     {report.numpy_version:<62}  ")
        lines.append(f"  Python:    {report.python_version:<62}  ")
        lines.append(f"  Timestamp: {report.timestamp[:62]:<62}  ")
        lines.append("-" * 80)

        # Results table header
        lines.append(
            f"  {'Test':<28} {'Mean':>8} {'P50':>8} {'P95':>8} "
            f"{'MSPS':>10} {'Tier':<14} "
        )
        lines.append("  " + "-" * 76)

        # Group results by category
        current_category = ""
        for r in report.results:
            # Detect category from name
            cat = r.name.split()[0] if r.name else ""
            if cat != current_category:
                current_category = cat
                if lines[-1] != "  " + "-" * 76:
                    lines.append("  " + "-" * 76)

            tier = classify_tier(r.name, r.mean_ms)

            # Calculate MSPS (mega samples per second)
            msps = r.throughput * r.notes_data.get("n_samples", 65536) / 1e6 if hasattr(r, "notes_data") and isinstance(getattr(r, "notes_data", None), dict) else 0.0

            p50 = r.p50_ms if hasattr(r, "p50_ms") else r.mean_ms
            p95 = r.p95_ms if hasattr(r, "p95_ms") else r.max_ms

            lines.append(
                f"  {r.name:<28} {r.mean_ms:>7.2f}ms {p50:>7.2f}ms {p95:>7.2f}ms "
                f"{r.throughput:>9.0f}/s {tier:<14} "
            )

        lines.append("-" * 80)
        readiness = overall_readiness(report.results)
        lines.append(f"  System Assessment: {readiness:<56} ")
        lines.append("-" * 80)
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def to_json(report: "DSPBenchmarkReport",
                profile: Optional["SystemProfile"] = None,
                output_path: str = "benchmark_report.json") -> str:
        """Export report to JSON with full metadata."""
        data = {
            "meta": {
                "tool": "DynamiX Labs SDR Hardware Benchmark",
                "version": "2.0.0",
                "platform": report.platform,
                "python_version": report.python_version,
                "numpy_version": report.numpy_version,
                "timestamp": report.timestamp,
            },
            "results": [],
        }

        if profile:
            data["system_profile"] = profile.to_dict()

        for r in report.results:
            entry = {
                "name": r.name,
                "mean_ms": round(r.mean_ms, 4),
                "min_ms": round(r.min_ms, 4),
                "max_ms": round(r.max_ms, 4),
                "std_ms": round(r.std_ms, 4),
                "throughput_ops_sec": round(r.throughput, 2),
                "tier": classify_tier(r.name, r.mean_ms),
                "iterations": r.iterations,
            }
            if hasattr(r, "p50_ms"):
                entry["p50_ms"] = round(r.p50_ms, 4)
            if hasattr(r, "p95_ms"):
                entry["p95_ms"] = round(r.p95_ms, 4)
            if hasattr(r, "p99_ms"):
                entry["p99_ms"] = round(r.p99_ms, 4)
            if r.notes:
                entry["notes"] = r.notes
            data["results"].append(entry)

        data["assessment"] = overall_readiness(report.results)

        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        log.info(f"JSON report saved: {output_path}")
        return output_path

    @staticmethod
    def to_csv(report: "DSPBenchmarkReport",
               output_path: str = "benchmark_report.csv") -> str:
        """Export report to CSV."""
        try:
            import pandas as pd
            rows = []
            for r in report.results:
                row = {
                    "name": r.name,
                    "mean_ms": r.mean_ms,
                    "min_ms": r.min_ms,
                    "max_ms": r.max_ms,
                    "std_ms": r.std_ms,
                    "throughput": r.throughput,
                    "tier": classify_tier(r.name, r.mean_ms),
                    "iterations": r.iterations,
                }
                if hasattr(r, "p50_ms"):
                    row["p50_ms"] = r.p50_ms
                if hasattr(r, "p95_ms"):
                    row["p95_ms"] = r.p95_ms
                rows.append(row)
            pd.DataFrame(rows).to_csv(output_path, index=False)
        except ImportError:
            # Fallback without pandas
            import csv
            fieldnames = ["name", "mean_ms", "min_ms", "max_ms", "std_ms",
                          "throughput", "tier", "iterations"]
            with open(output_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in report.results:
                    writer.writerow({
                        "name": r.name,
                        "mean_ms": round(r.mean_ms, 4),
                        "min_ms": round(r.min_ms, 4),
                        "max_ms": round(r.max_ms, 4),
                        "std_ms": round(r.std_ms, 4),
                        "throughput": round(r.throughput, 2),
                        "tier": classify_tier(r.name, r.mean_ms),
                        "iterations": r.iterations,
                    })
        log.info(f"CSV report saved: {output_path}")
        return output_path

    @staticmethod
    def to_markdown(report: "DSPBenchmarkReport",
                    profile: Optional["SystemProfile"] = None,
                    output_path: str = "benchmark_report.md") -> str:
        """Export report to Markdown with tables and assessment."""
        lines = [
            "# DynamiX Labs — DSP Benchmark Report",
            "",
            f"**Platform:** {report.platform}  ",
            f"**Python:** {report.python_version} | **NumPy:** {report.numpy_version}  ",
            f"**Timestamp:** {report.timestamp}  ",
            "",
        ]

        if profile:
            lines.extend([
                "## System Profile",
                "",
                f"| Property | Value |",
                f"|:---|:---|",
                f"| CPU | {profile.cpu.model} |",
                f"| Cores | {profile.cpu.cores_physical}P / {profile.cpu.cores_logical}L |",
                f"| RAM | {profile.memory.total_gb:.1f} GB |",
                f"| GPU | {profile.gpu.name if profile.gpu.available else 'None'} |",
                f"| BLAS | {profile.numeric.blas_library} |",
                "",
            ])

        # Results table
        lines.extend([
            "## Benchmark Results",
            "",
            "| Test | Mean (ms) | Min (ms) | Max (ms) | Throughput | Tier |",
            "|:---|---:|---:|---:|---:|:---|",
        ])

        for r in report.results:
            tier = classify_tier(r.name, r.mean_ms)
            lines.append(
                f"| {r.name} | {r.mean_ms:.2f} | {r.min_ms:.2f} | "
                f"{r.max_ms:.2f} | {r.throughput:.0f}/s | {tier} |"
            )

        lines.extend([
            "",
            "## Assessment",
            "",
            f"**System Readiness:** {overall_readiness(report.results)}",
            "",
            "---",
            "*Generated by DynamiX Labs SDR Hardware Benchmark v2.0*",
        ])

        with open(output_path, "w") as f:
            f.write("\n".join(lines))
        log.info(f"Markdown report saved: {output_path}")
        return output_path

    @staticmethod
    def compare(report: "DSPBenchmarkReport",
                baseline_path: str) -> str:
        """Compare current results against a baseline JSON report."""
        try:
            with open(baseline_path, "r") as f:
                baseline = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return f"Cannot load baseline: {e}"

        baseline_results = {r["name"]: r for r in baseline.get("results", [])}

        lines = [
            "",
            "═══ Benchmark Comparison ═══",
            f"{'Test':<28} {'Current':>10} {'Baseline':>10} {'Delta':>10} {'Status':<10}",
            "─" * 70,
        ]

        for r in report.results:
            if r.name in baseline_results:
                bl = baseline_results[r.name]
                bl_mean = bl.get("mean_ms", 0)
                if bl_mean > 0:
                    delta_pct = ((r.mean_ms - bl_mean) / bl_mean) * 100
                    status = "✓ FASTER" if delta_pct < -5 else ("✗ SLOWER" if delta_pct > 5 else "≈ SAME")
                    lines.append(
                        f"{r.name:<28} {r.mean_ms:>9.2f}ms {bl_mean:>9.2f}ms "
                        f"{delta_pct:>+9.1f}% {status:<10}"
                    )
                else:
                    lines.append(f"{r.name:<28} {r.mean_ms:>9.2f}ms {'N/A':>10} {'N/A':>10}")
            else:
                lines.append(f"{r.name:<28} {r.mean_ms:>9.2f}ms {'NEW':>10} {'---':>10}")

        lines.append("─" * 70)
        return "\n".join(lines)
