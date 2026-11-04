# Evaluations

The eval layer drives the real agent with recorded callers and scores the draft
it produces. Thirteen cases, three attempts each, one real Gemini Live session
per attempt. The offline tests never touch the network; this is the only part of
the suite that does.

## Quick start

```sh
uv run --no-sync python -m evals.recording_sheet           # what is missing, and the line to read
uv run --no-sync python -m evals.runner --split smoke      # two cases, a few minutes
uv run --no-sync python -m evals.runner --split full       # all thirteen
uv run --no-sync python -m evals.runner --split behaviour  # closure only
```

Provider credentials come from the environment or from `--env-file .env`.
`evals/report.json` is the artifact. A case passes on a strict majority of its
attempts, because the model varies between runs and one bad attempt is not a
regression.

## What a case is

A scenario plus gold. The scenario scripts the caller only; the agent speaks for
itself. Recorded assistant turns from the original transcript are seeded through
`context:`, so the model continues a conversation that has already happened.

Each case needs two recordings under `evals/audio/<case>/`:

| File | Content |
|---|---|
| `01_answer.wav` | the caller's last answer from the transcript |
| `02_closing.wav` | the caller closing the interview |

Turns carry audio because a speech-to-speech agent is driven by speech: a text
turn does not reach it. The eval bot holds incoming audio until the provider
session is live, then delivers it in order, so a caller who starts talking early
is heard rather than dropped.

## Who requests the draft

Production's Prepare is a host decision: the application calls an endpoint and
the model plays no part. The eval bot does the same, waiting for the caller to
stop and then asking for the draft itself. `interview_finished` is refused
during an extraction case, so the agent's interviewing etiquette cannot decide
whether extraction gets measured.

If the model never reports hearing the caller, a fallback fires after 15 seconds
and drafts from the seeded transcript rather than producing nothing.

## Scoring

The draft is re-validated with the production `validate_arguments`, then each
fact is compared against gold. Amounts are parsed with the same helper the
provider suite uses, and prose nulls are counted rather than failed. A case that
produced no draft reports no precision and no recall; it does not report a
perfect score.

Gold can leave a field open. The `property` cases are inheritance disputes, a
father's estate left undistributed and claimed by his heirs, and the domain list
has no inheritance category, so both `property_law` and `family_law` count as
correct.

## Comparing the languages

The Urdu, English and mixed versions of a case are compared with each other, not
only with their own gold. Legal domain must agree, urgency may sit one band
apart, the same core facts must be populated, and the first parsed amount must
match. A per-case score cannot see a language-specific comprehension gap,
because every variant can pass its own gold while disagreeing with its siblings.

Urgency, previous actions and documents are excluded: a model may record them
in one telling and leave them null in another without having misunderstood
anything. A domain that gold leaves open is not compared either.

## Baselines

`evals/baseline.json` is committed, because the gate is meaningless without it.

Every report records two fingerprints: `prompt_sha256` over the three
instruction constants the model is given, and `corpus_sha256` over the
scenarios, the gold, the recordings and the manifest. A run prints a notice when
either differs from the baseline. Neither notice skips the comparison: a prompt
edit that costs accuracy is exactly what the gate exists to catch.

```sh
uv run --no-sync python -m evals.runner --accept-baseline
```

Acceptance refuses a run with a failing case or a language divergence, and
accepts a flaky one, recording its pass rate. The gate fails a run for a case
that did not pass, a regression from pass to fail, a `field_accuracy` drop over
0.02, any `fact_precision` drop, or a language divergence.

## Behaviour cases

`evals/scenarios/behaviour/` holds the same exchange with one difference: it
asserts that the agent calls `interview_finished` after the caller closes. The
closing question stays seeded, because the caller's "that's all" has to answer
something. The bot accepts the tool call in this mode and does not request a
draft.

These cases are reported, never gated: closure is the agent's etiquette, and a
flake there must not block an extraction result.

Closure is the only behaviour measured, because it is a tool call rather than a
reading of the agent's words. Whether it asks one question at a time, mirrors
the caller's language, or handles a refusal well is not measured. Matching those
with patterns over generated text would measure whether the expected phrasing
appeared, and it would rot as soon as the prompt changed.

## Adding or changing a case

1. Add or edit a fixture in `tests/live_provider/fixtures/`: `turns` and
   `expect`.
2. Record the two lines and import them:
   ```sh
   uv run --no-sync python -m evals.recording_sheet <case>
   uv run --no-sync python -m evals.recording_sheet --import <case> 01_answer ~/Downloads/take.m4a
   ```
   Import converts to 16 kHz mono and trims the silence. The sheet reports a
   file that is the wrong shape before it costs a provider call.
3. Regenerate the corpus, which also rebuilds the gold and the behaviour
   scenario:
   ```sh
   uv run --no-sync python -m evals.port_fixtures
   ```
   `tests/test_eval_corpus.py` fails if the committed corpus drifts from the
   fixtures, so a skipped regeneration is caught offline.
4. Run the case, then accept a baseline once the whole split is clean.

## Reading a failure

`evals/report.json` keeps every attempt, not just the best one. Each attempt
carries its `checks`, its `harness_failures`, and the last few events, so a
flaky case can be read instead of guessed at.

The usual causes:

- **`propose_draft` never seen** — the bot never heard a caller turn and the
  fallback did not fire, or the model did not answer the draft request.
- **`schema` failed** — the payload was rejected by the production validator.
- **`dropped` / `invented`** — a fact gold expected is missing, or a fact gold
  ruled out was filled in.
- **`legal_domain` / `populated` divergence** — the language variants disagreed.

## Continuous integration

`.github/workflows/evals.yml` runs the full split nightly, and on demand through
`workflow_dispatch`. It does not run on pull requests: a two-case sample is a
weak signal, the model is variable enough to fail it spuriously, and most
changes cannot affect extraction.

The workflow needs `LIVE_EVAL_GEMINI_KEY`, `LIVE_EVAL_MODEL` and, for the
recordings, `EVAL_AUDIO_URL`. A missing or blank provider key skips the run
instead of failing it. Missing recordings fail it: the credentials say the
environment is ready, so running nothing is a setup error.

The recordings are not in the repository. `evals/audio/` is ignored, so a fresh
clone cannot run the evals until they are restored.

## Data handling

The caller corpus is synthetic. The lines come from fictional fixtures, and
`recording_sheet.py` prints them for a person to read. Real caller audio and
real intake transcripts must never enter `evals/audio/` or the artifact the
recording URL serves. Audio and conversation text are processed by the provider
configured for the eval key, which is separate from the production key.

## Limits

The recordings are one voice in a quiet room reading a script. Accents,
background noise, two people talking at once, and a caller who changes their
mind are all outside what this exercises.

The host application is outside it too: saving, review, confirmation and
submission are application transactions, tested there.

And the provider can change under the model. The baseline detects a shift in
accuracy, but only when the evals are run.
