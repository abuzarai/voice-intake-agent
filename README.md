# Insafdaar voice intake

Voice interview microservice for Insafdaar, built with Gemini Live and Pipecat.
It supports Urdu, English and mixed-language conversations over WebRTC, with
interruptions, live transcripts and structured intake proposals.

The service keeps session data in memory. The host application authenticates
users, associates interviews with cases, and owns transcript storage, review,
confirmation and submission.

## Interview flow

1. The application server authorizes the interview and obtains a short-lived
   session capability. Provider credentials stay on the server.
2. The browser connects through WebRTC. The service opens one Gemini Live session
   and requests a brief welcome and first question before waiting for user speech.
   Transcript events are published as conversation turns are captured.
3. Prepare stops interview input and requests a structured proposal on the same
   provider session. The application reads and saves the proposal for review.
4. Stop closes the call and discards its proposal. Saved case data remains the
   host application's responsibility.

Proposals are unconfirmed model output. Transcripts can be incomplete, especially
after interruption or disconnect. Neither is a substitute for client review.

## Local setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --frozen --group dev --python 3.12
```

Start the service with Live disabled by default:

```sh
uv run --no-sync python -m uvicorn app.main:create_app --factory \
  --host 127.0.0.1 --port 8000 --workers 1 \
  --no-access-log --log-level critical --no-proxy-headers \
  --timeout-graceful-shutdown 45
```

`GET /health` returns local liveness without contacting a provider. Enabling
interviews requires explicit model, key, bootstrap secret and runtime settings;
see [configuration](docs/configuration.md). The service does not load `.env`
files automatically.

## Development

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
```

Tests block network access and use fake media/provider seams. Each test belongs
to one registered layer; the default command runs all three:

```sh
uv run --no-sync pytest -q -m unit
uv run --no-sync pytest -q -m integration
uv run --no-sync pytest -q -m sdk
```

- **Unit:** isolated domain, validation, registry and controller behavior.
- **Integration:** in-process application components, ASGI routing and import
  isolation, with fake media/provider boundaries.
- **SDK:** component/compatibility checks against the installed pinned SDKs,
  without real sockets or provider calls. Requires Python 3.12 and the locked
  runtime dependencies; missing dependencies are not silently skipped.

These checks do not establish real browser audio, provider response quality or
webapp persistence. Those require separate acceptance through the webapp.

## Runtime constraints

- One active interview per process, including startup and cleanup. Use one worker;
  session capabilities and call ownership are not shared between replicas.
- Sessions last at most five minutes. Restarting loses unsaved in-memory data.
- No automatic provider retry, reconnect or replacement session.
- The WebRTC adapter currently uses no ICE servers. Remote connectivity
  requires suitable networking; HTTPS alone does not relay WebRTC media.
- Audio and conversation data are processed by Google Gemini. The host application
  must provide the appropriate notice and consent flow. Provider terms,
  eligibility and charges depend on the configured account and model.

The root Dockerfile builds this service. Application deployment and orchestration
are maintained with the webapp.

## Documentation

- [Configuration and container runtime](docs/configuration.md)
- [API contract](docs/contracts.md)
- [Transcript events and batch reads](docs/transcripts.md)
- [Architecture](docs/architecture.md)
