import os
import sys
import pkgutil

repo_root = os.path.abspath(os.path.dirname(__file__))

subdirs = [
    os.path.join(repo_root, "CubeSat-Telemetry-Decoder"),
    os.path.join(repo_root, "Doppler-Auto-Tracker"),
    os.path.join(repo_root, "SDR-Hardware-Benchmark"),
    os.path.join(repo_root, "SatSDR-Universal"),
    os.path.join(repo_root, "SatSDR-Universal", "src"),
]

for p in subdirs:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import src
    src.__path__ = pkgutil.extend_path(src.__path__, src.__name__)
except ImportError:
    pass
