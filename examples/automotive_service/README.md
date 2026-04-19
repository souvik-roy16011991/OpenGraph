# Automotive Service — example kb-config

Dealership / independent-shop service operations: diagnostic procedures, repair workflows, technical service bulletins, OEM tool stacks.

## Expected KB content

- `knowledge/*.json` — chapters covering vehicle systems (powertrain, chassis, electrical, HVAC, ADAS), diagnostic procedures, torque specs, fluid specifications, TSBs, recall campaigns, warranty policy.
- `tool/*.json` — OEM scan tools (Tech 2, IDS, VCDS, GDS), DMS (CDK, Reynolds & Reynolds), parts catalogues, alignment machines, ADAS calibration rigs, service-information systems (Mitchell 1, ALLDATA), lift/diagnostic bay equipment.

## Customizations demonstrated

- `domain.yaml` — pitched at a service advisor / master technician audience.
- `prompts/synthesize_process.md` — repair-workflow template with sections for safety warnings, required special tools, post-repair validation, and warranty-claim documentation.
- `extractor/keywords.yaml` — adds `Procedure`, `Diagnostic Step`, `Torque Spec` to process keywords; `Scan Tool`, `DMS`, `Rig` to tool keywords.

## Build & query

```bash
python scripts/build_graph.py --kb-config examples/automotive_service
python scripts/query_cli.py  --kb-config examples/automotive_service \
  --query "What's the ADAS recalibration procedure after a windshield replacement?"
```
