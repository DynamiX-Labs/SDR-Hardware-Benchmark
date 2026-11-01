"""Decoder registry — maps decoder names to classes."""

from .acars_decoder import ACARSDecoder
from .adsb_decoder import ADSBDecoder
from .apt_decoder import APTDecoder
from .hrpt_decoder import HRPTDecoder
from .lrpt_decoder import LRPTDecoder
from .sstv_decoder import SSTVDecoder

DECODER_REGISTRY = {
    "apt": APTDecoder,
    "adsb": ADSBDecoder,
    "hrpt": HRPTDecoder,
    "acars": ACARSDecoder,
    "lrpt": LRPTDecoder,
    "sstv": SSTVDecoder,
}


def get_decoder(name: str):
    cls = DECODER_REGISTRY.get(name.lower())
    if cls is None:
        raise ValueError(
            f"Unknown decoder: {name}. Available: {list(DECODER_REGISTRY)}"
        )
    return cls
