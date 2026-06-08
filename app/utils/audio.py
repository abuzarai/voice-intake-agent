"""Audio processing utilities."""

import base64
import numpy as np
from typing import Dict, Any


def base64_to_pcm(base64_audio: str) -> bytes:
    """Convert base64-encoded audio to PCM bytes.
    
    Args:
        base64_audio: Base64-encoded audio string
        
    Returns:
        PCM audio bytes (16-bit, 16kHz, mono)
    """
    try:
        return base64.b64decode(base64_audio)
    except Exception as e:
        raise ValueError(f"Failed to decode base64 audio: {str(e)}")


def pcm_to_base64(pcm_audio: bytes) -> str:
    """Convert PCM bytes to base64 string.
    
    Args:
        pcm_audio: PCM audio bytes
        
    Returns:
        Base64-encoded audio string
    """
    return base64.b64encode(pcm_audio).decode('utf-8')


def validate_audio_format(sample_rate: int = 16000, channels: int = 1, 
                          sample_width: int = 2) -> bool:
    """Validate audio format matches requirements.
    
    Args:
        sample_rate: Audio sample rate in Hz
        channels: Number of audio channels
        sample_width: Sample width in bytes
        
    Returns:
        True if format is valid
    """
    return (
        sample_rate == 16000 and
        channels == 1 and
        sample_width == 2
    )


def calculate_audio_duration(audio_bytes: bytes, sample_rate: int = 16000, 
                            sample_width: int = 2) -> float:
    """Calculate audio duration in seconds.
    
    Args:
        audio_bytes: Raw PCM audio bytes
        sample_rate: Sample rate in Hz
        sample_width: Sample width in bytes
        
    Returns:
        Duration in seconds
    """
    num_samples = len(audio_bytes) // sample_width
    return num_samples / sample_rate


def check_audio_quality(pcm_audio: bytes, min_amplitude: float = 100.0) -> Dict[str, Any]:
    """Perform basic audio quality checks.
    
    Args:
        pcm_audio: PCM audio bytes (16-bit LE). If input is compressed (e.g., webm/opus),
            we can't measure amplitude reliably, so we return an 'unknown' quality.
        min_amplitude: Minimum acceptable amplitude
        
    Returns:
        Dictionary with quality metrics and warnings
    """
    # If this doesn't look like raw 16-bit PCM, skip amplitude checks
    if len(pcm_audio) % 2 != 0:
        return {
            "max_amplitude": None,
            "mean_amplitude": None,
            "is_silent": False,
            "warnings": ["Audio format not PCM; quality unknown"],
            "quality": "unknown"
        }

    # Convert bytes to numpy array
    audio_array = np.frombuffer(pcm_audio, dtype=np.int16)
    
    # Calculate metrics
    max_amplitude = np.max(np.abs(audio_array))
    mean_amplitude = np.mean(np.abs(audio_array))
    
    warnings = []
    if max_amplitude < min_amplitude:
        warnings.append("Audio level too low - microphone may be muted or too far")
    if mean_amplitude < 10:
        warnings.append("Very low audio signal - check microphone connection")
    
    return {
        "max_amplitude": float(max_amplitude),
        "mean_amplitude": float(mean_amplitude),
        "is_silent": max_amplitude < 10,
        "warnings": warnings,
        "quality": "good" if not warnings else "poor"
    }
