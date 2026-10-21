#!/usr/bin/env python3
"""
Cross-Platform SDR Hardware Validation Tool
DynamiX Labs | Advanced Hardware Integration

Validates SDR hardware setup and produces diagnostic reports.
Works on both Windows and Linux without requiring root/admin.

Features:
  - Device discovery via SoapySDR
  - Self-test sequence (tune, read, verify IQ stream)
  - Quick 5-second throughput benchmark per device
  - Hardware report generation (JSON)
  - Recommended settings based on device capabilities
  - USB bus diagnostics
  - Kernel module conflict detection (Linux)

Usage:
    python validate_hardware.py                # Auto-detect and validate
    python validate_hardware.py --simulate     # Run without hardware
    python validate_hardware.py --report out   # Save JSON report
    python validate_hardware.py --verbose      # Detailed output
"""

import argparse
import json
import logging
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("validate_hardware")


# ─── Constants ───────────────────────────────────────────────────────────

# Known kernel module conflicts (Linux)
KERNEL_CONFLICTS = {
    "rtlsdr": {
        "modules": ["dvb_usb_rtl28xxu", "dvb_usb_rtl2832u", "rtl2832"],
        "fix": "sudo rmmod dvb_usb_rtl28xxu dvb_usb_rtl2832u rtl2832 2>/dev/null",
        "permanent": "echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtlsdr-blacklist.conf",
    },
    "hackrf": {
        "modules": [],
        "fix": "",
        "permanent": "",
    },
}

# Recommended settings per device type
RECOMMENDED_SETTINGS = {
    "rtlsdr": {
        "sample_rates": [250e3, 1e6, 2.048e6, 2.4e6],
        "optimal_rate": 2.048e6,
        "optimal_gain": 40.0,
        "buffer_size": 131072,
        "notes": "Use 2.048 MSPS for NOAA APT. Avoid rates above 2.8 MSPS (USB 2.0 limit).",
    },
    "hackrf": {
        "sample_rates": [2e6, 4e6, 8e6, 10e6, 20e6],
        "optimal_rate": 8e6,
        "optimal_gain": 40.0,
        "buffer_size": 262144,
        "notes": "8 MSPS is stable for most applications. 20 MSPS possible but may drop on USB 2.0.",
    },
    "plutosdr": {
        "sample_rates": [1e6, 2.5e6, 5e6, 10e6],
        "optimal_rate": 5e6,
        "optimal_gain": 50.0,
        "buffer_size": 262144,
        "notes": "Good for L-band and Inmarsat. Use USB OTG cable for best performance.",
    },
    "uhd": {
        "sample_rates": [1e6, 5e6, 10e6, 25e6, 56e6],
        "optimal_rate": 25e6,
        "optimal_gain": 50.0,
        "buffer_size": 524288,
        "notes": "Requires USB 3.0. Run uhd_images_downloader for FPGA firmware.",
    },
    "lime": {
        "sample_rates": [1e6, 2e6, 5e6, 10e6],
        "optimal_rate": 5e6,
        "optimal_gain": 50.0,
        "buffer_size": 262144,
        "notes": "Requires USB 3.0 for full sample rate. Use LimeSuiteGUI for calibration.",
    },
}


# ─── Validation Functions ────────────────────────────────────────────────


def discover_devices(simulate: bool = False) -> List[Dict]:
    """Discover all SDR devices using SoapySDR."""
    if simulate:
        return [
            {
                "driver": "rtlsdr",
                "label": "RTL-SDR v3 (Simulated)",
                "serial": "SIM00001",
                "simulated": True,
            },
            {
                "driver": "hackrf",
                "label": "HackRF One (Simulated)",
                "serial": "SIM00002",
                "simulated": True,
            },
        ]

    devices = []
    try:
        import SoapySDR

        results = SoapySDR.Device.enumerate()
        for r in results:
            devices.append(dict(r))
        log.info(f"SoapySDR found {len(devices)} device(s)")
    except ImportError:
        log.error("SoapySDR is NOT installed. Install it to detect hardware.")
        log.error("  Linux: sudo apt-get install soapysdr-tools python3-soapysdr")
        log.error(
            "  Windows: Install PothosSDR (https://github.com/pothosware/PothosSDR)"
        )
    except Exception as e:
        log.error(f"Device enumeration failed: {e}")

    return devices


