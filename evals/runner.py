"""Eval runner: drive the eval bot with scenarios, score drafts, gate a baseline."""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pipecat.evals.scenario import EvalScenarioFile
from pipecat.evals.session import EvalSession, EvalSessionParams

from app.adapters.pipecat_live.gemini import PROMPT_SHA256
from evals.corpus import corpus_hash
from evals.env import credentials, setting
from evals.gate import gate, pass_threshold
from evals.scoring import aggregate, parity, proposal_args, score_case
from evals.silent_tts import SilentUserTTS

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "scenarios" / "replay"
BEHAVIOUR = ROOT / "scenarios" / "behaviour"
GOLD = ROOT / "gold"
MANIFEST = ROOT / "manifest.yaml"
BASELINE = ROOT / "baseline.json"

DEFAULT_TIMEOUT_MS = 90000


PROPOSAL_WAIT_S = 20.0
BOOT_TIMEOUT_S = 20.0


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_port(port, process, timeout=BOOT_TIMEOUT_S):
    """Wait for the bot to listen, and name a bot that died instead of waiting."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"eval bot exited with {process.returncode} before listening"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"eval bot never listened on {port}")


CREDENTIAL_NAMES = (
    ("LIVE_EVAL_GEMINI_KEY", "LIVE_API_KEY"),
    ("LIVE_EVAL_MODEL", "LIVE_MODEL"),
)


def credentials_present(env_file=None):
    """Whether a non-blank key and model are available."""
    return credentials(env_file) is not None


async def run_case(name, env_file, mode="extraction"):
    source = SCENARIOS if mode == "extraction" else BEHAVIOUR
    scenario = EvalScenarioFile.load(source / f"{name}.yaml").scenarios[0]
    port = free_port()
    proposal_path = Path(tempfile.mkdtemp()) / f"{name}.proposal.json"
    command = [
        sys.executable,
        "-m",
        "evals.bot",
        "--port",
        str(port),
        "--proposal-out",
        str(proposal_path),
        "--mode",
        mode,
    ]
    if env_file:
        command += ["--env-file", env_file]
    process = subprocess.Popen(command, cwd=ROOT.parent, env=os.environ.copy())
    try:
        wait_for_port(port, process)
        session = EvalSession.from_scenario(
            scenario,
            f"ws://127.0.0.1:{port}",
            user_tts=SilentUserTTS(sample_rate=16000),
            params=EvalSessionParams(
                connect_timeout_s=10.0,
                default_timeout_ms=DEFAULT_TIMEOUT_MS,
            ),
        )
        result = await session.run()
        return result, await wait_for_proposal(proposal_path)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


async def wait_for_proposal(path, timeout=PROPOSAL_WAIT_S):
    """Read the draft the bot writes after the tool call settles.

    The harness matches a function-call expectation on the call starting, so
    the bot is still completing the callback and the context roundtrip when
    ``session.run()`` returns. Reading immediately loses the draft.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        await asyncio.sleep(0.1)
    return None


def missing_recordings(scenario):
    """Audio files a scenario names but that do not exist yet."""
    return [
        turn.audio
        for turn in scenario.turns
        if turn.audio is not None and not Path(turn.audio).exists()
    ]


def score_behaviour(name, result):
    """A behaviour case is judged by the harness alone: closure happened or not."""
    return {
        "id": name,
        "passed": result.passed,
        "checks": {"closure": result.passed},
        "harness_failures": [str(failure) for failure in result.failures],
        "duration_ms": result.duration_ms,
        "events": result.events_seen,
    }


