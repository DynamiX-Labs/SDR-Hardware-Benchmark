"""Decoder registry — maps decoder names to classes."""
from .apt_decoder import APTDecoder
from .adsb_decoder import ADSBDecoder
from .hrpt_decoder import HRPTDecoder
from .acars_decoder import ACARSDecoder

DECODER_REGISTRY = {
    "apt":   APTDecoder,
    "adsb":  ADSBDecoder,
    "hrpt":  HRPTDecoder,
    "acars": ACARSDecoder,
}

def get_decoder(name: str):
    cls = DECODER_REGISTRY.get(name.lower())
    if cls is None:
        raise ValueError(f"Unknown decoder: {name}. Available: {list(DECODER_REGISTRY)}")
    return cls
