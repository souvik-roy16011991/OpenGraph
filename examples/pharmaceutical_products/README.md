# Pharmaceutical Products — example kb-config

Regulatory affairs + quality assurance for drug development and manufacturing.

## Expected KB content

- `knowledge/*.json` — chapters covering GMP/GLP/GCP, ICH guidelines, stability testing, clinical trial phases, pharmacovigilance, CMC, labeling/artwork control, regulatory filings (IND, NDA, eCTD).
- `tool/*.json` — LIMS (LabWare, STARLIMS), eCTD publishing (Veeva Vault RIM, Lorenz docuBridge), PV systems (Oracle Argus, ArisGlobal LifeSphere), CTMS (Medidata Rave, Veeva Vault CTMS), lab instruments, QMS platforms.

## Customizations demonstrated

- `domain.yaml` — regulatory-team identity; chapter headings aligned with ICH structure.
- `prompts/synthesize_tool.md` — tool reference template that calls out GxP validation status, 21 CFR Part 11 compliance, and data-integrity (ALCOA+) attributes — things pharma teams care about beyond generic "purpose/SLA" fields.
- `extractor/keywords.yaml` — adds `Instrument`, `Assay`, `Method`, `Protocol`, `SOP`, `Specification` to tool/process keyword sets so tables of lab instruments and test protocols register as ToolNode/ProcessNode.

## Build & query

```bash
python scripts/build_graph.py --kb-config examples/pharmaceutical_products
python scripts/query_cli.py  --kb-config examples/pharmaceutical_products \
  --query "What stability studies are required for an NDA submission?"
```
