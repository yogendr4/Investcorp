# Investcorp AI Engineer Assignment

This repository contains the implementation for the Investcorp AI Engineer assignment, covering data inspection, baseline implementation, and iterative improvement of an AI-driven solution over the provided assignment dataset.

**Current status:** Repository initialized; application baseline not yet built.

**Local data location:** `data/Assignment_Data.xlsx`

**Planned progression:** Baseline → V1 → V2 → Evaluation/Hardening

## Local demo UI

A minimal Streamlit app (`app/`) provides a chat interface backed by V2.1 (with a deterministic execution trace, not model reasoning) and an evaluation dashboard over the recorded `docs/evaluation/*_results.json` files. See `docs/architecture/ui_and_local_observability.md`.

**Assumes:** `pip install -r requirements.txt`; the Baseline database built (`python -m src.baseline.build_db`); the V2 embedding index built (`python -m src.v2.build_embeddings`); `gcloud` authenticated with the Vertex AI API enabled, for meeting-question semantic retrieval.

```
streamlit run app/app.py
```
