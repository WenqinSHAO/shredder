# CLI smoke test

Run this sequence from repo root:

```bash
python -m src.cli init demo --theme "network systems"
python -m src.cli run-step demo discovery
python -m src.cli run-step demo parsing --paper-id sample --pdf examples/sample.pdf
python -m src.cli run-step demo extraction --paper-id sample
python -m src.cli render demo
python -m src.cli retrieve-paper demo --doi "10.1145/3366423.3380296"
python -m src.cli retrieve-open demo --prompt "memory disaggregation datacenter systems" --top-n 5
```

Expected discovery outputs:
- `workspace/demo/artifacts/discovery/raw_openalex.tsv` (if connector enabled)
- `workspace/demo/artifacts/discovery/raw_crossref.tsv` (if connector enabled)
- `workspace/demo/artifacts/discovery/raw_semantic_scholar.tsv` (if connector enabled)
- `workspace/demo/artifacts/discovery/raw.tsv`
- `workspace/demo/artifacts/discovery/deduped.tsv`

If network is unavailable, discovery emits deterministic `raw_mock.tsv` rows with `source=mock`.

Retrieval outputs:
- `workspace/demo/artifacts/retrieval/deterministic_request.yaml`
- `workspace/demo/artifacts/retrieval/deterministic_result.yaml`
- `workspace/demo/artifacts/retrieval/deterministic_sources.tsv`
- `workspace/demo/artifacts/retrieval/candidates_raw.tsv`
- `workspace/demo/artifacts/retrieval/candidates_ranked.tsv`
- `workspace/demo/artifacts/retrieval/handoff.tsv`
- `workspace/demo/artifacts/retrieval/candidates_summary.yaml`
