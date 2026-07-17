"""
Tests for Distributed Decoder Cluster (Models, Broker, Worker)
DynamiX Labs | Phase 3
"""
import pytest
import sys, os, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestModels:
    """Test cluster data models and serialization."""

    def test_job_status_enum(self):
        from cluster.models import JobStatus
        assert str(JobStatus.QUEUED) == "queued"
        assert JobStatus("complete") == JobStatus.COMPLETE

    def test_decode_job_creation(self):
        from cluster.models import DecodeJob, JobStatus
        job = DecodeJob(decoder_type="apt", iq_path="/data/test.iq")
        assert job.decoder_type == "apt"
        assert job.status == JobStatus.QUEUED
        assert len(job.job_id) == 36  # UUID4

    def test_decode_job_roundtrip(self):
        from cluster.models import DecodeJob, JobStatus
        job = DecodeJob(decoder_type="adsb", iq_path="/data/adsb.iq",
                        config={"sample_rate": 2e6}, priority=3)
        d = job.to_dict()
        restored = DecodeJob.from_dict(d)
        assert restored.decoder_type == "adsb"
        assert restored.priority == 3
        assert restored.status == JobStatus.QUEUED
        assert restored.config["sample_rate"] == 2e6

    def test_decode_result_roundtrip(self):
        from cluster.models import DecodeResult, JobStatus
        result = DecodeResult(job_id="abc-123", status=JobStatus.COMPLETE,
                              output_path="/out/image.png",
                              metrics={"decode_time_s": 1.5})
        d = result.to_dict()
        restored = DecodeResult.from_dict(d)
        assert restored.status == JobStatus.COMPLETE
        assert restored.metrics["decode_time_s"] == 1.5

    def test_worker_info(self):
        from cluster.models import WorkerInfo
        w = WorkerInfo(worker_id="w1", capabilities=["apt", "adsb"],
                       gpu_available=True, max_concurrent=4)
        assert w.can_decode("apt")
        assert w.has_capacity()
        assert w.is_alive(timeout=30.0)

    def test_worker_capacity_check(self):
        from cluster.models import WorkerInfo
        w = WorkerInfo(worker_id="w2", max_concurrent=2, active_jobs=2)
        assert not w.has_capacity()

    def test_worker_heartbeat_timeout(self):
        from cluster.models import WorkerInfo
        w = WorkerInfo(worker_id="w3", last_heartbeat=time.time() - 60)
        assert not w.is_alive(timeout=30.0)

    def test_serialize_deserialize(self):
        from cluster.models import DecodeJob, serialize, deserialize
        job = DecodeJob(decoder_type="apt", iq_path="/test.iq")
        raw = serialize(job)
        assert isinstance(raw, bytes)
        restored = deserialize(raw)
        assert restored["decoder_type"] == "apt"

    def test_job_retry_fields(self):
        from cluster.models import DecodeJob
        job = DecodeJob(decoder_type="apt", iq_path="/test.iq",
                        max_retries=5)
        assert job.retries == 0
        assert job.max_retries == 5


class TestBrokerInit:
    """Test broker initialization (no actual ZMQ connections)."""

    def test_broker_creation(self):
        try:
            from cluster.broker import DecoderBroker
            b = DecoderBroker()
            assert b.frontend_addr == "tcp://*:5555"
            assert b.backend_addr == "tcp://*:5556"
        except ImportError:
            pytest.skip("pyzmq not installed")

    def test_broker_metrics(self):
        try:
            from cluster.broker import DecoderBroker
            b = DecoderBroker()
            m = b.get_metrics()
            assert m["workers_total"] == 0
            assert m["queue_depth"] == 0
            assert m["jobs_submitted"] == 0
        except ImportError:
            pytest.skip("pyzmq not installed")

    def test_broker_submit_job(self):
        try:
            from cluster.broker import DecoderBroker
            from cluster.models import DecodeJob
            b = DecoderBroker()
            job = DecodeJob(decoder_type="apt", iq_path="/test.iq")
            jid = b.submit_job(job)
            assert jid == job.job_id
            assert b.get_metrics()["queue_depth"] == 1
        except ImportError:
            pytest.skip("pyzmq not installed")

    def test_broker_job_status(self):
        try:
            from cluster.broker import DecoderBroker
            from cluster.models import DecodeJob
            b = DecoderBroker()
            job = DecodeJob(decoder_type="adsb", iq_path="/test.iq")
            b.submit_job(job)
            status = b.get_status(job.job_id)
            assert status is not None
            assert status["status"] == "queued"
        except ImportError:
            pytest.skip("pyzmq not installed")

    def test_unknown_job_status(self):
        try:
            from cluster.broker import DecoderBroker
            b = DecoderBroker()
            assert b.get_status("nonexistent") is None
        except ImportError:
            pytest.skip("pyzmq not installed")


class TestWorkerInit:
    """Test worker initialization (no actual ZMQ connections)."""

    def test_worker_creation(self):
        try:
            from cluster.worker import DecoderWorker
            w = DecoderWorker(broker_addr="tcp://localhost:5556")
            assert w.broker_addr == "tcp://localhost:5556"
            assert w.max_concurrent == 2
            assert w._jobs_processed == 0
        except ImportError:
            pytest.skip("pyzmq not installed")

    def test_worker_custom_id(self):
        try:
            from cluster.worker import DecoderWorker
            w = DecoderWorker(worker_id="test-worker-01")
            assert w.worker_id == "test-worker-01"
        except ImportError:
            pytest.skip("pyzmq not installed")

    def test_worker_capabilities(self):
        try:
            from cluster.worker import DecoderWorker
            w = DecoderWorker(capabilities=["apt", "adsb"])
            assert w.capabilities == ["apt", "adsb"]
        except ImportError:
            pytest.skip("pyzmq not installed")
