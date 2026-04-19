# Wealth Management — example kb-config

Full-advisory firm example: investment management, financial planning, compliance surveillance, CRM/trading stack.

## Expected KB content

- `knowledge/*.json` — chapters covering client onboarding, IPS construction, portfolio construction, tax-lot accounting, compliance surveillance, reporting, succession planning.
- `tool/*.json` — CRM (Salesforce FSC, Wealthbox), portfolio accounting (Tamarac, Addepar), trading/OMS (TradeWarrior, iRebal), data feeds (Refinitiv, Bloomberg), compliance (MyComplianceOffice), reporting (Orion, Black Diamond).

## Customizations demonstrated

This example **replaces every prompt slot** verbatim — it preserves the pre-refactor wording (including "Meridian Wealth Advisors" branding) and demonstrates how a fully customized domain looks:

- `domain.yaml` — `organization_name: Meridian Wealth Advisors`.
- `prompts/intent_system.md` + `prompts/intent_template.md` — classification tuned for wealth-management vocabulary (IPS, FSC, surveillance, etc.).
- `prompts/synthesize_system.md` — system prompt grounded in the firm identity.
- `prompts/synthesize_{explore,process,tool,compare}.md` — all four synthesis templates customized (e.g., "How This Is Implemented at Meridian" section in the explore template).
- `extractor/keywords.yaml` — adds `CRM`, `OMS`, `PMS`, `Custodian` as tool columns.

## Build & query

```bash
python scripts/build_graph.py --kb-config examples/wealth_management
python scripts/query_cli.py  --kb-config examples/wealth_management \
  --query "How do I onboard a new HNW client?"
```
