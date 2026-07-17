"""
ProcessingModule — SatDump-inspired modular pipeline block system.

Ported from SatDump's ProcessingModule architecture (module.h / pipeline.h).
Provides a base class for pipeline stages with lifecycle management,
type negotiation, stats reporting, and module registry.

DynamiX Labs
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Type
from enum import Enum, auto

import numpy as np

log = logging.getLogger("satsdr.module")


class ModuleDataType(Enum):
    """Data types that modules can accept/produce."""
    FILE = auto()           # Generic file data
    STREAM = auto()         # Generic byte stream (circular buffer)
    DSP_STREAM = auto()     # Complex float DSP stream
    PACKET_STREAM = auto()  # Decoded packet stream


@dataclass
class ModuleStats:
    """Runtime statistics for a processing module."""
    samples_processed: int = 0
    bytes_processed: int = 0
    processing_time_ms: float = 0.0
    throughput_sps: float = 0.0    # Samples per second
    is_running: bool = False
    error_count: int = 0
    custom: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        d = {
            "samples_processed": self.samples_processed,
            "bytes_processed": self.bytes_processed,
            "processing_time_ms": round(self.processing_time_ms, 2),
            "throughput_sps": round(self.throughput_sps, 0),
            "is_running": self.is_running,
            "error_count": self.error_count,
        }
        d.update(self.custom)
        return d


class ProcessingModule(ABC):
    """
    Base class for all pipeline processing modules.

    Lifecycle:
        1. __init__(parameters) — Configure the module
        2. set_input_type() / set_output_type() — Negotiate data types
        3. init() — Prepare for processing
        4. process(data) — Run processing (may be called repeatedly)
        5. stop() — Signal module to stop (for streaming modes)
        6. get_output() — Retrieve output file path (if file mode)
        7. get_stats() — Get module runtime statistics

    Subclasses must implement:
        - process(data) -> result
        - get_id() -> str
    """

    def __init__(self, parameters: Dict = None):
        self.parameters = parameters or {}
        self._input_type = ModuleDataType.DSP_STREAM
        self._output_type = ModuleDataType.DSP_STREAM
        self._stats = ModuleStats()
        self._output_file = ""
        self._is_initialized = False
        self._should_stop = False

    # ── Type Negotiation ─────────────────────────────────────────────────

    def get_input_types(self) -> List[ModuleDataType]:
        """Override to specify supported input types."""
        return [ModuleDataType.FILE, ModuleDataType.DSP_STREAM]

    def get_output_types(self) -> List[ModuleDataType]:
        """Override to specify supported output types."""
        return [ModuleDataType.FILE, ModuleDataType.DSP_STREAM]

    def set_input_type(self, dtype: ModuleDataType):
        if dtype not in self.get_input_types():
            raise ValueError(f"Module {self.get_id()} does not support input type {dtype}")
        self._input_type = dtype

    def set_output_type(self, dtype: ModuleDataType):
        if dtype not in self.get_output_types():
            raise ValueError(f"Module {self.get_id()} does not support output type {dtype}")
        self._output_type = dtype

    @property
    def input_type(self) -> ModuleDataType:
        return self._input_type

    @property
    def output_type(self) -> ModuleDataType:
        return self._output_type

    # ── Lifecycle ────────────────────────────────────────────────────────

    def init(self):
        """Initialize the module. Called before process()."""
        self._is_initialized = True
        self._should_stop = False
        self._stats = ModuleStats()
        log.debug(f"Module {self.get_id()} initialized")

    def stop(self):
        """Signal the module to stop processing."""
        self._should_stop = True
        self._stats.is_running = False
        log.debug(f"Module {self.get_id()} stop requested")

    @abstractmethod
    def process(self, data: Any) -> Any:
        """
        Process input data and return output.

        Args:
            data: Input data (type depends on input_type setting)

        Returns:
            Processed output (type depends on output_type setting)
        """
        pass

    def process_timed(self, data: Any) -> Any:
        """Process with automatic timing and stats collection."""
        self._stats.is_running = True
        t0 = time.perf_counter()

        try:
            result = self.process(data)
        except Exception as e:
            self._stats.error_count += 1
            log.error(f"Module {self.get_id()} error: {e}")
            raise

        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._stats.processing_time_ms += elapsed_ms

        # Update sample count
        if isinstance(data, np.ndarray):
            self._stats.samples_processed += len(data)
            if elapsed_ms > 0:
                self._stats.throughput_sps = len(data) / (elapsed_ms / 1000)
        elif isinstance(data, bytes):
            self._stats.bytes_processed += len(data)

        return result

    def get_output(self) -> str:
        """Get path of output file (when output_type is FILE)."""
        return self._output_file

    # ── Stats & Identity ─────────────────────────────────────────────────

    @abstractmethod
    def get_id(self) -> str:
        """Return unique module identifier string."""
        pass

    def get_stats(self) -> Dict:
        """Get module runtime statistics as a dictionary."""
        return self._stats.to_dict()

    def get_params(self) -> Dict:
        """Return module parameters for serialization."""
        return dict(self.parameters)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id='{self.get_id()}'>"


# ── Module Registry ──────────────────────────────────────────────────────

@dataclass
class ModuleEntry:
    """Registry entry for a processing module."""
    module_id: str
    module_class: Type[ProcessingModule]
    default_params: Dict = field(default_factory=dict)
    description: str = ""


class ModuleRegistry:
    """
    Global registry for processing modules.

    Modules register themselves and can be instantiated by ID.
    Inspired by SatDump's REGISTER_MODULE macro and modules_registry.
    """

    _registry: Dict[str, ModuleEntry] = {}

    @classmethod
    def register(cls, module_class: Type[ProcessingModule],
                 default_params: Dict = None, description: str = ""):
        """Register a module class."""
        module_id = module_class.get_id_static() if hasattr(module_class, 'get_id_static') else module_class.__name__
        cls._registry[module_id] = ModuleEntry(
            module_id=module_id,
            module_class=module_class,
            default_params=default_params or {},
            description=description,
        )
        log.debug(f"Registered module: {module_id}")

    @classmethod
    def get_instance(cls, module_id: str, parameters: Dict = None) -> ProcessingModule:
        """Create an instance of a registered module."""
        if module_id not in cls._registry:
            raise KeyError(f"Unknown module: {module_id}. "
                           f"Available: {list(cls._registry.keys())}")
        entry = cls._registry[module_id]
        params = {**entry.default_params, **(parameters or {})}
        return entry.module_class(params)

    @classmethod
    def exists(cls, module_id: str) -> bool:
        return module_id in cls._registry

    @classmethod
    def list_modules(cls) -> List[Dict]:
        return [
            {"id": e.module_id, "description": e.description,
             "params": e.default_params}
            for e in cls._registry.values()
        ]


# ── Pipeline Preset System ──────────────────────────────────────────────

@dataclass
class PipelinePreset:
    """Pre-configured pipeline for common satellite modes."""
    name: str
    description: str
    sample_rate: float
    frequency: float
    steps: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "sample_rate": self.sample_rate,
            "frequency": self.frequency,
            "steps": self.steps,
        }


# Built-in presets inspired by SatDump pipeline definitions
PIPELINE_PRESETS = {
    "noaa_apt": PipelinePreset(
        name="NOAA APT",
        description="NOAA APT weather image decoder (137 MHz)",
        sample_rate=250_000,
        frequency=137.1e6,
        steps=[
            {"module": "dc_removal", "params": {}},
            {"module": "lowpass", "params": {"cutoff": 50000}},
            {"module": "fm_demod", "params": {}},
            {"module": "resample", "params": {"output_rate": 20800}},
            {"module": "apt_sync", "params": {}},
        ],
    ),
    "meteor_lrpt": PipelinePreset(
        name="METEOR LRPT",
        description="METEOR-M LRPT QPSK decoder",
        sample_rate=300_000,
        frequency=137.1e6,
        steps=[
            {"module": "dc_removal", "params": {}},
            {"module": "agc", "params": {}},
            {"module": "costas_qpsk", "params": {"loop_bw": 0.01}},
            {"module": "clock_recovery", "params": {"sps": 4.0}},
            {"module": "viterbi", "params": {"rate": "1/2", "k": 7}},
            {"module": "ccsds_deframe", "params": {}},
        ],
    ),
    "cubesat_ax25": PipelinePreset(
        name="CubeSat AX.25",
        description="Generic CubeSat AX.25 beacon decoder",
        sample_rate=48_000,
        frequency=145.825e6,
        steps=[
            {"module": "dc_removal", "params": {}},
            {"module": "fm_demod", "params": {}},
            {"module": "clock_recovery", "params": {"sps": 40.0}},
            {"module": "hdlc_deframe", "params": {}},
            {"module": "ax25_decode", "params": {}},
        ],
    ),
    "adsb_1090": PipelinePreset(
        name="ADS-B 1090 MHz",
        description="ADS-B aircraft transponder decoder",
        sample_rate=2_000_000,
        frequency=1090e6,
        steps=[
            {"module": "dc_removal", "params": {}},
            {"module": "adsb_demod", "params": {}},
        ],
    ),
    "noaa_hrpt": PipelinePreset(
        name="NOAA HRPT",
        description="NOAA HRPT high-resolution decoder",
        sample_rate=3_000_000,
        frequency=1698e6,
        steps=[
            {"module": "dc_removal", "params": {}},
            {"module": "agc", "params": {}},
            {"module": "costas_bpsk", "params": {"loop_bw": 0.005}},
            {"module": "clock_recovery", "params": {"sps": 3.0}},
            {"module": "manchester_decode", "params": {}},
            {"module": "cadu_sync", "params": {"frame_size": 11090}},
            {"module": "reed_solomon", "params": {}},
        ],
    ),
}


def get_preset(name: str) -> Optional[PipelinePreset]:
    """Get a pipeline preset by name."""
    return PIPELINE_PRESETS.get(name)


def list_presets() -> List[str]:
    """List all available pipeline preset names."""
    return list(PIPELINE_PRESETS.keys())
