"""List what the caller corpus needs, and import a recording into place."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT.parent / "tests" / "live_provider" / "fixtures"
AUDIO = ROOT / "audio"

CLOSING = {
    "urdu": "نہیں، بس اتنا ہی ہے۔",
    "english": "No, that is all.",
    "mixed": "Nahi, bas itna hi hai.",
}

TURNS = ("01_answer.wav", "02_closing.wav")

TRIM = (
    "silenceremove=start_periods=1:start_threshold=-45dB"
    ":stop_periods=-1:stop_threshold=-45dB:stop_silence=0.35"
)


def cases():
    """Every fixture that has a scenario, in name order."""
    return sorted(FIXTURES.glob("*.json"))


def lines_for(data):
    return (data["turns"][-1]["content"], CLOSING[data["language"]])


def describe(path):
    """A one-line verdict on a recording, or the reason it cannot be read."""
    try:
        info = sf.info(path)
    except Exception as exc:
        return f"unreadable: {exc}"
    seconds = info.frames / info.samplerate
    problems = []
    if info.channels != 1:
        problems.append(f"{info.channels} channels")
    if info.samplerate != 16000:
        problems.append(f"{info.samplerate} Hz")
    if not 0.5 <= seconds <= 20:
        problems.append(f"{seconds:.1f} s")
    return "ok" if not problems else "fix: " + ", ".join(problems)


def show(only=None):
    import json

    missing = 0
    for path in cases():
        if only and only != path.stem:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"\n{path.stem}  ({data['case']}, {data['language']})")
        for turn, line in zip(TURNS, lines_for(data), strict=True):
            target = AUDIO / path.stem / turn
            state = describe(target) if target.exists() else "missing"
            if state == "missing":
                missing += 1
            print(f"  {state:<12} {target.relative_to(ROOT.parent)}")
            print(f"  {'':<12} say: {line}")
    print(f"\n{missing} recording(s) still missing")
    return 0 if missing == 0 else 1


def import_recording(case, turn, source):
    """Convert one recording to 16 kHz mono WAV at its expected path."""
    name = turn if turn.endswith(".wav") else f"{turn}.wav"
    if name not in TURNS:
        raise SystemExit(f"turn must be one of {', '.join(TURNS)}, got {turn!r}")
    target = AUDIO / case / name
    if not Path(source).exists():
        raise SystemExit(f"no such file: {source}")
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required to convert a recording")
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-y",
        "-i",
        source,
        "-af",
        TRIM,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {result.stderr.strip()}")
    print(f"{target.relative_to(ROOT.parent)}: {describe(target)}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", nargs="?", default=None)
    parser.add_argument(
        "--import", dest="import_", nargs=3, metavar=("CASE", "TURN", "FILE")
    )
    args = parser.parse_args(argv)
    if args.import_:
        return import_recording(*args.import_)
    return show(args.case)


if __name__ == "__main__":
    sys.exit(main())
