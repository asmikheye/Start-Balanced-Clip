"""Hardware detection: compute device + auto-selected Whisper model size.

Extracted from ``pipeline.main`` so the shared ``DEVICE`` / ``CUDA_AVAILABLE`` /
``WHISPER_MODEL`` state lives in one place that both the transcription and
reframe modules can import without a circular dependency on ``main``. The
detection (including the CUDA-usability probe) runs once at import, exactly as
it did at the top of ``main``.
"""
import os

import torch

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Test if CUDA actually works for faster-whisper (needs libcublas via ctranslate2).
# Creating the model is not enough — libcublas only loads during actual encoding.
CUDA_AVAILABLE = False
GPU_VRAM_GB = 0
WHISPER_COMPUTE_TYPE = "int8"
if DEVICE == "cuda":
    try:
        import ctranslate2 as _ct2
        from faster_whisper import WhisperModel as _WM
        import numpy as _np
        _supported = _ct2.get_supported_compute_types("cuda")
        _compute_type = next((kind for kind in ("float16", "int8_float32", "int8") if kind in _supported), None)
        if _compute_type is None:
            raise RuntimeError("no supported CUDA compute type for Faster-Whisper")
        _m = _WM("tiny", device="cuda", compute_type=_compute_type)
        _dummy = _np.zeros(16000, dtype=_np.float32)
        _segments, _ = _m.transcribe(_dummy)
        list(_segments)  # inference is lazy; run it before marking CUDA usable
        del _m, _dummy
        CUDA_AVAILABLE = True
        WHISPER_COMPUTE_TYPE = _compute_type
        GPU_VRAM_GB = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1)
        print(f"✅ CUDA runtime verified — GPU {torch.cuda.get_device_name(0)} ({GPU_VRAM_GB}GB VRAM), Whisper {_compute_type}")
    except Exception as e:
        CUDA_AVAILABLE = False
        print(f"⚠️  CUDA not usable for Whisper: {type(e).__name__} — using CPU")
else:
    print("ℹ️  No CUDA detected — using CPU")

# Auto-select Whisper model based on available hardware
# Models: tiny (39M) < base (74M) < small (244M) < medium (769M) < large-v3 (1.55B)
import psutil as _psutil_check
_total_ram_gb = round(_psutil_check.virtual_memory().total / (1024**3), 1)

if CUDA_AVAILABLE:
    if GPU_VRAM_GB >= 6:
        WHISPER_MODEL = "large-v3"
    elif GPU_VRAM_GB >= 3:
        WHISPER_MODEL = "medium"
    else:
        WHISPER_MODEL = "small"
else:
    if _total_ram_gb >= 16:
        WHISPER_MODEL = "medium"
    elif _total_ram_gb >= 8:
        WHISPER_MODEL = "small"
    else:
        WHISPER_MODEL = "base"

# Allow override via env var
WHISPER_MODEL = os.getenv("WHISPER_MODEL", WHISPER_MODEL)
print(f"🎙️  Whisper model: {WHISPER_MODEL} (auto-selected for {'GPU ' + str(GPU_VRAM_GB) + 'GB' if CUDA_AVAILABLE else 'CPU ' + str(_total_ram_gb) + 'GB RAM'})")
