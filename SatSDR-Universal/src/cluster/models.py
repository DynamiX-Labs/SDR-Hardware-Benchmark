"""
Distributed Decoder Cluster — Shared Data Models
MessagePack-serialisable job and worker descriptors.
DynamiX Labs | Phase 3
"""

import uuid
import time
import enum
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

# Try msgpack, fall back to JSON
try:
    import msgpack  # type: ignore[import-untyped]
    _USE_MSGPACK = True
except ImportError:
    import json
    _USE_MSGPACK = False


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class JobStatus(enum.Enum):
    """Lifecycle states for a decode job."""
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"

    def __str__(self):
        return self.value


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class DecodeJob:
    """
    A unit of work submitted to the distributed decoder cluster.

    Attributes
    ----------
    job_id : str
        Unique job identifier (UUID4).
    decoder_type : str
        Decoder name ("apt", "adsb", "ax25", "lrpt", etc.).
    iq_path : str
        Filesystem path to the IQ recording file.
    config : dict
        Decoder-specific configuration (sample_rate, frequency, etc.).
    priority : int
        Job priority (lower = higher priority).  Default 5.
    status : JobStatus
        Current lifecycle state.
    submitted_at : float
        UNIX timestamp when the job was submitted.
    dispatched_at : float
        UNIX timestamp when the job was sent to a worker.
    completed_at : float
        UNIX timestamp when the job finished.
    worker_id : str
        ID of the worker processing this job (empty if unassigned).
    retries : int
        Number of times this job has been retried after failure.
    max_retries : int
        Maximum retry attempts before marking as permanently failed.
    """

    decoder_type: str
    iq_path: str
    config: Dict[str, Any] = field(default_factory=dict)
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    priority: int = 5
    status: JobStatus = JobStatus.QUEUED
    submitted_at: float = field(default_factory=time.time)
    dispatched_at: float = 0.0
    completed_at: float = 0.0
    worker_id: str = ""
    retries: int = 0
    max_retries: int = 3

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DecodeJob":
        d = dict(d)  # copy
        if isinstance(d.get("status"), str):
            d["status"] = JobStatus(d["status"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class DecodeResult:
    """
    Result produced by a decoder worker after processing a job.

    Attributes
    ----------
    job_id : str
        Corresponding job identifier.
    status : JobStatus
        Final status (COMPLETE or FAILED).
    output_path : str
        Path to decoder output (image, JSON, etc.).
    metrics : dict
        Performance metrics (decode_time_s, snr_db, etc.).
    error : str
        Error message if status is FAILED.
    completed_at : float
        UNIX timestamp when processing finished.
    """

    job_id: str
    status: JobStatus = JobStatus.COMPLETE
    output_path: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    completed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DecodeResult":
        d = dict(d)
        if isinstance(d.get("status"), str):
            d["status"] = JobStatus(d["status"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class WorkerInfo:
    """
    Descriptor for a registered decoder worker node.

    Attributes
    ----------
    worker_id : str
        Unique worker identifier.
    capabilities : list[str]
        Decoder types this worker can handle.
    gpu_available : bool
        Whether the worker has GPU acceleration.
    max_concurrent : int
        Maximum concurrent jobs.
    active_jobs : int
        Currently running jobs on this worker.
    last_heartbeat : float
        UNIX timestamp of last heartbeat.
    address : str
        ZeroMQ endpoint address.
    """

    worker_id: str
    capabilities: List[str] = field(default_factory=list)
    gpu_available: bool = False
    max_concurrent: int = 2
    active_jobs: int = 0
    last_heartbeat: float = field(default_factory=time.time)
    address: str = ""

    def is_alive(self, timeout: float = 30.0) -> bool:
        """Check if worker heartbeat is within timeout."""
        return (time.time() - self.last_heartbeat) < timeout

    def has_capacity(self) -> bool:
        """Check if worker can accept more jobs."""
        return self.active_jobs < self.max_concurrent

    def can_decode(self, decoder_type: str) -> bool:
        """Check if worker supports the given decoder type."""
        return decoder_type in self.capabilities or not self.capabilities

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WorkerInfo":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def serialize(obj) -> bytes:
    """Serialise a model to bytes (msgpack or JSON fallback)."""
    if hasattr(obj, "to_dict"):
        data = obj.to_dict()
    elif isinstance(obj, dict):
        data = obj
    else:
        data = {"value": obj}

    if _USE_MSGPACK:
        return msgpack.packb(data, use_bin_type=True)
    else:
        return json.dumps(data).encode("utf-8")


def deserialize(raw: bytes) -> dict:
    """Deserialise bytes into a dict."""
    if _USE_MSGPACK:
        return msgpack.unpackb(raw, raw=False)
    else:
        return json.loads(raw.decode("utf-8"))
