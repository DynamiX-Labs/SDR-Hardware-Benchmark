"""
Satellite Database — YAML-based satellite configuration registry.

Inspired by:
  - gr-satellites satyaml: Per-satellite YAML definitions with frequencies,
    modulation, framing, and telemetry field layouts
  - FoxTelem spacecraft directory: Spacecraft configuration files

DynamiX Labs
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from pathlib import Path

log = logging.getLogger("cubesat.satdb")

# ── Built-in Satellite Definitions ───────────────────────────────────────
# These are embedded so the system works out of the box without external files.

BUILTIN_SATELLITES = {
    "NOAA-15": {
        "norad_id": 25338,
        "name": "NOAA 15",
        "frequencies": {
            "apt": 137.620e6,
            "hrpt": 1702.5e6,
        },
        "modulation": "APT-AM",
        "framing": "APT",
        "fec": "none",
        "status": "active",
        "telemetry_fields": {},
    },
    "NOAA-18": {
        "norad_id": 28654,
        "name": "NOAA 18",
        "frequencies": {
            "apt": 137.9125e6,
            "hrpt": 1707.0e6,
        },
        "modulation": "APT-AM",
        "framing": "APT",
        "fec": "none",
        "status": "active",
        "telemetry_fields": {},
    },
    "NOAA-19": {
        "norad_id": 33591,
        "name": "NOAA 19",
        "frequencies": {
            "apt": 137.100e6,
            "hrpt": 1698.0e6,
        },
        "modulation": "APT-AM",
        "framing": "APT",
        "fec": "none",
        "status": "active",
        "telemetry_fields": {},
    },
    "METEOR-M2": {
        "norad_id": 40069,
        "name": "METEOR-M 2",
        "frequencies": {
            "lrpt": 137.100e6,
            "hrpt": 1700.0e6,
        },
        "modulation": "QPSK",
        "framing": "CCSDS",
        "fec": "viterbi",
        "status": "active",
        "telemetry_fields": {},
    },
    "METEOR-M2-3": {
        "norad_id": 57166,
        "name": "METEOR-M2-3",
        "frequencies": {
            "lrpt": 137.900e6,
            "hrpt": 1700.0e6,
        },
        "modulation": "QPSK",
        "framing": "CCSDS",
        "fec": "viterbi",
        "status": "active",
        "telemetry_fields": {},
    },
    "ISS-ZARYA": {
        "norad_id": 25544,
        "name": "ISS (ZARYA)",
        "frequencies": {
            "aprs": 145.825e6,
            "sstv": 145.800e6,
        },
        "modulation": "AFSK-1200",
        "framing": "AX.25",
        "fec": "none",
        "status": "active",
        "telemetry_fields": {},
    },
    "CAS-4A": {
        "norad_id": 42761,
        "name": "CAS-4A (ZHUHAI-1 OG1)",
        "frequencies": {
            "beacon": 145.855e6,
        },
        "modulation": "GMSK-4800",
        "framing": "AX.25",
        "fec": "none",
        "status": "active",
        "telemetry_fields": {
            "rf_power_mw": {"offset": 0, "size": 2, "format": ">H", "unit": "mW"},
            "obc_temp_c": {"offset": 2, "size": 2, "format": ">h", "scale": 0.1, "unit": "°C"},
            "bus_voltage_v": {"offset": 4, "size": 2, "format": ">H", "scale": 0.01, "unit": "V"},
            "bus_current_ma": {"offset": 6, "size": 2, "format": ">H", "unit": "mA"},
        },
    },
    "CAS-4B": {
        "norad_id": 42759,
        "name": "CAS-4B (ZHUHAI-1 OG2)",
        "frequencies": {
            "beacon": 145.910e6,
        },
        "modulation": "GMSK-4800",
        "framing": "AX.25",
        "fec": "none",
        "status": "active",
        "telemetry_fields": {
            "rf_power_mw": {"offset": 0, "size": 2, "format": ">H", "unit": "mW"},
            "obc_temp_c": {"offset": 2, "size": 2, "format": ">h", "scale": 0.1, "unit": "°C"},
            "bus_voltage_v": {"offset": 4, "size": 2, "format": ">H", "scale": 0.01, "unit": "V"},
            "bus_current_ma": {"offset": 6, "size": 2, "format": ">H", "unit": "mA"},
        },
    },
    "FUNCUBE-1": {
        "norad_id": 39444,
        "name": "FUNcube-1 (AO-73)",
        "frequencies": {
            "beacon": 145.935e6,
        },
        "modulation": "BPSK-1200",
        "framing": "AX.25",
        "fec": "none",
        "status": "active",
        "telemetry_fields": {
            "solar_panel_v": {"offset": 0, "size": 2, "format": ">H", "scale": 0.001, "unit": "V"},
            "battery_v": {"offset": 2, "size": 2, "format": ">H", "scale": 0.001, "unit": "V"},
            "battery_temp_c": {"offset": 4, "size": 2, "format": ">h", "scale": 0.1, "unit": "°C"},
        },
    },
    "AMSAT-OSCAR-7": {
        "norad_id": 7530,
        "name": "AMSAT-OSCAR 7 (AO-7)",
        "frequencies": {
            "beacon": 145.977e6,
            "beacon_b": 29.502e6,
        },
        "modulation": "CW",
        "framing": "none",
        "fec": "none",
        "status": "active",
        "telemetry_fields": {},
    },
    "FOX-1A": {
        "norad_id": 40908,
        "name": "Fox-1A (AO-85)",
        "frequencies": {
            "beacon": 145.980e6,
        },
        "modulation": "DUV-200",
        "framing": "FOX",
        "fec": "reed_solomon",
        "status": "inactive",
        "telemetry_fields": {
            "battery_a_v": {"offset": 0, "size": 2, "format": ">H", "scale": 0.001, "unit": "V"},
            "battery_b_v": {"offset": 2, "size": 2, "format": ">H", "scale": 0.001, "unit": "V"},
            "battery_c_v": {"offset": 4, "size": 2, "format": ">H", "scale": 0.001, "unit": "V"},
            "panel_temp_c": {"offset": 6, "size": 2, "format": ">h", "scale": 0.1, "unit": "°C"},
        },
    },
    "FOX-1CLIFF": {
        "norad_id": 43770,
        "name": "Fox-1Cliff (AO-95)",
        "frequencies": {
            "beacon": 145.920e6,
        },
        "modulation": "DUV-200",
        "framing": "FOX",
        "fec": "reed_solomon",
        "status": "active",
        "telemetry_fields": {},
    },
}


@dataclass
class SatelliteConfig:
    """Configuration for a single satellite."""
    norad_id: int = 0
    name: str = ""
    frequencies: Dict[str, float] = field(default_factory=dict)
    modulation: str = ""
    framing: str = ""
    fec: str = "none"
    status: str = "unknown"
    telemetry_fields: Dict[str, Dict] = field(default_factory=dict)
    tle: Optional[Dict[str, str]] = None

    @property
    def primary_frequency(self) -> float:
        """Return the first (primary) frequency."""
        if self.frequencies:
            return next(iter(self.frequencies.values()))
        return 0.0


class SatelliteDatabase:
    """
    Satellite configuration database with YAML loading and query support.

    Usage:
        db = SatelliteDatabase()
        db.load_builtin()
        db.load_yaml("path/to/custom_satellites.yaml")

        sat = db.get("CAS-4A")
        sats = db.find_by_frequency(145.855e6, tolerance_hz=25e3)
    """

    def __init__(self):
        self._satellites: Dict[str, SatelliteConfig] = {}

    def load_builtin(self):
        """Load built-in satellite definitions."""
        for key, data in BUILTIN_SATELLITES.items():
            self._satellites[key] = SatelliteConfig(**data)
        log.info(f"Loaded {len(self._satellites)} built-in satellite definitions")

    def load_yaml(self, path: str):
        """Load satellite definitions from a YAML file."""
        try:
            import yaml
        except ImportError:
            log.error("PyYAML not installed. Cannot load YAML satellite database.")
            return

        filepath = Path(path)
        if not filepath.exists():
            log.warning(f"Satellite database file not found: {path}")
            return

        try:
            with open(filepath, "r") as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict):
                log.error(f"Invalid satellite database format in {path}")
                return

            satellites = data.get("satellites", data)
            count = 0
            for key, sat_data in satellites.items():
                if isinstance(sat_data, dict):
                    self._satellites[key] = SatelliteConfig(**sat_data)
                    count += 1

            log.info(f"Loaded {count} satellites from {filepath.name}")
        except Exception as e:
            log.error(f"Failed to load satellite database: {e}")

    def load_directory(self, directory: str):
        """Load all YAML files from a directory."""
        dir_path = Path(directory)
        if not dir_path.is_dir():
            log.warning(f"Satellite database directory not found: {directory}")
            return

        for yaml_file in dir_path.glob("*.yaml"):
            self.load_yaml(str(yaml_file))
        for yml_file in dir_path.glob("*.yml"):
            self.load_yaml(str(yml_file))

    def get(self, key: str) -> Optional[SatelliteConfig]:
        """Get satellite configuration by key."""
        return self._satellites.get(key)

    def get_by_norad(self, norad_id: int) -> Optional[SatelliteConfig]:
        """Find satellite by NORAD catalog ID."""
        for sat in self._satellites.values():
            if sat.norad_id == norad_id:
                return sat
        return None

    def find_by_frequency(self, freq_hz: float,
                          tolerance_hz: float = 25e3) -> List[SatelliteConfig]:
        """Find satellites operating near a given frequency."""
        matches = []
        for sat in self._satellites.values():
            for f in sat.frequencies.values():
                if abs(f - freq_hz) <= tolerance_hz:
                    matches.append(sat)
                    break
        return matches

    def find_by_modulation(self, modulation: str) -> List[SatelliteConfig]:
        """Find satellites using a specific modulation scheme."""
        mod_lower = modulation.lower()
        return [s for s in self._satellites.values()
                if mod_lower in s.modulation.lower()]

    def find_by_framing(self, framing: str) -> List[SatelliteConfig]:
        """Find satellites using a specific framing protocol."""
        frame_lower = framing.lower()
        return [s for s in self._satellites.values()
                if frame_lower in s.framing.lower()]

    def active_satellites(self) -> List[SatelliteConfig]:
        """Return only active satellites."""
        return [s for s in self._satellites.values() if s.status == "active"]

    def list_all(self) -> List[str]:
        """List all satellite keys."""
        return list(self._satellites.keys())

    @property
    def count(self) -> int:
        return len(self._satellites)

    def auto_select_decoder(self, sat_key: str) -> Optional[Dict[str, str]]:
        """
        Auto-select the decoder pipeline based on satellite configuration.

        Returns a dict with 'decoder', 'modulation', 'fec', 'framing' keys.
        """
        sat = self.get(sat_key)
        if not sat:
            return None

        # Map modulation → decoder
        mod = sat.modulation.upper()
        decoder_map = {
            "APT-AM": "apt",
            "QPSK": "lrpt",
            "AFSK-1200": "ax25",
            "GMSK-4800": "ax25",
            "BPSK-1200": "ax25",
            "DUV-200": "ax25",
        }

        decoder = decoder_map.get(mod, "generic")

        return {
            "decoder": decoder,
            "modulation": sat.modulation,
            "fec": sat.fec,
            "framing": sat.framing,
            "frequency": sat.primary_frequency,
        }

    def summary(self) -> str:
        """Print a summary table of all satellites."""
        lines = [
            f"\n{'Key':<18} {'Name':<28} {'Freq (MHz)':<12} {'Mod':<14} {'Status':<10}",
            "─" * 85,
        ]
        for key, sat in sorted(self._satellites.items()):
            freq_str = f"{sat.primary_frequency / 1e6:.3f}" if sat.primary_frequency else "N/A"
            lines.append(
                f"{key:<18} {sat.name:<28} {freq_str:<12} "
                f"{sat.modulation:<14} {sat.status:<10}"
            )
        return "\n".join(lines)
