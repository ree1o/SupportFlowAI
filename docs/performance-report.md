# Performance measurements (M9)

## Method

Run the repeatable diagnostic against a dedicated PostgreSQL database ending in
`_test`. From `backend`, set `TEST_DATABASE_URL` to the Compose host mapping and run:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://supportflow:supportflow_dev_only@127.0.0.1:15432/supportflow_test'
..\.venv\Scripts\python.exe -m pytest -q -s tests/test_release_performance.py
```

`test_release_request_path_benchmark` creates synthetic rows in a random schema,
installs the current migrations, and drops only that schema in fixture cleanup. It
measures sequential FastAPI `TestClient` calls for ticket listing, operations reporting,
related-ticket vector search, ticket creation, staff public replies, customer replies,
and signed webhook ingestion. Radar timing covers the bounded candidate-row query plus
the deterministic correlation function. The report prints p50, nearest-rank p95,
mean, sequential request throughput and status errors. Queue/report/retrieval are
sampled 20 times at each dataset size; mutations are sampled 10 times. Radar is timed
once per size. Synthetic vector rows exercise PostgreSQL vector retrieval but do not
run the embedding model.

These are local diagnostic measurements, not concurrent load tests, SLOs, capacity
claims or production results. TestClient timings include Python, application and local
Docker database round trips, but exclude a real network path, browser, and concurrent
clients. Hardware CPU/RAM were not captured. The test counts HTTP statuses but does not
measure database resource consumption. No production endpoints or user data are used.

## Recorded local run

Measured 2026-10-31 on Windows 10 (build 19045), Intel64 Family 6 Model 154 Stepping 3,
16 logical CPUs, Python 3.12.4, Docker Compose, PostgreSQL 16 and pgvector 0.8.1. RAM
was not captured. All measured HTTP samples succeeded (zero errors).

| Operation | Synthetic size | Samples | p50 ms | p95 ms | Mean ms | Sequential req/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Ticket list | 100 | 20 | 22.83 | 29.72 | 24.32 | 41.12 |
| Ticket list | 500 | 20 | 24.75 | 39.91 | 27.72 | 36.07 |
| Operations report | 100 | 20 | 22.85 | 31.06 | 24.76 | 40.39 |
| Operations report | 500 | 20 | 25.66 | 38.22 | 28.06 | 35.64 |
| Related ticket vector query | 100 | 20 | 22.47 | 40.48 | 27.11 | 36.88 |
| Related ticket vector query | 500 | 20 | 58.38 | 65.58 | 59.26 | 16.87 |
| Ticket creation | n/a | 10 | 35.47 | 54.58 | 39.10 | 25.58 |
| Staff public reply | n/a | 10 | 31.52 | 38.88 | 32.76 | 30.52 |
| Customer public reply | n/a | 10 | 37.15 | 50.01 | 36.95 | 27.07 |
| Signed webhook ticket ingestion | n/a | 10 | 45.93 | 62.99 | 46.52 | 21.49 |
| Radar candidate query + correlation | 100 tickets | 1 | — | — | 10.46 | — |
| Radar candidate query + correlation | 500 tickets | 1 | — | — | 36.78 | — |

The semantic query latency increase at 500 rows is consistent with exact cosine ranking
scoring the eligible tenant rows; this small run is not enough to establish a scaling
curve. M8's separate model evaluation measured warm query encoding at 6.84 ms and SQL
at 1.844 ms for its 32-document fixture. That separate number is not directly
comparable with this end-to-end related-ticket route measurement. M8 also measured
32 local CPU embeddings in 0.262 seconds (121.97 documents/second), stored 49,152 raw
vector bytes, and 106,496 PostgreSQL table bytes for its temporary fixture. Details and
limitations are in [semantic retrieval](semantic-retrieval.md).

## Reproduction and interpretation

The integration benchmark is included in the ordinary backend test suite and prints
one `RELEASE_PERFORMANCE_REPORT` JSON record when run with `-s`. Each run creates a
new disposable schema and synthetic organization, and cannot use a database whose
name does not end in `_test`. The number of repetitions and dataset sizes are fixed in
the test to keep comparisons consistent. Compare results only when environment,
container resources, model-cache state, code revision and workload are also recorded.

The run did not collect `EXPLAIN ANALYZE`, CPU/RAM, concurrent throughput, or storage
for the M9 synthetic workload. Those remain measurement gaps; do not derive performance
claims from the table above. PostgreSQL index choice and exact vector-search behavior
are described in [M8 semantic retrieval](semantic-retrieval.md). No speculative
optimization was made based on this small sample.