def self_test_device(device_info: Dict, simulate: bool = False) -> Dict:
    """
    Run a self-test on a single SDR device.

    Steps:
    1. Open device
    2. Configure to a safe frequency
    3. Read a block of samples
    4. Verify samples are non-zero and valid
    5. Close device
    """
    result = {
        "driver": device_info.get("driver", "unknown"),
        "serial": device_info.get("serial", ""),
        "label": device_info.get("label", ""),
        "tests": {},
        "passed": True,
    }

    if simulate or device_info.get("simulated"):
        log.info(f"  Self-test [{result['driver']}]: SIMULATED")
        result["tests"]["connect"] = {"status": "pass", "note": "simulated"}
        result["tests"]["configure"] = {"status": "pass", "note": "simulated"}
        result["tests"]["read_samples"] = {
            "status": "pass",
            "note": "simulated",
            "samples_read": 65536,
        }
        result["tests"]["iq_valid"] = {"status": "pass", "note": "simulated"}
        return result

    # Test 1: Connect
    try:
        import numpy as np
        import SoapySDR
        from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX

        sdr = SoapySDR.Device(device_info)
        result["tests"]["connect"] = {"status": "pass"}
        log.info(f"  ✓ Connect: OK")
    except Exception as e:
        result["tests"]["connect"] = {"status": "fail", "error": str(e)}
        result["passed"] = False
        log.error(f"  ✗ Connect: FAILED ({e})")
        return result

    # Test 2: Configure
    try:
        test_freq = 100e6  # 100 MHz (safe for all devices)
        test_rate = 250e3  # Low rate for reliable test
        test_gain = 20.0

        sdr.setFrequency(SOAPY_SDR_RX, 0, test_freq)
        sdr.setSampleRate(SOAPY_SDR_RX, 0, test_rate)
        sdr.setGain(SOAPY_SDR_RX, 0, test_gain)

        result["tests"]["configure"] = {
            "status": "pass",
            "frequency_MHz": test_freq / 1e6,
            "sample_rate_kSPS": test_rate / 1e3,
            "gain_dB": test_gain,
        }
        log.info(f"  ✓ Configure: {test_freq/1e6:.1f} MHz, {test_rate/1e3:.0f} kSPS")
    except Exception as e:
        result["tests"]["configure"] = {"status": "fail", "error": str(e)}
        result["passed"] = False
        log.error(f"  ✗ Configure: FAILED ({e})")
        return result

    # Test 3: Read samples
    try:
        stream = sdr.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        sdr.activateStream(stream)

        buf = np.zeros(65536, dtype=np.complex64)
        sr = sdr.readStream(stream, [buf], 65536)

        sdr.deactivateStream(stream)
        sdr.closeStream(stream)

        if sr.ret > 0:
            result["tests"]["read_samples"] = {
                "status": "pass",
                "samples_read": sr.ret,
            }
            log.info(f"  ✓ Read: {sr.ret} samples")
        else:
            result["tests"]["read_samples"] = {
                "status": "fail",
                "error": f"readStream returned {sr.ret}",
            }
            result["passed"] = False
            log.error(f"  ✗ Read: returned {sr.ret}")
            return result
    except Exception as e:
        result["tests"]["read_samples"] = {"status": "fail", "error": str(e)}
        result["passed"] = False
        log.error(f"  ✗ Read: FAILED ({e})")
        return result

    # Test 4: Validate IQ data
    try:
        samples = buf[: sr.ret]
        all_zero = np.all(samples == 0)
        has_nan = np.any(np.isnan(np.abs(samples)))
        has_inf = np.any(np.isinf(np.abs(samples)))
        power = np.mean(np.abs(samples) ** 2)

        if all_zero:
            result["tests"]["iq_valid"] = {
                "status": "fail",
                "error": "All samples are zero",
            }
            result["passed"] = False
            log.error("  ✗ IQ Valid: All samples are zero!")
        elif has_nan or has_inf:
            result["tests"]["iq_valid"] = {
                "status": "fail",
                "error": "NaN/Inf in samples",
            }
            result["passed"] = False
            log.error("  ✗ IQ Valid: NaN/Inf detected!")
        else:
            result["tests"]["iq_valid"] = {
                "status": "pass",
                "power_dbfs": round(10 * np.log10(power + 1e-20), 2),
                "dc_offset": round(float(np.abs(np.mean(samples))), 6),
            }
            log.info(f"  ✓ IQ Valid: power={10*np.log10(power+1e-20):.1f} dBFS")
    except Exception as e:
        result["tests"]["iq_valid"] = {"status": "fail", "error": str(e)}

    return result


