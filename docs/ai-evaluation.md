# M10 Copilot evaluation

## Purpose and limits

This is a reproducible engineering evaluation of a small synthetic knowledge corpus. It
does not measure production accuracy, language generalization, or customer outcomes. The
only automated correctness labels concern source relevance and whether the scenario is
answerable from the supplied published articles. A human must still review the answer for
factual correctness, language quality, tone, and safety before any draft is used.

## Dataset and split

`backend/tests/fixtures/copilot_eval.json` contains 36 synthetic scenarios (12 development,
24 held out), 10 published article examples plus an unpublished stale revision, and
English, Russian, and Kazakh content. It includes paraphrases, cross-language requests,
generic distractors, insufficient-context questions, conflicting article versions, and
prompt-injection attempts. Article/scenario identifiers and labels are not included in the
model prompt. The unpublished revision is excluded from retrieval and generation.

The evaluation first embeds article chunks and each scenario using the configured local
Knowledge Base embedder. It uses exact cosine ranking, the same top-four and 0.30 relevance
floor as the current Copilot path, then builds a structured generation prompt from only
those retrieved current published chunks. If retrieval returns no source, generation is
skipped and the system abstains. This is an in-memory replay of the current retrieval rule;
database authorization, tenant isolation, and citation-version checks are tested separately
against PostgreSQL and the real runtime role.

The generator is the configured native Ollama model. No remote provider is configured or
used. Evaluation records case IDs, citation identifiers, contract outcomes, and latency;
it does not write generated text or prompt context to the result artifact. The current run
uses the pinned model digest and default generation settings (temperature 0.15, 4096 token
context, bounded output). Results can vary by hardware/runtime despite the fixed model.

## Metrics

- **Retrieval recall@4**: relevant article IDs present in the top four, averaged over
  answerable scenarios only.
- **Retrieval MRR@4**: reciprocal rank of the first relevant article, averaged over
  answerable scenarios only.
- **Answerable citation recall**: answerable scenarios where the generated citations
  include at least one ground-truth relevant article, divided by answerable scenarios.
- **Abstention accuracy**: unanswerable scenarios where the assistant abstained, divided
  by unanswerable scenarios.
- **Citation reference validity**: generated citation identifiers resolve to a source
  actually supplied for that case, divided by all scenarios. Server-side source/version
  authorization is covered separately by integration tests.
- **Latency**: end-to-end local query embedding plus generation where generation occurs;
  retrieval-only abstentions are included. Report mean and nearest-rank p95.

### Proposed release acceptance targets

These are engineering gates for this small held-out fixture, not claims about production.
They were set after the initial exploratory all-documents prompt and before the final
retrieval-aware run. Do not tune the prompt or threshold against the held-out set; changes
after inspection require a new held-out set. Status below is measured once; failed targets
remain failed and are not tuned away.

| Check | Target |
| --- | ---: |
| Citation reference validity | 100% |
| Retrieval recall@4 on answerable cases | >= 0.80 |
| Answerable citation recall | >= 0.80 |
| Abstention accuracy on unanswerable cases | >= 0.90 |
| Deterministic tenant/source security violations | 0 |
| Structured output validation | >= 95% |
| Warm local generation p95 | <= 30 seconds on the documented workstation |

Structured output, tenant boundaries, and source authorization are hard contract/security
checks. Retrieval and generation metrics are small-sample quality indicators. Human answer
review is separate: score each answer as **0** unsupported/unsafe, **1** partly supported
or materially incomplete, or **2** fully supported and appropriately phrased. Record
reviewer notes and examples; no human-review score is claimed unless that review is run.

## Reproduction

From `backend`, after explicitly installing the pinned model caches and starting native
Ollama:

```powershell
$env:SUPPORTFLOW_AI_ENABLED = "true"
$env:SUPPORTFLOW_AI_OLLAMA_URL = "http://127.0.0.1:11434"
$env:SUPPORTFLOW_SEMANTIC_MODEL_CACHE_DIR = (Resolve-Path ..\.cache\supportflow\kb-embedding-eval).Path
..\.venv\Scripts\python.exe -m app.ai.evaluation --split heldout
```

The evaluator never downloads model files. It requires the local Ollama model and the
separately installed Knowledge Base embedding model cache. `--split development` is
available for prompt debugging; `--limit N` is for a smoke run and must not be reported as
the full evaluation.

## Results

### Retrieval-aware held-out run (2026-11-25)

The run used `qwen3.5:4b-q4_K_M`, digest
`d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`, with the pinned
Knowledge Base embedder and the same top-four / 0.30 relevance-floor rule as production.
The 24 cases comprise 15 answerable and 9 insufficient-evidence examples. The evaluator
retrieved 10 chunks and generated on all 24 scenarios (the relevance floor admitted at
least one chunk for every case).

| Measure | Result | Target | Outcome |
| --- | ---: | ---: | --- |
| Retrieval recall@4 | 15/15 (1.000) | >= 0.80 | Met |
| Retrieval MRR@4 | 0.844 | Report | — |
| Answerable citation recall | 5/15 (0.333) | >= 0.80 | **Not met** |
| Insufficient-evidence abstention accuracy | 8/9 (0.889) | >= 0.90 | **Not met** |
| Citation reference validity | 24/24 (1.000) | 100% | Met |
| Structured output accepted | 24/24 (1.000) | >= 95% | Met |
| Mean end-to-end latency | 9.57 s | Report | — |
| End-to-end p95 latency | 15.36 s | <= 30 s | Met |

Per-language counts (8 scenarios each):

| Language | Answerable cases | Relevant citations | Correct abstentions / insufficient-evidence cases |
| --- | ---: | ---: | ---: |
| English | 5 | 2/5 | 2/3 |
| Russian | 5 | 1/5 | 3/3 |
| Kazakh | 5 | 2/5 | 3/3 |

The result is mixed: retrieval found a labeled relevant source for every answerable case,
but the 4B generator often abstained despite evidence or selected a source that did not
match the exact fixture label; it also answered one unanswerable question. Citation IDs
were always drawn from the supplied source set, which is structural validity rather than
evidence that the cited article supports every sentence. The Copilot must remain optional,
experimental, and human-reviewed. These quality targets are **not met**, and the run is
not presented as a quality pass. Changes to prompts, labels, threshold, or model require a
new held-out dataset before claiming improvement.

No human answer-quality review was conducted. Security and tenant/source checks are
reported separately from this synthetic answer-quality metric.

The separate 12-case development split returned retrieval recall@4 **9/9 (1.000)**,
MRR@4 **0.944**, relevant citations **8/9 (0.889)**, correct abstentions **3/3**, and
valid citation references **12/12**; mean latency was **8.18 s**, p95 **14.93 s**. This
development result was not used to change the prompt after the held-out run. The difference
from held-out scores indicates the tiny fixture's sensitivity to scenario composition and
is another reason not to generalize from it.

An earlier diagnostic passed all 10 published articles to the generator instead of using
retrieval. It returned valid source-reference structure for 24/24 cases, cited an expected
source in 8/15 answerable cases, and correctly abstained in 7/9 insufficient-evidence
cases. That diagnostic is not comparable to the retrieval-aware run and is retained only
as context.

## Evaluation limitations

- The corpus and labels are synthetic and small; each language has only a few examples.
- The current harness uses fixture articles in memory, while PostgreSQL integration tests
  separately verify the real tenant-filtered query and current-publication checks.
- Citation presence is not proof the answer is correct or fully supported.
- Similarity and model-generated text are not calibrated confidence or root-cause claims.
- Prompt-injection fixtures test behavior on selected examples, not general resistance.
- Latency is workstation-specific and includes local model/service warm-state effects.
