# Loan Application Assessment — example kb-config

Personal-loan underwriting: eligibility rules, KYC, bureau integrations, decisioning workflows.

## Expected KB content

- `knowledge/*.json` — chapters covering applicant classification, income & employment eligibility, FOIR, bureau score cut-offs, documentation rules, policy exceptions, collections.
- `tool/*.json` — tables of credit bureaus (CIBIL / Experian / CRIF / Equifax), KYC platforms (Karza, Digio), bank statement analyzers, loan origination systems, decisioning engines, collections platforms.

## Customizations demonstrated

- `domain.yaml` — polished `organization_name` and focus examples tuned for Indian retail credit.
- `prompts/synthesize_process.md` — loan-workflow synthesis template emphasizing compliance checks, TAT, and exception handling.
- `extractor/keywords.yaml` — adds `Bureau`, `KYC Partner` as tool columns and `TAT`, `Gate` as process columns.

## Build & query

```bash
python scripts/build_graph.py --kb-config examples/loan_assessment
python scripts/query_cli.py  --kb-config examples/loan_assessment \
  --query "How is FOIR computed for a self-employed applicant?"
```
