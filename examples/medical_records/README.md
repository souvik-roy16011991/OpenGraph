# Medical Records — example kb-config

Clinical information systems for a hospital or integrated delivery network: EHR workflows, HIE integrations, coding/billing, HIPAA/42 CFR Part 2 compliance.

## Expected KB content

- `knowledge/*.json` — chapters covering patient admission & discharge flows, clinical documentation, CPT/ICD-10/HCPCS coding, HIPAA / 42 CFR Part 2 privacy rules, release-of-information workflows, audit log reviews, consent management.
- `tool/*.json` — EHR (Epic, Cerner/Oracle Health, Meditech, Allscripts), HIE networks (CommonWell, Carequality), coding tools (3M 360 Encompass, TruCode), ROI platforms (MRO, Verisma), audit/compliance tools, identity management.

## Customizations demonstrated

- `domain.yaml` — identifies the Health Information Management (HIM) team and uses clinical-informatics vocabulary in focus examples.
- `prompts/synthesize_tool.md` — tool-lookup template that surfaces PHI handling, HIPAA BAA status, HITRUST/SOC 2 certifications, and audit-log retention per tool — the things a HIM or compliance officer must confirm before use.
- `extractor/keywords.yaml` — adds `EHR`, `HIE`, `Module` as tool keywords; `Encounter`, `Release` as process keywords.

## Build & query

```bash
python scripts/build_graph.py --kb-config examples/medical_records
python scripts/query_cli.py  --kb-config examples/medical_records \
  --query "What's the ROI workflow for 42 CFR Part 2 protected records?"
```
