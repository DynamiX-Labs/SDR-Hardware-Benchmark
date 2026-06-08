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

if __name__ == "__main__":
    main()
