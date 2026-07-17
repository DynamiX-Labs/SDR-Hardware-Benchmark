"""
Distributed Decoder Cluster — Central Job Broker
ZeroMQ ROUTER/DEALER load-balanced work distribution.
DynamiX Labs | Phase 3

Architecture:
    Clients ──PUSH──►  [ROUTER:5555]  Broker  [DEALER:5556]  ──►  Workers
    Monitor ◄─────── [PUB:5557]

The broker receives decode jobs on a ROUTER socket (frontend),
dispatches them round-robin to available workers via a DEALER socket
(backend), and publishes status events on a PUB socket (monitor).

Workers register by sending a READY message with their capabilities.
Heartbeat-based health monitoring reaps dead workers and re-queues
their in-flight jobs automatically.

Usage:
    broker = DecoderBroker()
    broker.start()   # blocking
"""

import threading
import time
import uuid
import logging
from typing import Dict, List, Optional
from collections import deque

log = logging.getLogger("satsdr.cluster.broker")

try:
    import zmq  # type: ignore[import-untyped]
    _ZMQ_AVAILABLE = True
except ImportError:
    _ZMQ_AVAILABLE = False

from .models import (
    DecodeJob, DecodeResult, JobStatus, WorkerInfo,
    serialize, deserialize,
)

# Wire-level command constants
CMD_READY = b"READY"
CMD_HEARTBEAT = b"HEARTBEAT"
CMD_JOB = b"JOB"
CMD_RESULT = b"RESULT"
CMD_DISCONNECT = b"DISCONNECT"
CMD_SUBMIT = b"SUBMIT"
CMD_STATUS = b"STATUS"


