# Configuration

Settings are defined in [app/settings.py](../app/settings.py). Names are
case-sensitive. The service reads the process environment and does not load
`.env` files or discover Google credentials automatically.

[.env.example](../.env.example) contains non-secret defaults. Inject credentials
through your application's runtime configuration or secret management. Keep
provider keys, bootstrap secrets and session capabilities out of browser code,
URLs, logs and source control.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIVE_ENABLED` | `false` | Enables the Live API. |
| `LIVE_BOOTSTRAP_SECRET` | Unset | Server-only admission secret, 32–256 characters. |
| `LIVE_API_KEY` | Unset | Dedicated Gemini API key, 1–256 characters. |
| `LIVE_MODEL` | Unset | Explicit model identifier; no default or automatic selection. |
| `LIVE_FREE_TIER_ATTESTED` | `false` | Required attestation before provider operations; does not verify eligibility or prevent charges. |
| `LIVE_STUN_URL` | Unset | Optional explicit `stun:host[:port]` for server-side mapped-candidate discovery. No credentials or automatic server selection. |
| `LIVE_CAPABILITY_TTL_SECONDS` | `300` | Absolute session lifetime, 30–300 seconds. |
| `LIVE_MAX_SESSIONS` | `16` | Retained session limit, 1–64, including stopped sessions until expiry. |
| `LIVE_ADMISSIONS_PER_MINUTE` | `6` | Successful admissions per fixed minute, 1–60, per process. |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated exact allowlist for supplied Origin headers. |

Blank optional key, secret and model values normalize to unset. Calls require a
nonblank key and a model matching `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. The supported
runtime is Python 3.12, Pipecat 1.12.0 and google-genai 2.19.0, pinned in the
[project dependencies](../pyproject.toml) and [lockfile](../uv.lock).

Admission requires Live enabled, a bootstrap secret and
`processing_consent:true`; admission itself does not contact Gemini. Signaling
and proposal operations additionally require the model, API key and attestation.
Transcript reads require the existing unexpired session capability, not provider
configuration.

`CORS_ORIGINS` validates supplied Origin headers. It does not configure CORS
response middleware or authenticate users. Requests without Origin are allowed
for trusted server callers. The host application must enforce user and case
permissions; see the [API contract](contracts.md).

## Running locally

Install dependencies and use the command in the [README](../README.md). To load
an environment file explicitly, uv can supply it to the process:

```sh
uv run --no-sync --env-file .env python -m uvicorn app.main:create_app --factory \
  --host 127.0.0.1 --port 8000 --workers 1 \
  --no-access-log --log-level critical --no-proxy-headers \
  --timeout-graceful-shutdown 45
```

Only enable Live after setting the required credentials, model and account
attestation. Attestation is an application guard, not a provider-side billing or
privacy control. Check the configured model's current terms and eligibility
before processing sensitive information.

## Container

The root [Dockerfile](../Dockerfile) installs the frozen runtime dependencies,
runs as UID/GID 10001 and starts one Uvicorn worker on port 8000. It does not copy
development tests or documentation into the application image. Build and run
configuration for the Insafdaar stack belongs to the webapp repository.

Supply settings at runtime. Do not put credentials in build arguments or images.
The [.dockerignore](../.dockerignore) excludes environment files, recordings,
private keys and local workspace material from the build context.

## Capacity and lifecycle

One process owns one active call, including startup and cleanup. The retained
session limit is not concurrent-call capacity. Multiple workers or replicas do
not share capabilities, admission limits, pending proposals or transcript journals.

Calls are capped at five minutes within the original session expiry. Prepare
has at most 30 seconds within that deadline. Stop gates media immediately;
cleanup remains owned until it finishes. State/Stop wait at most three seconds
for cleanup and return 503 if it remains pending. Signaling failure paths have a
three-second cleanup wait but retain their original sanitized 502 response.
These are waiting bounds, not guarantees of SDK connection closure.

Runtime shutdown blocks admission and waits within one total 20-second budget.
Uvicorn's container grace period is 45 seconds; an outer supervisor must allow
for shutdown and enforce termination if cleanup stalls. An aborted cleanup owner
keeps the call slot reserved until process replacement. Restarting must not
silently start another provider session.

Registry expiry denies access to expired capabilities. Expired records are
removed on subsequent registry access/admission; this is not a timed physical
RAM-erasure guarantee. In-memory transcripts and unsaved proposals do not survive
process exit.

## Health and networking

`GET /health` returns:

```json
{"status":"alive","scope":"local-network-only"}
```

It checks local liveness only. It does not test provider credentials, available
call capacity, SDK cleanup or media reachability.

The WebRTC adapter uses an empty ICE-server list unless `LIVE_STUN_URL` is
explicitly configured. STUN discovers a mapped address; it is not a media relay
and does not open firewall or container ports. The service has no TURN
configuration or bundled reverse proxy. Container/network topology, HTTPS,
firewall rules and any media relay belong to the application deployment. HTTP
health and signaling success alone do not establish remote audio connectivity.
