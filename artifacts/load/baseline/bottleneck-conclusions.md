# T015 Baseline Bottleneck Conclusions

- This is a deterministic, route-level ASGI baseline executed on September 13, 2026.
- All nine profiles exercised real authentication and application routes; no `/health`-only result is used.
- The run recorded no 5xx responses. This is not a production capacity pass/fail result because it is sequential and in-process.
- SSE event counts and file upload/status transitions are retained per profile.
- An HR employee token could not list or open the finance knowledge base; this is the retained tenant-isolation check.
- Real Anthropic provider evidence remains physically separate under `artifacts/provider/anthropic/` and was not invoked.
