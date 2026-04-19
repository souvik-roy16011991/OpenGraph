# K-12 Curriculum & Pedagogy — example kb-config

Zero-override example. Demonstrates that a new domain works with **nothing but the two KB JSON folders** — the pipeline auto-derives everything else.

## Expected KB content

- `knowledge/*.json` — chapters covering standards (Common Core, NGSS, state frameworks), unit plans, instructional strategies, assessment design, differentiation, SEL, special education (IEP/504) compliance.
- `tool/*.json` — LMS (Canvas, Schoology, Google Classroom), SIS (PowerSchool, Infinite Campus), assessment platforms (Edulastic, Illuminate), content providers (IXL, Khan Academy), accessibility tools, gradebook systems.

## What's demonstrated

**Nothing is overridden.** No `domain.yaml`, no `prompts/`, no `extractor/keywords.yaml`. The pipeline:

- Derives `domain_display_name` from the `--domain` CLI hint (or folder name).
- Generates `organization_name` as `"your K12 Curriculum team"` (template fallback).
- Extracts `knowledge_focus_examples` from the first few chapter headings in the knowledge KB.
- Extracts `tool_focus_examples` from the first few tool-named rows in the tool KB.
- Uses the default tool/process column keywords (`Tool`, `System`, `Platform`, `Step`, `Workflow`, …), which already match most education-vendor tables.
- Uses the unmodified BASE_* prompt templates — generic enough that "Implemented at <your K12 Curriculum team>" reads naturally.

## Build & query

```bash
python scripts/build_graph.py --kb-config examples/education_k12 \
  --domain "K-12 curriculum"
python scripts/query_cli.py  --kb-config examples/education_k12 \
  --domain "K-12 curriculum" \
  --query "How do I scaffold a CCSS-aligned unit for ELLs in 6th grade ELA?"
```

Once you've built once, inspect the auto-profile at `examples/education_k12/.generated/profile.yaml` (created when `OPENROUTER_API_KEY` is set and `--no-llm-profile` is not passed). Copy any field you want to pin into a new `domain.yaml`.
