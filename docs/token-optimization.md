# Docs research: token optimization (measured 2026-10-26)

Question: "How do I export a vertical 9:16 video in Shotcut? Give 3 short steps and cite the official URL."
Model: `muse-spark-1.3`. Scripts: `scripts/meta_search_token_bench.py` and `scripts/meta_docs_pipeline_bench.py`.

## Why the default is expensive

Each web search, page open and find-in-page step adds the retrieved content to the context. The
model re-reads the growing context on every tool step. The default reasoning effort is `high`
(`max` on Wonder), and `search_context_size` defaults to more than `low`.

## Results

| Variant | Input tok | Output tok | Searches + page ops | Approx. cost* | Answer |
| --- | --- | --- | --- | --- | --- |
| Earlier test (default, version question) | 38,616 | 1,196 | 3 + 4 | ~$0.061 | Correct |
| A: default `web_search` | 21,847 | 1,976 | 3 + 2 | ~$0.044 | Correct |
| B: `search_context_size: low`, `reasoning.effort: low` | 15,798 | 1,166 | 2 + 2 | ~$0.030 | Correct |
| C: B + `max_tool_calls: 2` + "≤2 searches, official domain" instructions | 7,177 | 1,069 | 1 + 1 | ~$0.016 | Correct |
| D: C on `muse-spark-1.3-contributor` | 6,419 | 576 | 1 + 1 | ~$0.003 | Correct (Meta may train on the data) |
| **E: our pipeline** (1 search → we fetch the pages → local BM25 picks excerpts → answer with no tools) | 3,396 | 983 | 1 + 0 | **~$0.011** | Correct |
| E with a docs cache hit (same app, already fetched) | ~300 | ~500 | 0 | ~$0.0025 | Same |

\*Standard prices: $1.25 per 1M input, $4.25 per 1M output, $2.50 per 1,000 searches.
Contributor mode: $0.10 / $0.20. Page opens are not billed as searches.

## Plan for Cueframe

1. **Search only for URLs.** Make one `web_search` call with `search_context_size: low`,
   `max_tool_calls: 1`, `reasoning.effort: low` and `include: ["web_search_call.results"]`. Take the
   URLs from the structured results, not from the model's prose. Prefer official domains
   (`site:` query plus an allow-list per app).
2. **Fetch and extract ourselves, at zero model tokens.** Use httpx with readability-style cleaning,
   chunk the page, and rank chunks with BM25 (optionally embeddings later). Send only the top
   chunks, about 1–2k tokens.
3. **Docs cache in SQLite**, keyed by app, version and URL, with page text, chunks and a fetched_at
   TTL. Repeat questions about the same app skip search and fetch entirely.
4. **Stable prefix first.** Put the system prompt and tool schemas first and the volatile content
   last, to get automatic prefix caching. Cached input is $0.15 per 1M versus $1.25. Set
   `prompt_cache_key` per job.
5. **Per-phase effort.** Use `low` for research and extraction, `medium` or `high` only for planning
   and failure diagnosis, and never `max` unless asked.
6. **Budgets.** Set a per-job token and search budget, and show the running cost in the run panel
   (from `usage`).
7. **Fallback.** If the excerpts are insufficient (the model says so), run one fuller
   `web_search` call (variant C).
8. **Optional:** use contributor-mode models for non-private research only, if the user accepts
   Meta training on that content.