def quick_benchmark(
    device_info: Dict, duration_s: float = 5.0, simulate: bool = False
) -> Dict:
    """Run a quick 5-second throughput benchmark."""
    result = {
        "driver": device_info.get("driver", "unknown"),
        "duration_s": duration_s,
    }

    if simulate or device_info.get("simulated"):
        import numpy as np

        # Simulate throughput test
        rate = 2.4e6
        total = int(rate * duration_s)
        result["sample_rate_requested"] = rate
        result["total_samples"] = total
        result["actual_sps"] = rate * 0.99  # Simulate 99% efficiency
        result["efficiency_pct"] = 99.0
        result["dropped_chunks"] = 0
        return result

    try:
        import numpy as np
        import SoapySDR
        from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX

        driver = device_info.get("driver", "")
        rate = RECOMMENDED_SETTINGS.get(driver, {}).get("optimal_rate", 2.4e6)

        sdr = SoapySDR.Device(device_info)
        sdr.setFrequency(SOAPY_SDR_RX, 0, 100e6)
        sdr.setSampleRate(SOAPY_SDR_RX, 0, rate)
        sdr.setGain(SOAPY_SDR_RX, 0, 30.0)

        stream = sdr.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        sdr.activateStream(stream)

        chunk = 65536
        buf = np.zeros(chunk, dtype=np.complex64)
        total_samples = 0
        dropped = 0

        start = time.perf_counter()
        deadline = start + duration_s

        while time.perf_counter() < deadline:
            sr = sdr.readStream(stream, [buf], chunk)
            if sr.ret > 0:
                total_samples += sr.ret
            else:
                dropped += 1

        elapsed = time.perf_counter() - start

        sdr.deactivateStream(stream)
        sdr.closeStream(stream)

        actual_rate = total_samples / elapsed if elapsed > 0 else 0
        efficiency = (actual_rate / rate * 100) if rate > 0 else 0

        result.update(
            {
                "sample_rate_requested": rate,
                "total_samples": total_samples,
                "actual_sps": round(actual_rate, 0),
                "efficiency_pct": round(efficiency, 2),
                "dropped_chunks": dropped,
                "actual_duration_s": round(elapsed, 2),
            }
        )

        log.info(f"  Throughput: {actual_rate/1e6:.2f} MSPS ({efficiency:.1f}%)")

    except Exception as e:
        result["error"] = str(e)
        log.error(f"  Benchmark failed: {e}")

    return result


def check_kernel_conflicts() -> List[Dict]:
    """Check for kernel module conflicts (Linux only)."""
    conflicts = []

    if platform.system() != "Linux":
        return conflicts

    import subprocess

    try:
        result = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=5)
        loaded_modules = result.stdout

        for driver, info in KERNEL_CONFLICTS.items():
            for module in info["modules"]:
                if module in loaded_modules:
                    conflicts.append(
                        {
                            "driver": driver,
                            "conflicting_module": module,
                            "fix_command": info["fix"],
                            "permanent_fix": info["permanent"],
                        }
                    )
                    log.warning(
                        f"Kernel conflict: '{module}' blocks {driver}. "
                        f"Fix: {info['fix']}"
                    )
    except Exception:
        pass

    return conflicts


def get_recommended_settings(driver: str) -> Dict:
    """Get recommended settings for a specific device type."""
    return RECOMMENDED_SETTINGS.get(
        driver,
        {
            "sample_rates": [250e3, 1e6, 2.4e6],
            "optimal_rate": 2.4e6,
            "optimal_gain": 30.0,
            "buffer_size": 131072,
            "notes": "No specific recommendations available for this device.",
        },
    )


