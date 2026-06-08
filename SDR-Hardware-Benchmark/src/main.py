import argparse
import sys
from src.benchmarks.dsp_benchmark import DSPBenchmark

def main():
    parser = argparse.ArgumentParser(description="SDR Hardware Benchmark Suite")
    parser.add_argument("--samples", type=int, default=65536, help="Number of samples to generate")
    parser.add_argument("--iterations", type=int, default=500, help="Number of iterations for each test")
    
    args = parser.parse_args()
    
    print(f"Initializing DSP Benchmark (Samples: {args.samples}, Iterations: {args.iterations})")
    benchmark = DSPBenchmark(n_samples=args.samples, iterations=args.iterations)
    
    print("Running benchmarks...")
    report = benchmark.run_all()
    
    print("\n" + report.summary())

    import json
    import pandas as pd
    rows = [{"name": r.name, "mean_ms": r.mean_ms,
             "throughput": r.throughput} for r in report.results]
    pd.DataFrame(rows).to_csv("benchmark_report.csv", index=False)
    with open("benchmark_report.json", "w") as f:
        json.dump(rows, f, indent=2)
    print("Saved benchmark_report.csv and benchmark_report.json")

if __name__ == "__main__":
    main()
