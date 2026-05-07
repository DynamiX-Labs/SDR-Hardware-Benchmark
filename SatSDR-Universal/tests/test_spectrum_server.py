"""
Tests for WebSocket Spectrum Streaming
DynamiX Labs | Phase 3
"""
import numpy as np
import pytest
import sys, os, time, struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestStreamProtocol:
    def test_psd_roundtrip(self):
        from streaming.stream_protocol import SpectrumFrame, pack_frame, unpack_frame
        payload = np.random.randn(4096).astype(np.float32)
        frame = SpectrumFrame(channel="spectrum.psd", fft_size=4096,
                              sample_rate=250000.0, center_freq=137.5e6, payload=payload)
        unpacked = unpack_frame(pack_frame(frame))
        assert unpacked.channel == "spectrum.psd"
        np.testing.assert_allclose(unpacked.payload, payload, rtol=1e-5)

    def test_detection_roundtrip(self):
        from streaming.stream_protocol import SpectrumFrame, pack_frame, unpack_frame
        dets = [{"snr_db": 15.3, "bw": 6000.0}]
        frame = SpectrumFrame(channel="spectrum.detections", detections=dets)
        unpacked = unpack_frame(pack_frame(frame))
        assert len(unpacked.detections) == 1

    def test_magic_bytes(self):
        from streaming.stream_protocol import SpectrumFrame, pack_frame, MAGIC
        packed = pack_frame(SpectrumFrame(channel="spectrum.psd", payload=np.zeros(10, dtype=np.float32)))
        assert struct.unpack("!I", packed[:4])[0] == MAGIC

    def test_invalid_magic(self):
        from streaming.stream_protocol import unpack_frame
        with pytest.raises(ValueError):
            unpack_frame(b"\x00" * 20)

    def test_short_frame(self):
        from streaming.stream_protocol import unpack_frame
        with pytest.raises(ValueError):
            unpack_frame(b"\x00\x01")

    def test_compression(self):
        from streaming.stream_protocol import SpectrumFrame, pack_frame, unpack_frame
        payload = np.random.randn(32768).astype(np.float32)
        frame = SpectrumFrame(channel="spectrum.psd", payload=payload)
        unpacked = unpack_frame(pack_frame(frame, compress_threshold=1024))
        np.testing.assert_allclose(unpacked.payload, payload, rtol=1e-5)

    def test_json_mode(self):
        import json
        from streaming.stream_protocol import SpectrumFrame, frame_to_json
        frame = SpectrumFrame(channel="spectrum.psd", payload=np.ones(10, dtype=np.float32))
        data = json.loads(frame_to_json(frame))
        assert data["channel"] == "spectrum.psd"
        assert "payload_b64" in data

    def test_waterfall_matrix(self):
        from streaming.stream_protocol import SpectrumFrame, pack_frame, unpack_frame
        matrix = np.random.randn(50, 4096).astype(np.float32)
        frame = SpectrumFrame(channel="spectrum.waterfall", fft_size=4096, payload=matrix)
        unpacked = unpack_frame(pack_frame(frame, compress_threshold=1024))
        assert unpacked.payload.shape == (50, 4096)


class TestSpectrumServerConfig:
    def test_server_init(self):
        try:
            from streaming.spectrum_server import SpectrumServer
            s = SpectrumServer(port=0)
            assert s.max_fps == 30
            assert s.fft_size == 4096
        except ImportError:
            pytest.skip("websockets not installed")

    def test_channels(self):
        try:
            from streaming.spectrum_server import SpectrumServer
            assert "spectrum.psd" in SpectrumServer.CHANNELS
            assert "system.status" in SpectrumServer.CHANNELS
        except ImportError:
            pytest.skip("websockets not installed")

    def test_status(self):
        try:
            from streaming.spectrum_server import SpectrumServer
            s = SpectrumServer(port=0)
            st = s.get_status()
            assert st["clients"] == 0
        except ImportError:
            pytest.skip("websockets not installed")
