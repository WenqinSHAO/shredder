# CLI smoke test

Run this sequence from repo root:

```bash
python -m src.cli init demo --theme "network systems"
python -m src.cli run-step demo discovery
python -m src.cli run-step demo parsing --paper-id sample --pdf examples/sample.pdf
python -m src.cli run-step demo extraction --paper-id sample
python -m src.cli render demo
```

Expected discovery outputs:
- `workspace/demo/artifacts/discovery/raw_openalex.tsv` (if connector enabled)
- `workspace/demo/artifacts/discovery/raw_crossref.tsv` (if connector enabled)
- `workspace/demo/artifacts/discovery/raw_semantic_scholar.tsv` (if connector enabled)
- `workspace/demo/artifacts/discovery/raw.tsv`
- `workspace/demo/artifacts/discovery/deduped.tsv`

If network is unavailable, discovery emits deterministic `raw_mock.tsv` rows with `source=mock`.
