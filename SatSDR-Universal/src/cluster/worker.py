"""
Distributed Decoder Cluster — Worker Node
Connects to broker, pulls decode jobs, executes decoders.
DynamiX Labs | Phase 3

A worker node registers with the broker by sending a READY message
containing its capabilities (supported decoder types, GPU status).
It then enters a loop: pull a job, execute the appropriate decoder,
and return the result.

Usage:
    worker = DecoderWorker(broker_addr="tcp://broker-host:5556")
    worker.start()   # blocking
"""

import threading
import time
import uuid
import logging
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, Future

log = logging.getLogger("satsdr.cluster.worker")

try:
    import zmq  # type: ignore[import-untyped]
    _ZMQ_AVAILABLE = True
except ImportError:
    _ZMQ_AVAILABLE = False

import numpy as np

from .models import (
    DecodeJob, DecodeResult, JobStatus, WorkerInfo,
    serialize, deserialize,
)

# Wire-level command constants (must match broker.py)
CMD_READY = b"READY"
CMD_HEARTBEAT = b"HEARTBEAT"
CMD_JOB = b"JOB"
CMD_RESULT = b"RESULT"
CMD_DISCONNECT = b"DISCONNECT"


class DecoderWorker:
    """
    Distributed decoder worker — connects to broker and processes jobs.

    Parameters
    ----------
    broker_addr : str
        ZeroMQ address of the broker's backend socket.
    worker_id : str, optional
        Unique worker ID.  Auto-generated if not provided.
    gpu_backend : GPUBackend, optional
        GPU backend for accelerated DSP.
    max_concurrent : int
        Maximum number of concurrent decode jobs (default 2).
    capabilities : list[str], optional
        Decoder types this worker supports.  Empty = all.
    heartbeat_interval : float
        Seconds between heartbeat messages to broker (default 5.0).
    """

    def __init__(
        self,
        broker_addr: str = "tcp://localhost:5556",
        worker_id: Optional[str] = None,
        gpu_backend=None,
        max_concurrent: int = 2,
        capabilities: Optional[List[str]] = None,
        heartbeat_interval: float = 5.0,
    ):
        if not _ZMQ_AVAILABLE:
            raise ImportError("pyzmq required: pip install pyzmq>=25.0")

        self.broker_addr = broker_addr
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.gpu = gpu_backend
        self.max_concurrent = max_concurrent
        self.capabilities = capabilities or []
        self.heartbeat_interval = heartbeat_interval

        self._running = False
        self._active_jobs: int = 0
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent, thread_name_prefix="decoder"
        )
        self._futures: dict = {}  # job_id → Future

        # Metrics
        self._jobs_processed = 0
        self._jobs_failed = 0
        self._total_decode_time = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self):
        """Start the worker event loop (blocking)."""
        self._running = True
        ctx = zmq.Context()

        socket = ctx.socket(zmq.DEALER)
        socket.identity = self.worker_id.encode("utf-8")
        socket.connect(self.broker_addr)

        log.info(f"Worker {self.worker_id} connecting to broker at {self.broker_addr}")
        log.info(f"  Capabilities: {self.capabilities or ['all']}")
        log.info(f"  GPU: {'enabled' if self.gpu and self.gpu.gpu_available else 'disabled'}")
        log.info(f"  Max concurrent: {self.max_concurrent}")

        # Register with broker
        self._send_ready(socket)

        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)

        last_heartbeat = time.time()

        try:
            while self._running:
                events = dict(poller.poll(timeout=1000))

                if socket in events:
                    self._handle_message(socket)

                # Send heartbeat
                if time.time() - last_heartbeat > self.heartbeat_interval:
                    self._send_heartbeat(socket)
                    last_heartbeat = time.time()

                # Check completed futures
                self._collect_results(socket)

        except KeyboardInterrupt:
            log.info("Worker interrupted by user")
        finally:
            self._running = False
            # Graceful shutdown — wait for in-flight jobs
            log.info("Waiting for in-flight jobs to complete...")
            self._executor.shutdown(wait=True, cancel_futures=False)
            socket.send_multipart([b"", CMD_DISCONNECT])
            socket.close()
            ctx.term()
            log.info(f"Worker {self.worker_id} shut down | "
                     f"processed={self._jobs_processed} failed={self._jobs_failed}")

    def stop(self):
        """Signal the worker to stop."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal: Registration & heartbeat
    # ------------------------------------------------------------------
    def _send_ready(self, socket):
        """Send READY registration to broker."""
        info = WorkerInfo(
            worker_id=self.worker_id,
            capabilities=self.capabilities,
            gpu_available=bool(self.gpu and self.gpu.gpu_available),
            max_concurrent=self.max_concurrent,
            active_jobs=self._active_jobs,
        )
        socket.send_multipart([b"", CMD_READY, serialize(info.to_dict())])
        log.debug("Sent READY to broker")

    def _send_heartbeat(self, socket):
        """Send periodic heartbeat to broker."""
        socket.send_multipart([
            b"", CMD_HEARTBEAT,
            serialize({"active_jobs": self._active_jobs}),
        ])

    # ------------------------------------------------------------------
    # Internal: Message handler
    # ------------------------------------------------------------------
    def _handle_message(self, socket):
        """Process a message from the broker."""
        frames = socket.recv_multipart()
        if len(frames) < 2:
            return

        # frames[0] is empty delimiter
        cmd = frames[1]

        if cmd == CMD_JOB and len(frames) >= 3:
            job_data = deserialize(frames[2])
            job = DecodeJob.from_dict(job_data)
            log.info(f"Received job {job.job_id[:8]}… | decoder={job.decoder_type}")

            if self._active_jobs >= self.max_concurrent:
                # At capacity — send failure to re-queue
                result = DecodeResult(
                    job_id=job.job_id,
                    status=JobStatus.FAILED,
                    error="Worker at capacity",
                )
                socket.send_multipart([b"", CMD_RESULT, serialize(result.to_dict())])
                return

            # Submit to thread pool
            self._active_jobs += 1
            future = self._executor.submit(self._execute_job, job)
            self._futures[job.job_id] = future

    # ------------------------------------------------------------------
    # Internal: Job execution
    # ------------------------------------------------------------------
    def _execute_job(self, job: DecodeJob) -> DecodeResult:
        """
        Execute a decode job using the appropriate decoder.

        This runs in a worker thread from the ThreadPoolExecutor.
        """
        t0 = time.time()
        log.info(f"Executing job {job.job_id[:8]}… | decoder={job.decoder_type}")

        try:
            # Import decoder
            decoder = self._get_decoder(job.decoder_type, job.config)

            # Load IQ data
            samples = self._load_iq(job.iq_path)

            # Run decode
            result_data = decoder.decode(samples)

            decode_time = time.time() - t0
            self._total_decode_time += decode_time

            return DecodeResult(
                job_id=job.job_id,
                status=JobStatus.COMPLETE,
                output_path=str(decoder.output_dir),
                metrics={
                    "decode_time_s": round(decode_time, 3),
                    "samples_processed": len(samples),
                    "result_keys": list(result_data.keys()) if isinstance(result_data, dict) else [],
                },
            )

        except Exception as exc:
            decode_time = time.time() - t0
            log.error(f"Job {job.job_id[:8]}… failed: {exc}")
            return DecodeResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                error=str(exc),
                metrics={"decode_time_s": round(decode_time, 3)},
            )

    def _get_decoder(self, decoder_type: str, config: dict):
        """Instantiate a decoder by type name."""
        from ..decoders import get_decoder

        sample_rate = config.get("sample_rate", 250_000)
        output_dir = config.get("output_dir", "./output")

        dec_cls = get_decoder(decoder_type)
        return dec_cls(sample_rate=sample_rate, output_dir=output_dir)

    def _load_iq(self, iq_path: str) -> np.ndarray:
        """Load IQ samples from a file."""
        import gzip
        from pathlib import Path

        path = Path(iq_path)

        if path.suffix == ".gz" or str(path).endswith(".iq.gz"):
            with gzip.open(path, "rb") as f:
                raw = f.read()
        else:
            with open(path, "rb") as f:
                raw = f.read()

        return np.frombuffer(raw, dtype=np.complex64)

    # ------------------------------------------------------------------
    # Internal: Result collection
    # ------------------------------------------------------------------
    def _collect_results(self, socket):
        """Check for completed futures and send results to broker."""
        completed = [
            jid for jid, fut in self._futures.items() if fut.done()
        ]

        for job_id in completed:
            future = self._futures.pop(job_id)
            self._active_jobs = max(0, self._active_jobs - 1)

            try:
                result = future.result()
            except Exception as exc:
                result = DecodeResult(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    error=str(exc),
                )

            if result.status == JobStatus.COMPLETE:
                self._jobs_processed += 1
            else:
                self._jobs_failed += 1

            # Send result to broker
            socket.send_multipart([b"", CMD_RESULT, serialize(result.to_dict())])
            log.info(f"Sent result for job {job_id[:8]}… | status={result.status}")
