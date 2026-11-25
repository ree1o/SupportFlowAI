# Local generative AI runtime

## Optional service boundary

Generative inference is an optional local service. `SUPPORTFLOW_AI_ENABLED` defaults
to `false`; PostgreSQL, Redis, API liveness/readiness, tickets, and M1-M9 workflows do
not depend on Ollama. The first provider adapter is synchronous and makes bounded HTTP
requests only to an exact allowlist of local Ollama endpoints. It has no cloud provider
or automatic external fallback. Ordinary request paths do not call the model.

The authenticated endpoint
`GET /api/organizations/{organization_id}/ai/status` checks active staff membership
before returning whether the configured model is disabled, unavailable, missing, or
available. It does not expose host paths, model output, or service error details.

## Initial model selection

The primary local model is `qwen3.5:4b-q4_K_M`, an Ollama Q4_K_M quantization of the
Qwen3.5 4B instruct model. The upstream model card declares Apache-2.0. The local Ollama
artifact is 3,324,173,934 bytes (about 3.10 GiB), 4.2B parameters, and supports tool
calling and structured output. Ollama reports a 262,144-token model maximum; SupportFlow
will deliberately request only 4,096 tokens initially. The complete model/license is
available with `ollama show qwen3.5:4b-q4_K_M --license`; upstream license and model
information: [Qwen3.5 model card](https://huggingface.co/Qwen/Qwen3.5-4B) and
[Ollama library entry](https://ollama.com/library/qwen3.5:4b).

The approved fallback is `qwen3.5:2b-q4_K_M`, but it is not downloaded or selected
automatically. Switch to it only after observing resource pressure or unacceptable
latency/quality and then rerun the evaluation. Model choice is operator configuration,
restricted to the two reviewed local identifiers. The application never treats a
model-reported confidence field as a calibrated probability.

## Host setup (Windows + Docker Desktop)

Install Ollama for Windows from its official distribution, download the selected model
once, and check it locally:

```powershell
ollama pull qwen3.5:4b-q4_K_M
ollama show qwen3.5:4b-q4_K_M
ollama list
```

The supported Windows setup keeps Ollama native on the host so it can use the host GPU;
Compose containers call `http://host.docker.internal:11434`. Ollama must listen on an
interface reachable from Docker for container access. If configuring `OLLAMA_HOST` to
make that possible, restrict access to the local Docker/host network with Windows
Firewall rules; do not expose port 11434 publicly. The endpoint remains a fixed local
allowlist, not a tenant/user-provided URL. Leave AI disabled until the local endpoint is
reachable and the operator has configured that network boundary.

Add these values to the developer's ignored `.env` only when opting in:

```dotenv
SUPPORTFLOW_AI_ENABLED=true
SUPPORTFLOW_AI_MODEL=qwen3.5:4b-q4_K_M
SUPPORTFLOW_AI_OLLAMA_URL=http://host.docker.internal:11434
SUPPORTFLOW_AI_TIMEOUT_SECONDS=120
SUPPORTFLOW_AI_STATUS_TIMEOUT_SECONDS=1.5
SUPPORTFLOW_AI_CONTEXT_TOKENS=4096
SUPPORTFLOW_AI_REPLY_MAX_TOKENS=512
SUPPORTFLOW_AI_SUMMARY_MAX_TOKENS=700
SUPPORTFLOW_AI_TEMPERATURE=0.15
SUPPORTFLOW_AI_KEEP_ALIVE=1m
```

Restart API/worker containers after changing settings. The model remains in Ollama's
user-managed persistent cache, outside Git and outside the PostgreSQL volume. `keep_alive`
is one minute by default so the GPU can be reclaimed when the model is idle. Generation
runs asynchronously on the isolated Celery `ai` queue at concurrency one. The API commits
run state and outbox work together, returns `202`, and the staff UI polls the run. Reply
generation and summaries cannot delay ordinary ticket requests. Model calls have a bounded
120-second timeout and worker recovery/retry policies.

## M10.1 host observation (2026-11-24)

This is a small hardware smoke test, not an application-quality benchmark:

| Observation | Measured value |
| --- | --- |
| Host | Windows 11 Pro build 26300; Core i5-12500H, 12 cores / 16 threads |
| RAM | 15.68 GiB total; 5.43 GiB free when inspected before inference |
| GPU | GeForce RTX 3060 Laptop GPU, 6 GiB dedicated VRAM |
| Artifact | 3,324,173,934 bytes; Ollama showed 3.3 GB |
| Model identity | `qwen3.5:4b-q4_K_M`, Ollama manifest digest `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04` |
| First one-line JSON probe | 68.8 s total, 47.6 s model load, 26 prompt / 6 output tokens |
| Warm one-line JSON probe | 0.843 s total, 3.9 ms load, 20 prompt / 6 output tokens (about 12.1 generated tokens/s) |
| GPU placement | Ollama reported 100% GPU; post-probe NVIDIA query showed 3,770 MiB used and 2,225 MiB free |

The test generated no customer data. The short warm probe only establishes that the model
loads and returns valid JSON on this host. Six output tokens are too few for a reliable
latency or quality conclusion. The cold start is visibly slow; the warm result is
promising for an asynchronous draft workflow, but real 100-300-token outputs, concurrent
application resource usage, summaries, multilingual quality, and tool calling still need
repeatable measurement before setting user-facing promises. The 4B Q4 model fits in the
observed dedicated VRAM; shared GPU memory was not counted as dedicated capacity.

Docker Desktop's Linux engine was initially unavailable during inspection. After the
user started Docker Desktop, Docker 29.9.3 and the existing Compose stack were verified
healthy. The API container reached the host Ollama version endpoint at
`host.docker.internal:11434` and received version `0.40.18`. Existing PostgreSQL and
semantic-model volumes were preserved, and no Docker or WSL reset or firewall change
was needed. Keep the Ollama endpoint private to the host; do not expose it publicly.

## Provider settings and failure behavior

The initial request configuration is temperature `0.15`, context `4096`, output capped
at 512 tokens for reply drafts or 700 for summaries, one-minute keep-alive, and a 120 s
provider timeout. The adapter disables Qwen thinking mode for bounded product outputs
and asks Ollama for the Pydantic response schema. Prompts are capped at 24,000
characters. Invalid JSON, schema mismatch, timeout, or unavailable Ollama become a safe
`ProviderUnavailable` error; sensitive prompts and provider response bodies are not
logged. The adapter does not call Ollama when disabled.

## M10.3-M10.5 local inference verification

On 2026-11-25, the local model was restarted after the host shutdown and its existing
cached artifact was reused. The API container verified model availability and matched
the expected artifact digest. The model returned one valid `get_sla_status` tool call
through the implemented `/api/chat` adapter. A separate opt-in PostgreSQL integration
smoke exercised the staff run workflow, published source retrieval, real local model,
tool loop, structured response validation, and tenant-scoped result readback; it passed
in 20.42 seconds. This is one synthetic smoke case, not a generation-quality or latency
benchmark. No customer data was used.

The Copilot UI displays local availability and inserts only a draft into the existing
public reply editor after a human action. It never sends generated content. When Ollama
is disabled or unavailable, status is shown and ordinary ticket operations remain
available. The standard test suite does not download or require a generative model; set
`RUN_LOCAL_AI_SMOKE=1` to enable the optional real-model integration test.

There is no cloud fallback. The provider URL is restricted to explicit local endpoints.
The Compose default remains disabled; local opt-in instructions are above.