class DecoderBroker:
    """
    ZeroMQ-based distributed decoder job broker.

    Manages a pool of decoder workers, distributes jobs based on
    capability and load, and monitors worker health via heartbeats.

    Parameters
    ----------
    frontend_addr : str
        ROUTER socket address for client job submissions.
    backend_addr : str
        DEALER socket address for worker connections.
    monitor_addr : str
        PUB socket address for status event broadcasting.
    heartbeat_interval : float
        Seconds between heartbeat checks (default 5.0).
    worker_timeout : float
        Seconds before a silent worker is considered dead (default 30.0).
    """

    def __init__(
        self,
        frontend_addr: str = "tcp://*:5555",
        backend_addr: str = "tcp://*:5556",
        monitor_addr: str = "tcp://*:5557",
        heartbeat_interval: float = 5.0,
        worker_timeout: float = 30.0,
    ):
        if not _ZMQ_AVAILABLE:
            raise ImportError("pyzmq required: pip install pyzmq>=25.0")

        self.frontend_addr = frontend_addr
        self.backend_addr = backend_addr
        self.monitor_addr = monitor_addr
        self.heartbeat_interval = heartbeat_interval
        self.worker_timeout = worker_timeout

        # State
        self._workers: Dict[str, WorkerInfo] = {}
        self._job_queue: deque = deque()
        self._active_jobs: Dict[str, DecodeJob] = {}    # job_id → job
        self._completed_jobs: Dict[str, DecodeResult] = {}
        self._worker_jobs: Dict[str, str] = {}           # worker_id → job_id
        self._running = False
        self._lock = threading.Lock()

        # Metrics
        self._jobs_submitted = 0
        self._jobs_completed = 0
        self._jobs_failed = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self):
        """Start the broker event loop (blocking)."""
        self._running = True
        ctx = zmq.Context()

        # Frontend: ROUTER for clients
        frontend = ctx.socket(zmq.ROUTER)
        frontend.bind(self.frontend_addr)

        # Backend: ROUTER for workers (not DEALER — we need worker identity)
        backend = ctx.socket(zmq.ROUTER)
        backend.bind(self.backend_addr)

        # Monitor: PUB for status events
        monitor = ctx.socket(zmq.PUB)
        monitor.bind(self.monitor_addr)

        poller = zmq.Poller()
        poller.register(frontend, zmq.POLLIN)
        poller.register(backend, zmq.POLLIN)

        log.info(f"Broker started | Frontend: {self.frontend_addr} | "
                 f"Backend: {self.backend_addr} | Monitor: {self.monitor_addr}")

        last_heartbeat_check = time.time()

        try:
            while self._running:
                events = dict(poller.poll(timeout=1000))  # 1s poll

                # --- Handle client messages (frontend) ---
                if frontend in events:
                    self._handle_frontend(frontend, monitor)

                # --- Handle worker messages (backend) ---
                if backend in events:
                    self._handle_backend(backend, monitor)

                # --- Periodic heartbeat check ---
                if time.time() - last_heartbeat_check > self.heartbeat_interval:
                    self._check_heartbeats(backend, monitor)
                    last_heartbeat_check = time.time()

                # --- Dispatch queued jobs ---
                self._dispatch_jobs(backend, monitor)

        except KeyboardInterrupt:
            log.info("Broker interrupted by user")
        finally:
            self._running = False
            frontend.close()
            backend.close()
            monitor.close()
            ctx.term()
            log.info("Broker shut down")

    def submit_job(self, job: DecodeJob) -> str:
        """
        Submit a decode job to the queue (thread-safe).

        Can be called from a separate thread while the broker
        event loop is running.

        Returns the job_id.
        """
        with self._lock:
            job.status = JobStatus.QUEUED
            self._job_queue.append(job)
            self._jobs_submitted += 1
            log.info(f"Job submitted: {job.job_id} | decoder={job.decoder_type} | "
                     f"path={job.iq_path}")
        return job.job_id

    def get_status(self, job_id: str) -> Optional[dict]:
        """Get the current status of a job."""
        with self._lock:
            if job_id in self._completed_jobs:
                return self._completed_jobs[job_id].to_dict()
            if job_id in self._active_jobs:
                return self._active_jobs[job_id].to_dict()
            for job in self._job_queue:
                if job.job_id == job_id:
                    return job.to_dict()
        return None

    def get_metrics(self) -> dict:
        """Return broker performance metrics."""
        with self._lock:
            return {
                "workers_total": len(self._workers),
                "workers_alive": sum(1 for w in self._workers.values() if w.is_alive(self.worker_timeout)),
                "queue_depth": len(self._job_queue),
                "active_jobs": len(self._active_jobs),
                "jobs_submitted": self._jobs_submitted,
                "jobs_completed": self._jobs_completed,
                "jobs_failed": self._jobs_failed,
            }

    # ------------------------------------------------------------------
    # Internal: Frontend (client) message handler
    # ------------------------------------------------------------------
    def _handle_frontend(self, frontend, monitor):
        """Process messages from clients on the frontend ROUTER."""
        frames = frontend.recv_multipart()
        if len(frames) < 3:
            return

        client_id = frames[0]
        # frames[1] is empty delimiter
        cmd = frames[2]

        if cmd == CMD_SUBMIT and len(frames) >= 4:
            # Client submits a job
            job_data = deserialize(frames[3])
            job = DecodeJob.from_dict(job_data)
            self.submit_job(job)
            # Acknowledge
            frontend.send_multipart([
                client_id, b"", CMD_STATUS,
                serialize({"job_id": job.job_id, "status": "queued"}),
            ])
            self._publish_event(monitor, "job_submitted", job.to_dict())

        elif cmd == CMD_STATUS:
            # Client requests status
            if len(frames) >= 4:
                job_id = frames[3].decode("utf-8")
                status = self.get_status(job_id)
            else:
                status = self.get_metrics()
            frontend.send_multipart([
                client_id, b"",
                CMD_STATUS, serialize(status or {"error": "not_found"}),
            ])

    # ------------------------------------------------------------------
    # Internal: Backend (worker) message handler
    # ------------------------------------------------------------------
    def _handle_backend(self, backend, monitor):
        """Process messages from workers on the backend ROUTER."""
        frames = backend.recv_multipart()
        if len(frames) < 3:
            return

        worker_addr = frames[0]
        # frames[1] is empty delimiter
        cmd = frames[2]
        worker_id = worker_addr.decode("utf-8", errors="replace")

        with self._lock:
            if cmd == CMD_READY:
                # Worker registration
                if len(frames) >= 4:
                    info_data = deserialize(frames[3])
                    worker = WorkerInfo.from_dict(info_data)
                else:
                    worker = WorkerInfo(worker_id=worker_id)
                worker.address = worker_id
                worker.last_heartbeat = time.time()
                self._workers[worker_id] = worker
                log.info(f"Worker registered: {worker_id} | "
                         f"caps={worker.capabilities} | gpu={worker.gpu_available}")
                self._publish_event(monitor, "worker_registered", worker.to_dict())

            elif cmd == CMD_HEARTBEAT:
                if worker_id in self._workers:
                    self._workers[worker_id].last_heartbeat = time.time()
                    if len(frames) >= 4:
                        info = deserialize(frames[3])
                        self._workers[worker_id].active_jobs = info.get("active_jobs", 0)

            elif cmd == CMD_RESULT:
                # Worker completed a job
                if len(frames) >= 4:
                    result_data = deserialize(frames[3])
                    result = DecodeResult.from_dict(result_data)
                    self._handle_result(worker_id, result)
                    self._publish_event(monitor, "job_completed", result.to_dict())

            elif cmd == CMD_DISCONNECT:
                self._handle_worker_disconnect(worker_id)

    # ------------------------------------------------------------------
    # Internal: Job dispatch
    # ------------------------------------------------------------------
    def _dispatch_jobs(self, backend, monitor):
        """Dispatch queued jobs to available workers."""
        with self._lock:
            while self._job_queue:
                job = self._job_queue[0]
                worker = self._find_worker(job.decoder_type)
                if worker is None:
                    break  # No available workers

                self._job_queue.popleft()
                job.status = JobStatus.DISPATCHED
                job.dispatched_at = time.time()
                job.worker_id = worker.worker_id
                self._active_jobs[job.job_id] = job
                self._worker_jobs[worker.worker_id] = job.job_id
                worker.active_jobs += 1

                # Send job to worker
                backend.send_multipart([
                    worker.address.encode("utf-8"),
                    b"",
                    CMD_JOB,
                    serialize(job.to_dict()),
                ])

                log.info(f"Dispatched job {job.job_id[:8]}… → worker {worker.worker_id[:8]}…")
                self._publish_event(monitor, "job_dispatched", {
                    "job_id": job.job_id,
                    "worker_id": worker.worker_id,
                })

    def _find_worker(self, decoder_type: str) -> Optional[WorkerInfo]:
        """Find the best available worker for a decoder type."""
        candidates = [
            w for w in self._workers.values()
            if w.is_alive(self.worker_timeout)
            and w.has_capacity()
            and w.can_decode(decoder_type)
        ]
        if not candidates:
            return None

        # Prefer GPU workers for FFT-heavy decoders
        gpu_heavy = {"apt", "lrpt", "hrpt"}
        if decoder_type in gpu_heavy:
            gpu_workers = [w for w in candidates if w.gpu_available]
            if gpu_workers:
                candidates = gpu_workers

        # Least-loaded first
        candidates.sort(key=lambda w: w.active_jobs)
        return candidates[0]

    # ------------------------------------------------------------------
    # Internal: Result handling
    # ------------------------------------------------------------------
    def _handle_result(self, worker_id: str, result: DecodeResult):
        """Process a completed job result from a worker."""
        job_id = result.job_id
        if job_id in self._active_jobs:
            del self._active_jobs[job_id]
        if worker_id in self._worker_jobs:
            del self._worker_jobs[worker_id]
        if worker_id in self._workers:
            self._workers[worker_id].active_jobs = max(
                0, self._workers[worker_id].active_jobs - 1
            )

        self._completed_jobs[job_id] = result

        if result.status == JobStatus.COMPLETE:
            self._jobs_completed += 1
            log.info(f"Job {job_id[:8]}… completed by {worker_id[:8]}…")
        else:
            self._jobs_failed += 1
            log.warning(f"Job {job_id[:8]}… failed: {result.error}")

            # Retry logic
            for job in list(self._active_jobs.values()):
                if job.job_id == job_id and job.retries < job.max_retries:
                    job.retries += 1
                    job.status = JobStatus.QUEUED
                    job.worker_id = ""
                    self._job_queue.appendleft(job)
                    log.info(f"Re-queued job {job_id[:8]}… (retry {job.retries}/{job.max_retries})")

    # ------------------------------------------------------------------
    # Internal: Heartbeat monitoring
    # ------------------------------------------------------------------
    def _check_heartbeats(self, backend, monitor):
        """Detect dead workers and re-queue their jobs."""
        with self._lock:
            dead_workers = [
                wid for wid, w in self._workers.items()
                if not w.is_alive(self.worker_timeout)
            ]
            for wid in dead_workers:
                log.warning(f"Worker {wid[:8]}… timed out — reaping")
                self._handle_worker_disconnect(wid)
                self._publish_event(monitor, "worker_timeout", {"worker_id": wid})

    def _handle_worker_disconnect(self, worker_id: str):
        """Handle worker disconnection — re-queue its jobs."""
        if worker_id in self._worker_jobs:
            job_id = self._worker_jobs[worker_id]
            if job_id in self._active_jobs:
                job = self._active_jobs.pop(job_id)
                job.status = JobStatus.QUEUED
                job.worker_id = ""
                self._job_queue.appendleft(job)
                log.info(f"Re-queued job {job_id[:8]}… from dead worker {worker_id[:8]}…")
            del self._worker_jobs[worker_id]

        if worker_id in self._workers:
            del self._workers[worker_id]
            log.info(f"Worker {worker_id[:8]}… removed")

    # ------------------------------------------------------------------
    # Internal: Monitor PUB
    # ------------------------------------------------------------------
    def _publish_event(self, monitor, event_type: str, data: dict):
        """Publish a status event on the monitor PUB socket."""
        try:
            monitor.send_multipart([
                event_type.encode("utf-8"),
                serialize(data),
            ])
        except Exception:
            pass
