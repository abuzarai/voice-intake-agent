"""Port the recorded provider fixtures into eval scenarios and gold files.

The fixtures encode both sides of a transcript. A scenario scripts only the
caller, so the recorded assistant turns move into ``context:`` and the last
caller answer becomes the first turn. Two turns follow: the caller closes, then
the bot is observed while it drafts.

Turns carry audio paths, because a speech-to-speech service is driven by
speech. Each case needs two recordings under ``evals/audio/<case>/``.
"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT.parent / "tests" / "live_provider" / "fixtures"
SCENARIOS = ROOT / "scenarios" / "replay"
BEHAVIOUR = ROOT / "scenarios" / "behaviour"
GOLD = ROOT / "gold"

CLOSING = {
    "urdu": "نہیں، بس اتنا ہی ہے۔",
    "english": "No, that is all.",
    "mixed": "Nahi, bas itna hi hai.",
}

# A fixture ends mid-interview, so the agent has not asked its closing
# question yet and the caller's "that is all" would read as a non-answer.
# The recorded transcript is followed by that question, and the caller's last
# answer becomes a late addition to it.

# The bot buffers caller audio until the provider session is live, so this is
# only a head start, not a race the caller can lose.
HEAD_START_MS = 1500

# Behaviour cases keep the closing question seeded, because closure is the thing
# being measured: the caller's "that's all" has to answer something.
CLOSING_QUESTION = {
    "urdu": "شکریہ۔ کیا آپ کچھ اور شامل کرنا چاہیں گے؟",
    "english": "Thank you. Is there anything else you would like to add?",
    "mixed": "Shukriya. Kya aap kuch aur add karna chahenge?",
}

CLOSED = [{"event": "function_call", "calls": [{"name": "interview_finished"}]}]

DRAFT = [{"event": "function_call", "calls": [{"name": "propose_draft"}]}]


def convert(name, data):
    turns = data["turns"]
    language = data["language"]
    scenario = {
        "name": name,
        "tags": [data["case"], language],
        "scenarios": [
            {
                "name": "replay",
                "user": {"modality": "audio", "speech": {"sample_rate": 16000}},
                "context": [dict(turn) for turn in turns[:-1]],
                "turns": [
                    {
                        "user": turns[-1]["content"],
                        "audio": f"../../audio/{name}/01_answer.wav",
                        # The bot buffers caller audio until the provider
                        # session is live, so this is only a short head start.
                        "send_after": {"delay_ms": HEAD_START_MS},
                    },
                    {
                        "user": CLOSING[language],
                        "audio": f"../../audio/{name}/02_closing.wav",
                        # The caller keeps going, as a person does; the
                        # transport queues the audio behind the answer.
                    },
                    {"expect": DRAFT},
                ],
            }
        ],
    }
    return scenario, data["expect"]


def behaviour_scenario(name, data):
    """The same exchange, but asserting that the agent closes it.

    Extraction cases never assert closure: a flake there would block every
    draft. This case measures closure on its own.
    """
    turns = data["turns"]
    language = data["language"]
    return {
        "name": name,
        "tags": [data["case"], language, "behaviour"],
        "scenarios": [
            {
                "name": "closure",
                "user": {"modality": "audio", "speech": {"sample_rate": 16000}},
                "context": [dict(turn) for turn in turns[:-1]]
                + [{"role": "assistant", "content": CLOSING_QUESTION[language]}],
                "turns": [
                    {
                        "user": turns[-1]["content"],
                        "audio": f"../../audio/{name}/01_answer.wav",
                        "send_after": {"delay_ms": HEAD_START_MS},
                    },
                    {
                        "user": CLOSING[language],
                        "audio": f"../../audio/{name}/02_closing.wav",
                        "expect": CLOSED,
                    },
                ],
            }
        ],
    }


def main():
    SCENARIOS.mkdir(parents=True, exist_ok=True)
    BEHAVIOUR.mkdir(parents=True, exist_ok=True)
    GOLD.mkdir(parents=True, exist_ok=True)
    written = 0
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        scenario, expect = convert(path.stem, data)
        (SCENARIOS / f"{path.stem}.yaml").write_text(
            yaml.safe_dump(scenario, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        (BEHAVIOUR / f"{path.stem}.yaml").write_text(
            yaml.safe_dump(
                behaviour_scenario(path.stem, data),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (GOLD / f"{path.stem}.json").write_text(
            json.dumps(expect, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written += 1
    print(f"ported {written} fixtures")


if __name__ == "__main__":
    main()
