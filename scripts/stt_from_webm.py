import sys
from pathlib import Path

from app.services.stt_service import SpeechToTextService
from app.utils.audio import check_audio_quality

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/stt_from_webm.py path/to/audio.webm")
        sys.exit(1)

    webm_path = Path(sys.argv[1])
    if not webm_path.exists():
        print(f"File not found: {webm_path}")
        sys.exit(1)

    audio_bytes = webm_path.read_bytes()
    print(f"Loaded webm bytes: {len(audio_bytes)}")

    stt = SpeechToTextService()

    # Convert only
    pcm = stt._convert_audio_to_linear16(audio_bytes)
    if not pcm:
        print("❌ Conversion failed: pcm is empty/None")
        sys.exit(2)

    print(f"Converted to PCM bytes: {len(pcm)}")
    q = check_audio_quality(pcm)
    print("Quality:", q)

    # Recognize
    result = stt.recognize_audio(audio_bytes)
    print("STT result:", result)

if __name__ == "__main__":
    main()
