"""Metadata APIs needed by pinned pyannote/SpeechBrain on TorchAudio 2.9+.

Only supply removed APIs. Keep TorchAudio's decoder, encoder and resampler,
and leave older runtimes' native implementations unchanged.
"""
from dataclasses import dataclass


@dataclass
class AudioMetaData:
    sample_rate: int
    num_frames: int
    num_channels: int
    bits_per_sample: int
    encoding: str


def _soundfile_info(filepath, format=None, backend=None):
    if backend not in (None, "soundfile"):
        raise ValueError(f"Metadata compatibility supports soundfile, not {backend!r}")
    import soundfile
    import torchaudio

    info = soundfile.info(filepath)
    subtype = info.subtype
    bits = {"PCM_S8": 8, "PCM_U8": 8, "PCM_16": 16, "PCM_24": 24,
            "PCM_32": 32, "FLOAT": 32, "DOUBLE": 64, "ULAW": 8, "ALAW": 8,
            "DWVW_12": 12, "DWVW_16": 16, "DWVW_24": 24,
            "DPCM_8": 8, "DPCM_16": 16,
            "ALAC_16": 16, "ALAC_20": 20, "ALAC_24": 24, "ALAC_32": 32}
    encodings = {"PCM_S8": "PCM_S", "PCM_16": "PCM_S", "PCM_24": "PCM_S",
                 "PCM_32": "PCM_S", "PCM_U8": "PCM_U", "FLOAT": "PCM_F",
                 "DOUBLE": "PCM_F", "ULAW": "ULAW", "ALAW": "ALAW",
                 "VORBIS": "VORBIS"}
    encoding = "FLAC" if info.format == "FLAC" else encodings.get(subtype, "UNKNOWN")
    return torchaudio.AudioMetaData(info.samplerate, info.frames, info.channels,
                                   bits.get(subtype, 0), encoding)


def ensure_legacy_audio_metadata():
    """Run before importing pyannote or SpeechBrain; no model/device work."""
    import torchaudio

    if all(hasattr(torchaudio, name) for name in ("AudioMetaData", "info", "list_audio_backends")):
        return
    # Verify the actual installed backend before advertising it.
    import soundfile
    soundfile.available_formats()
    if not hasattr(torchaudio, "AudioMetaData"):
        torchaudio.AudioMetaData = AudioMetaData
    if not hasattr(torchaudio, "info"):
        torchaudio.info = _soundfile_info
    if not hasattr(torchaudio, "list_audio_backends"):
        torchaudio.list_audio_backends = lambda: ["soundfile"]