def score(name, result, proposal=None):
    expect = json.loads((GOLD / f"{name}.json").read_text(encoding="utf-8"))
    scored = score_case(result.events_seen, expect, proposal_args(proposal))
    scored["id"] = name
    scored["expected_present"] = len(expect.get("facts_present", []))
    scored["expected_absent"] = len(expect.get("facts_absent", []))
    scored["duration_ms"] = result.duration_ms
    scored["harness_passed"] = result.passed
    scored["harness_failures"] = [str(failure) for failure in result.failures]
    scored["checks"]["harness"] = result.passed
    scored["passed"] = scored["passed"] and result.passed
    scored["events"] = result.events_seen
    return scored


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", default="smoke", choices=["smoke", "full", "behaviour"]
    )
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--report", default=str(ROOT / "report.json"))
    parser.add_argument("--accept-baseline", action="store_true")
    parser.add_argument("--case", action="append", default=None)
    args = parser.parse_args(argv)

    if not credentials_present(args.env_file):
        print("no provider credentials; set LIVE_EVAL_GEMINI_KEY and LIVE_EVAL_MODEL")
        return 0

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    names = args.case or manifest[args.split]
    mode = "behaviour" if args.split == "behaviour" else "extraction"
    repeats = args.repeats or manifest.get("repeats", 1)

    cases = []
    for name in names:
        source = SCENARIOS if mode == "extraction" else BEHAVIOUR
        scenario = EvalScenarioFile.load(source / f"{name}.yaml").scenarios[0]
        missing = missing_recordings(scenario)
        if missing:
            print(f"{name}: skipped, needs recording {missing[0]}")
            continue
        attempts = []
        for _ in range(repeats):
            result, proposal = asyncio.run(run_case(name, args.env_file, mode))
            if mode == "behaviour":
                attempts.append(score_behaviour(name, result))
            else:
                attempts.append(score(name, result, proposal))
        best = max(attempts, key=lambda case: case.get("passed", False))
        best["attempts"] = len(attempts)
        best["pass_rate"] = round(
            sum(1 for attempt in attempts if attempt.get("passed")) / len(attempts), 3
        )
        # Snapshot each attempt before the case verdict replaces the best one.
        detail = [
            {
                "passed": attempt["passed"],
                "checks": attempt["checks"],
                "harness_failures": attempt.get("harness_failures", []),
                "duration_ms": attempt.get("duration_ms"),
                "tail": [
                    f"{event.get('type')}: {str(event.get('text', ''))[:60]}"
                    for event in attempt.get("events", [])[-6:]
                ],
            }
            for attempt in attempts
        ]
        best["passed"] = best["pass_rate"] >= pass_threshold(len(attempts))
        best["attempts_detail"] = detail
        cases.append(best)
        print(
            f"{name}: {'pass' if best.get('passed') else 'FAIL'} "
            f"({best['pass_rate']:.0%} of {len(attempts)})"
        )

    if not cases:
        print("no case ran; the caller recordings are missing")
        return 1

    if mode == "behaviour":
        rate = round(sum(1 for case in cases if case["passed"]) / len(cases), 4)
        report = {
            "run": {
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "split": args.split,
                "repeats": repeats,
                "prompt_sha256": PROMPT_SHA256,
                "model": setting("LIVE_EVAL_MODEL", "LIVE_MODEL", args.env_file),
            },
            "behaviour": cases,
            "aggregate": {"cases": len(cases), "pass_rate": rate},
        }
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report["aggregate"], indent=2))
        # Reported, never gated: closure is the agent's etiquette, not a
        # correctness gate on extraction.
        return 0

    divergences = parity(cases)
    for divergence in divergences:
        print(
            f"parity: {'/'.join(divergence['triplet'])} {divergence['check']}: {divergence['detail']}"
        )

    aggregate_result = aggregate(cases)
    baseline = None
    baseline_prompt = None
    baseline_corpus = None
    if BASELINE.exists():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        baseline_prompt = baseline["run"].get("prompt_sha256")
        baseline_corpus = baseline["run"].get("corpus_sha256")
        # A prompt edit is compared, not skipped: a change that costs accuracy
        # is exactly what the gate exists to catch. Blessing a new prompt is an
        # explicit --accept-baseline, not a side effect of editing one.
        if baseline_prompt != PROMPT_SHA256:
            print(f"baseline prompt {baseline_prompt} differs from {PROMPT_SHA256}")
        current_corpus = corpus_hash()
        if baseline_corpus != current_corpus:
            print(f"baseline corpus {baseline_corpus} differs from {current_corpus}")

    report = {
        "run": {
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "split": args.split,
            "repeats": repeats,
            "prompt_sha256": PROMPT_SHA256,
            "corpus_sha256": corpus_hash(),
            "baseline_prompt_sha256": baseline_prompt,
            "baseline_corpus_sha256": baseline_corpus,
            "model": setting("LIVE_EVAL_MODEL", "LIVE_MODEL", args.env_file),
        },
        "cases": cases,
        "parity": divergences,
        "aggregate": aggregate_result,
    }
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(aggregate_result, indent=2))

    if args.accept_baseline:
        if args.case and set(args.case) != set(manifest[args.split]):
            print("refusing to accept a baseline from a subset of the split")
            return 1
        # A case passes on a majority, so one flaky attempt does not block
        # acceptance. The recorded pass_rate keeps the flakiness visible.
        failed = [case["id"] for case in cases if not case.get("passed")]
        if failed:
            print(f"refusing to accept a baseline: {', '.join(failed)} did not pass")
            return 1
        if divergences:
            print("refusing to accept a baseline: the language variants diverge")
            return 1
        flaky = [case["id"] for case in cases if case.get("pass_rate", 0) < 1.0]
        if flaky:
            print(f"accepting with flaky attempts: {', '.join(flaky)}")
        BASELINE.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline accepted at {BASELINE}")
        return 0

    reasons = gate(aggregate_result, cases, baseline, divergences)
    for reason in reasons:
        print(f"gate: {reason}")
    return 1 if reasons else 0


if __name__ == "__main__":
    raise SystemExit(main())
