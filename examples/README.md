# Example kb-configs

Each folder here is a self-contained `kb-config` for one domain. Copy a folder, drop in your KB JSONs, and build:

```bash
# 1. Copy an example that's close to your domain
cp -r examples/pharmaceutical_products /path/to/my-kb

# 2. Drop your KB JSONs in
cp my_knowledge.json /path/to/my-kb/knowledge/
cp my_tools.json     /path/to/my-kb/tool/

# 3. Build the graph
python scripts/build_graph.py --kb-config /path/to/my-kb

# 4. Query it
python scripts/query_cli.py --kb-config /path/to/my-kb --query "..."
```

## What lives in a kb-config folder

```
<kb-config>/
├── knowledge/<anything>.json      required — 1+ knowledge KB JSON(s)
├── tool/<anything>.json           required — 1+ tool KB JSON(s)
├── domain.yaml                    optional — profile overrides
├── prompts/                       optional — verbatim prompt overrides per slot
│   ├── intent_system.md
│   ├── intent_template.md
│   ├── synthesize_system.md
│   ├── synthesize_explore.md
│   ├── synthesize_process.md
│   ├── synthesize_tool.md
│   └── synthesize_compare.md
├── extractor/keywords.yaml        optional — tool/process column keyword overrides
└── .generated/profile.yaml        auto — cached LLM-polished profile (gitignored)
```

**You can start with nothing but the two JSON folders.** The pipeline will auto-derive a sensible profile from chapter headings and tool table values. Add `domain.yaml` / `prompts/*.md` when you want manual control.

## Override resolution order (per field)

1. `domain.yaml` explicit value
2. `.generated/profile.yaml` cached LLM output
3. LLM polish output at build time (if `OPENROUTER_API_KEY` is set and `--no-llm-profile` wasn't passed)
4. Deterministic KB-scan default
5. `--domain "<hint>"` + generic template (e.g., `"your {hint} team"`)

Prompt slot overrides in `prompts/*.md` **always win** over BASE_* templates.

## Included examples

| Folder | Domain | What it demonstrates |
|---|---|---|
| [`loan_assessment/`](loan_assessment/) | Personal-loan underwriting | Minimal manifest, one prompt override, custom extractor keywords |
| [`wealth_management/`](wealth_management/) | Financial advisory | Full prompt override set (all 7 slots), domain-specific organization name |
| [`pharmaceutical_products/`](pharmaceutical_products/) | Regulatory & QA | Technical domain with extractor keyword overrides (Instrument, Assay…) |
| [`automotive_service/`](automotive_service/) | Vehicle repair workflows | Process-heavy domain with `Phase`/`Procedure` keyword overrides |
| [`medical_records/`](medical_records/) | Clinical information systems | HIPAA-sensitive domain with tool-focused prompt override |
| [`education_k12/`](education_k12/) | Curriculum & pedagogy | Zero-override example — everything auto-derived from the KB |