def generate_report(
    devices: List[Dict],
    self_tests: List[Dict],
    benchmarks: List[Dict],
    conflicts: List[Dict],
    output_path: str = None,
) -> Dict:
    """Generate a comprehensive hardware validation report."""
    report = {
        "meta": {
            "tool": "DynamiX Labs SDR Hardware Validator",
            "version": "1.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
        },
        "discovered_devices": devices,
        "self_tests": self_tests,
        "benchmarks": benchmarks,
        "kernel_conflicts": conflicts,
        "recommendations": {},
        "summary": {},
    }

    # Add recommendations for each detected device
    for dev in devices:
        driver = dev.get("driver", "")
        report["recommendations"][driver] = get_recommended_settings(driver)

    # Summary
    total = len(self_tests)
    passed = sum(1 for t in self_tests if t.get("passed", False))
    report["summary"] = {
        "devices_found": len(devices),
        "tests_passed": passed,
        "tests_failed": total - passed,
        "conflicts_detected": len(conflicts),
        "overall": "PASS" if passed == total and not conflicts else "NEEDS ATTENTION",
    }

    # Save report
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        report_file = (
            path / "hardware_validation_report.json" if path.is_dir() else path
        )
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2, default=str)
        log.info(f"Report saved: {report_file}")

    return report


# ─── Main ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="DynamiX Labs — SDR Hardware Validation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run in simulation mode (no hardware required)",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Output directory or file path for JSON report",
    )
    parser.add_argument(
        "--skip-benchmark",
        action="store_true",
        help="Skip throughput benchmark (faster validation)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    print()
    print("=" * 60)
    print("  DynamiX Labs — SDR Hardware Validation Tool")
    print("=" * 60)
    print()

    # Step 1: Discover devices
    print("▸ Discovering SDR devices...")
    devices = discover_devices(simulate=args.simulate)

    if not devices:
        print("\n  ⚠ No SDR devices found!")
        print("  Ensure your device is plugged in and drivers are installed.")
        print("  Run: SoapySDRUtil --find")
        if not args.simulate:
            sys.exit(1)

    for dev in devices:
        driver = dev.get("driver", "?")
        label = dev.get("label", dev.get("driver", "Unknown"))
        serial = dev.get("serial", "N/A")
        print(f"  ✓ Found: {label} (driver={driver}, serial={serial})")

    # Step 2: Check kernel conflicts (Linux)
    print("\n▸ Checking for kernel module conflicts...")
    conflicts = check_kernel_conflicts()
    if conflicts:
        for c in conflicts:
            print(f"  ⚠ Conflict: {c['conflicting_module']} blocks {c['driver']}")
            print(f"    Fix: {c['fix_command']}")
    else:
        print("  ✓ No conflicts detected")

    # Step 3: Self-test each device
    print("\n▸ Running self-tests...")
    self_tests = []
    for dev in devices:
        print(f"\n  Testing: {dev.get('label', dev.get('driver', 'Unknown'))}...")
        result = self_test_device(dev, simulate=args.simulate)
        self_tests.append(result)
        status = "PASS ✓" if result["passed"] else "FAIL ✗"
        print(f"  Result: {status}")

    # Step 4: Quick benchmark
    benchmarks = []
    if not args.skip_benchmark:
        print("\n▸ Running quick throughput benchmarks (5s each)...")
        for dev in devices:
            print(
                f"  Benchmarking: {dev.get('label', dev.get('driver', 'Unknown'))}..."
            )
            bm = quick_benchmark(dev, duration_s=5.0, simulate=args.simulate)
            benchmarks.append(bm)

    # Step 5: Recommendations
    print("\n▸ Recommended settings:")
    for dev in devices:
        driver = dev.get("driver", "")
        settings = get_recommended_settings(driver)
        print(f"\n  {driver.upper()}:")
        print(f"    Optimal rate: {settings.get('optimal_rate', 0)/1e6:.1f} MSPS")
        print(f"    Optimal gain: {settings.get('optimal_gain', 30):.0f} dB")
        print(f"    Buffer size:  {settings.get('buffer_size', 131072)} samples")
        if settings.get("notes"):
            print(f"    Note: {settings['notes']}")

    # Step 6: Generate report
    report = generate_report(
        devices,
        self_tests,
        benchmarks,
        conflicts,
        output_path=args.report,
    )

    # Summary
    print("\n" + "=" * 60)
    summary = report["summary"]
    overall = summary["overall"]
    print(f"  Devices: {summary['devices_found']} found")
    print(
        f"  Tests:   {summary['tests_passed']}/{summary['tests_passed'] + summary['tests_failed']} passed"
    )
    print(f"  Overall: {overall}")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
