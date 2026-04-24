# 04 — Templates

A **template** is a pre-filled domain profile you can clone into a new workspace. It's the fastest way to start — you skip writing the five `/domain` fields from scratch and land on `/upload` ready to add files.

**Templates don't ship with content.** You still upload your own documents. The template only seeds the *context* (domain name, organization name, focus examples) that goes into LLM prompts and graph extraction.

## The catalog (`/templates`)

Sidebar → **Templates**, or go to `/templates` directly.

You see a grid of cards. Each shows:

- Icon + name + one-sentence description
- Category badge
- **Use this template** button

The ship-default catalog has 29 templates across 10 categories; operators can add more.

### Ship-default catalog

| Category | Templates |
|---|---|
| **Healthcare** (6) | Healthcare Protocols, Medicine Product KB, Quality Control Lab, Hospital Management, Clinical Trials, Medical Device Compliance |
| **Finance** (8) | Finance Compliance, Procurement Vendor, Wealth Management, Tax Planning, Lending Operations, Credit Analysis, Insurance Operations, Risk Management |
| **Engineering** (4) | Product Docs, DevOps SRE, Security InfoSec, Data Engineering |
| **Legal** (3) | Legal Contracts, IP Patents, Privacy GDPR |
| **Operations** (3) | IT Support, Customer Support, Manufacturing SOPs |
| **Customer** (1) | Customer Onboarding |
| **People** (1) | HR Policies |
| **Sales** (1) | Sales Enablement |
| **Marketing** (1) | Marketing Brand |
| **General** (1) | Blank (no pre-fill) |

Your operator may have added more. The list on the page is the source of truth.

## Searching & filtering

The top of the page has:

- **Search box** — type a name, domain, or topic. Matches against template name, description, and category substring. Live filter.
- **Category pills** — "All" plus one pill per category. Click to filter.

Examples:

- Type `loan` → shows Lending Operations, Credit Analysis.
- Click the **Healthcare** pill → shows all 6 healthcare templates.
- Type `HIPAA` → shows Healthcare Protocols, Medical Device Compliance (matches on description).

## Using a template

1. Click **Use this template** on any card.
2. *(Optional)* The dialog asks for a **workspace name** and **description**. Defaults are "<Template name>" and the template's description. Override to something specific to your situation — e.g. `Loan Ops — North America` rather than just `Lending Operations`.
3. Click **Create workspace**.

The app:

- Calls `POST /api/v1/templates/{slug}/instantiate`.
- Creates a new workspace with the template's `domain_config` pre-populated.
- Enforces your per-user workspace cap (402 if you're over).
- Sets the new workspace as active.
- Routes you to `/upload`.

Now go upload your own files.

## What gets pre-filled?

The template pre-populates the `/domain` fields. Roughly:

- **Domain name** (slug), e.g. `loan_underwriting`.
- **Domain display name**, e.g. `Loan Underwriting`.
- **Organization name**, e.g. `Retail Credit Desk`.
- **Knowledge focus examples** — a curated list of topics someone working in that domain would expect.
- **Tool focus examples** — systems commonly used in that domain.

Before you click **Save & continue** on `/domain`, review these. Tweak them to match *your* specific org and *your* specific KB — the more concrete, the better your agent will reason.

## Customizing a template after the fact

After instantiating, your workspace has its own copy of the domain config. Changing it on `/domain` affects only *your* workspace, not the underlying template.

Conversely, if the operator updates the template after you've instantiated, your workspace doesn't inherit the change — you already branched off.

## Custom templates *(if enabled)*

Some deployments expose:

- **+ Create template** (top-right of the catalog) — opens a form to define a new template with your own name, description, category, and domain fields. Saved to the backend via `POST /api/v1/templates`.
- **Edit** (pencil icon on templates you created) — edit your custom templates in place. `PUT /api/v1/templates/{slug}`.
- **Delete** (trash icon on templates you created) — `DELETE /api/v1/templates/{slug}`.

Use custom templates to:

- Codify your company's standard domain profile so new teammates get consistent pre-fills.
- Share a domain profile across many workspaces (one per product line, region, etc.).
- Keep commonly-tuned tool catalogs versioned.

If you don't see the **+ Create template** button, your operator hasn't enabled custom templates on this deployment.

## Which template should I pick?

Rules of thumb:

- **Match the domain more than the industry.** If you're running credit operations at a fintech, pick *Lending Operations* (finance) rather than *Customer Support* (operations) even if the KB includes some support workflows — the domain profile matters more.
- **When in doubt, pick the narrower one.** *Medical Device Compliance* is more useful than *Healthcare Protocols* for a device manufacturer because the focus examples are sharper.
- **Blank is fine.** If nothing fits, pick **Blank** and write your own domain profile on `/domain`. Templates are a shortcut; they're not magic.

## FAQ

**Q: Do templates come with example files I can use?** No. Templates only seed the domain profile. Bring your own KB.

**Q: Can I contribute my custom template back as a ship-default?** Not through the UI. Open a PR adding a YAML file to the `templates/` folder in the repo.

**Q: I instantiated from the wrong template — do I have to start over?** Nope. Your workspace is now independent of the template. Go to `/domain` and edit the five fields. Or delete the workspace and pick a different template.

**Q: Where is the template icon defined?** In the template YAML (`icon: BookOpen`, etc.). Custom templates let you pick from a Lucide icon list in the form.

**Q: Why is there a limit on how many workspaces I can create from templates?** The cap is per-user, not per-template. See [15 — Billing & limits](15-billing-and-limits.md). The free trial allows 1 workspace total.
