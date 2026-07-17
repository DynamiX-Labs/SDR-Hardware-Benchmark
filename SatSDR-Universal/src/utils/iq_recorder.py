"""
IQ Recording & Replay Engine
SigMF-compliant metadata, gzip-compressed storage, and precise replay.
"""
import numpy as np
import gzip
import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("satsdr.iq_recorder")


class IQRecorder:
    """
    Records IQ samples to disk with SigMF-compliant metadata.
    Supports gzip compression for efficient storage.
    """

    def __init__(self, output_dir: str = "./recordings"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def record(self, samples: np.ndarray, frequency: float, sample_rate: float,
               gain: float, hardware: str, compress: bool = True) -> str:
        """
        Save IQ samples with SigMF metadata.
        Returns the path to the saved recording.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base_name = f"capture_{timestamp}_{frequency/1e6:.3f}MHz"

        # Write IQ data
        if compress:
            iq_path = self.output_dir / f"{base_name}.iq.gz"
            with gzip.open(iq_path, 'wb') as f:
                f.write(samples.astype(np.complex64).tobytes())
        else:
            iq_path = self.output_dir / f"{base_name}.iq"
            samples.astype(np.complex64).tofile(str(iq_path))

        # Write SigMF metadata
        meta = {
            "global": {
                "core:datatype": "cf32_le",
                "core:sample_rate": sample_rate,
                "core:hw": hardware,
                "core:version": "1.0.0",
                "core:recorder": "SatSDR-Universal",
                "core:description": f"Capture at {frequency/1e6:.3f} MHz"
            },
            "captures": [
                {
                    "core:sample_start": 0,
                    "core:frequency": frequency,
                    "core:datetime": datetime.now(timezone.utc).isoformat(),
                    "satsdr:gain_db": gain,
                    "satsdr:compressed": compress
                }
            ],
            "annotations": []
        }

        meta_path = self.output_dir / f"{base_name}.sigmf-meta"
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=2)

        log.info(f"Recorded {len(samples)} samples to {iq_path.name}")
        return str(iq_path)

    def replay(self, iq_path: str) -> tuple:
        """
        Load a recorded IQ file and its SigMF metadata.
        Returns (samples, metadata_dict).
        """
        path = Path(iq_path)

        # Load IQ data
        if path.suffix == '.gz' or str(path).endswith('.iq.gz'):
            with gzip.open(path, 'rb') as f:
                raw = f.read()
        else:
            with open(path, 'rb') as f:
                raw = f.read()

        samples = np.frombuffer(raw, dtype=np.complex64)

        # Load metadata
        meta_path = path.with_suffix('').with_suffix('.sigmf-meta')
        if not str(path).endswith('.iq.gz'):
            meta_path = path.with_suffix('.sigmf-meta')
        else:
            meta_path = Path(str(path).replace('.iq.gz', '.sigmf-meta'))

        metadata = {}
        if meta_path.exists():
            with open(meta_path, 'r') as f:
                metadata = json.load(f)

        log.info(f"Replayed {len(samples)} samples from {path.name}")
        return samples, metadata
