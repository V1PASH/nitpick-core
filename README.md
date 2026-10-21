# nitpick-core

**The agent runtime for [nitpick](https://github.com/V1PASH/nitpick).**

Loads agent definitions from a [minidb](https://github.com/V1PASH/minidb)
registry, dispatches them through [Prefect](https://docs.prefect.io), and
serves the voice rooms so people can talk to them.

```
   Django                    nitpick-core                    Prefect
   voice_service
      │                          │                             │
      │  POST /api/tasks         │                             │
      ├─────────────────────────▶│                             │
      │                          │  run the flow               │
      │                          ├────────────────────────────▶│
      │                          │                             │
      │                          │   dispatch ──▶ agent runtime│
      │  GET /api/sessions/{id}  │                             │
      │◀─────────────────────────│                             │
      │                          │                             │
      │                          │  WS /ws                     │
      │                          │◀──── browser / SDK ────────▶│
```

Prefect **dispatches**; the runtime **processes**. A voice session is
long-running and interactive, which does not fit Prefect's bounded-task
model — so the flow is short (it hands the request over and returns a
ticket) and the session itself is owned by the runtime, which can hold
many at once.

---

## Quick start

```bash
git clone https://github.com/V1PASH/nitpick-core.git
cd nitpick-core
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Point it at the registry the Django service writes to.
export MINIDB_PATH=/path/to/voice_service/voice.db
export OPENAI_API_KEY=sk-...

nitpick-core serve --port 8100
```

```bash
# What is in the registry?
nitpick-core agents

# Queue an agent by hand.
nitpick-core dispatch acme-support --room demo
```

Open <http://localhost:8100> and talk to it.

---

## The registry

The agent definitions live in the same minidb database the Django service
writes to — two collections:

`agent`
: the agents. A name, a prompt, and the three model slugs it is wired
  together from.

`models`
: the registry of models an agent can be built from. Each has a `kind`
  (`llm`, `tts` or `stt`) and a `handle` naming the provider.

Every document carries a `handle` — a stable identifier. nitpick-core
fetches an agent **by its handle**:

```python
from nitpick_core import resolve_agent

resolved = resolve_agent("acme-support")
resolved["agent"]["prompt"]     # the system prompt
resolved["llm"]["slug"]         # 'gpt-4o'
resolved["stt"]["slug"]         # 'whisper'
resolved["tts"]["slug"]         # 'nova'
```

### How a document becomes a running agent

The registry stores *what* an agent is, in a provider-neutral way. The
builder translates that into the provider-specific strings nitpick's
`Agent` facade expects, using the model's `handle`:

| Registry | nitpick | Derived from |
|---|---|---|
| `llm` model | `llm="openai:<model>"` | the provider handle, and the model's slug or `config["model"]` |
| `stt` model | `stt="openai" \| "deepgram" \| "sarvam"` | the provider handle |
| `tts` model | `tts="openai:<voice>"` | the provider handle, and `config["voice"]` or the first of `config["voices"]` |

A model whose provider nitpick does not know about is still usable if it
declares an OpenAI-compatible `base_url` — that is how a self-hosted model
or a gateway can be reached.

The agent's own `config` may carry pipeline settings:

```json
{
  "language": "en",
  "vad": "silero",
  "allow_interruptions": true
}
```

---

## The HTTP API

### `POST /tasks` — dispatch a task

```json
{ "handle": "acme-support", "room_id": "demo", "metadata": {"campaign": "spring"} }
```

```json
{
  "id": "ses-3d248ab4429c",
  "handle": "acme-support",
  "room_id": "demo",
  "status": "queued",
  "flow_run_id": "28c24f9f-7434-4423-aae2-0a7fcace9aa7",
  "agent": { "stt": "openai", "llm": "openai:gpt-4o", "tts": "openai:nova", ... }
}
```

The agent is resolved **before** the task is queued, so an unknown handle
comes back as a `404` straight away rather than a session that quietly
fails later.

| Status | Meaning |
|---|---|
| `202` | Queued |
| `400` | The task could not be dispatched |
| `404` | No such agent or model |
| `422` | The agent cannot be built (an unsupported provider, for example) |
| `503` | The registry could not be read |

### `GET /tasks/{id}` and `GET /sessions/{id}` — the session

```json
{
  "id": "ses-3d248ab4429c",
  "status": "running",
  "started_at": 1763000000.0,
  "duration_seconds": 42.5,
  "flow_run_id": "28c24f9f-..."
}
```

Statuses: `queued` → `starting` → `waiting` (nobody has joined yet) →
`running` (somebody is talking) → `ended`, or `failed`.

### `GET /agents` and `GET /agents/{handle}` — the registry

`GET /agents` lists every agent. `GET /agents/{handle}` returns the agent
with the models it names resolved.

### `GET /health`

### `WS /ws` — the signaling socket

The nitpick room protocol, so somebody can talk to a running agent. See
[nitpick's SDK documentation](https://github.com/V1PASH/nitpick/blob/main/docs/sdk.md).

### `GET /` — the browser client

nitpick's own client, served from the installed package.

---

## Prefect

The flow is `dispatch-agent-task`. It is registered **once** — registering
it per call would restart Prefect's ephemeral server each time, adding
seconds to every dispatch.

**Self-contained by default.** With no `PREFECT_API_URL`, Prefect runs an
ephemeral local server, so there is no external infrastructure to set up.
The first dispatch pays about three seconds for the server to start; after
that a dispatch takes about one second.

**External server.** Set `PREFECT_API_URL` and nitpick-core talks to that
instead:

```bash
export PREFECT_API_URL=http://prefect-server:4200/api
```

Either way the session records its flow run id, so a conversation can be
traced back to the dispatch that started it.

`PREFECT_HOME` is resolved to an absolute path and exported, so the
ephemeral server subprocess finds the same state the service does — a
service should not depend on whose home directory it runs under.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MINIDB_PATH` | `voice.db` | The registry database |
| `AGENT_COLLECTION` | `agent` | The collection the agents live in |
| `MODEL_COLLECTION` | `models` | The collection the models live in |
| `NITPICK_CORE_HOST` | `0.0.0.0` | The HTTP host |
| `NITPICK_CORE_PORT` | `8100` | The HTTP port |
| `NITPICK_CORE_MAX_SESSIONS` | `4` | How many sessions run at once |
| `NITPICK_CORE_SESSION_MAX_SECONDS` | `900` | The longest a session may live |
| `NITPICK_CORE_JOIN_TIMEOUT` | `120` | How long a session waits for somebody to join |
| `NITPICK_CORE_IDLE_TIMEOUT` | `30` | How long a session waits after everybody leaves |
| `NITPICK_CORE_DEFAULT_ROOM` | `demo` | The room a task joins when it does not name one |
| `NITPICK_CORE_WEB_ROOT` | nitpick's client | The browser client to serve |
| `PREFECT_API_URL` | unset | An external Prefect server |
| `PREFECT_HOME` | `.prefect` | Where Prefect keeps its state, relative to the working directory |

---

## How a session runs

```
POST /tasks
    │
    ▼
Prefect flow ──▶ runtime.dispatch()
    │                │
    │                ├─ resolve the agent from minidb   ← fails fast
    │                ├─ build the spec
    │                └─ queue it
    │
    ▼ returns a ticket
                        │
              a worker picks it up
                        │
                        ├─ build the nitpick Agent
                        ├─ join the room
                        ├─ start the pipeline
                        │
              the session lives until:
                        ├─ the pipeline ends
                        ├─ everybody leaves (idle timeout)
                        ├─ nobody joins (join timeout)
                        └─ it hits its maximum duration
```

The agent is a participant in the room, so a browser (or the nitpick SDK)
can join and talk to it exactly as it would talk to any other peer.

---

## Tests

```bash
pytest              # everything
pytest -m "not slow"  # skip the tests that start a Prefect server
```

42 tests covering the registry lookups, the document-to-agent translation,
the dispatch queue and session lifecycle, the HTTP surface, and the Prefect
flow.

---

## Notes

* **The `nitpick` name is taken on PyPI.** The framework is installed from
  its GitHub repository; `nitpick-core` itself is free to publish.
* **Providers.** nitpick speaks the OpenAI wire format, plus Deepgram and
  Sarvam for speech-to-text. An Anthropic model needs a `base_url` in its
  config, pointing at an OpenAI-compatible gateway.
* **The Django service is not modified.** nitpick-core reads the same
  database file, so the two agree without a network hop. Pointing
  `MINIDB_PATH` at the same file is the whole integration.

## License

BSD-2-Clause.
