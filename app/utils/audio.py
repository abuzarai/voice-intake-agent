"""Audio processing utilities."""

import base64


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


