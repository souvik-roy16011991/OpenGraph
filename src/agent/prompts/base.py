"""
Domain-agnostic prompt infrastructure.

Provides:
- DomainPrompts: frozen dataclass holding all prompt slots for the agent pipeline.
- BASE_* template strings with {placeholders} for domain-specific text.
- build_domain_prompts(): factory that fills base templates with domain values.
- format_context_for_llm(): utility converting gathered_context to LLM-ready text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Base template strings
# All {placeholder} values are filled by build_domain_prompts().
# ---------------------------------------------------------------------------

BASE_INTENT_CLASSIFICATION_SYSTEM = """\
You are an expert {domain_display_name} knowledge assistant.
Your job is to classify user queries and extract key topics so a knowledge graph can be traversed to answer them.

You must respond with ONLY valid JSON in the exact format shown. No markdown, no explanation.\
"""

BASE_INTENT_CLASSIFICATION_TEMPLATE = """\
Classify the following user query about {domain_display_name}.

Query: "{{query}}"

Respond with this exact JSON structure:
{{{{
  "intent": "<one of: explore | process | tool_lookup | compare>",
  "kb_focus": "<one of: knowledge | tool | both>",
  "extracted_topics": ["<topic1>", "<topic2>", ...],
  "reasoning": "<1 sentence explanation>"
}}}}

Intent definitions:
- explore: User wants to understand a concept, topic, or domain area
- process: User wants step-by-step instructions for a workflow or procedure
- tool_lookup: User wants to find specific tools, systems, or software
- compare: User wants to compare options, strategies, or tools

kb_focus:
- knowledge: Focus on domain knowledge ({knowledge_focus_examples})
- tool: Focus on software/systems/technology ({tool_focus_examples})
- both: Mix of domain knowledge and tools
"""

BASE_SYNTHESIZE_SYSTEM = """\
You are an expert {domain_display_name} knowledge assistant at {organization_name}.
You provide precise, professional, actionable responses backed by the firm's knowledge base and technology stack.
Format your response in clean markdown with clear sections.\
"""

BASE_SYNTHESIZE_PROCESS_TEMPLATE = """\
The user asked: "{{query}}"

Based on traversal of the knowledge graph, here is the gathered context:

{{context}}

Please synthesize a comprehensive step-by-step response. Structure your answer as:

## Overview
<1-2 paragraph overview of the process/topic>

## Step-by-Step Guide

<For each step, include:>
### Step N: <Step Title>
**What to do:** <Clear action description>
**Tools/Systems:** <Specific tools used, with key details>
**Key Considerations:** <Important compliance, timing, or context notes>

## Key Tools & Systems Referenced
<Brief summary table of all tools mentioned, with their purpose>

## Related Knowledge Areas
<List related concepts from the knowledge base>

## Follow-Up Questions You Might Have
<3 bullet-point follow-up questions>
"""

BASE_SYNTHESIZE_EXPLORE_TEMPLATE = """\
The user asked: "{{query}}"

Based on traversal of the knowledge graph, here is the gathered context:

{{context}}

Please synthesize a comprehensive educational response. Structure your answer as:

## Overview
<1-2 paragraph introduction>

## Key Concepts

<For each major concept:>
### <Concept Name>
<Clear explanation with practical context>

## How This Is Implemented at {organization_name}
<Connect domain concepts to specific tools/systems used>

## Practical Implications
<Actionable insights for {domain_display_name} professionals>

## Related Topics
<Connected areas worth exploring>
"""

BASE_SYNTHESIZE_TOOL_TEMPLATE = """\
The user asked: "{{query}}"

Based on traversal of the knowledge graph, here is the gathered context:

{{context}}

Please provide a comprehensive tool reference response. Structure your answer as:

## Summary
<Brief overview of what tools were found>

## Tool Details

<For each tool:>
### <Tool/System Name>
- **Provider:** <Vendor/Provider>
- **Purpose:** <What it does>
- **Key Features:** <Notable capabilities>
- **Connected Systems:** <Integrations>
- **SLA/Availability:** <Uptime/reliability specs>
- **Data Classification:** <Security level>
- **Relevant For:** <Which {domain_display_name} domains it supports>

## Architecture & Integration Notes
<How these tools work together>

## Access & Security
<Who can access what and how>
"""

BASE_SYNTHESIZE_COMPARE_TEMPLATE = """\
The user asked: "{{query}}"

Based on traversal of the knowledge graph, here is the gathered context:

{{context}}

Please provide a comprehensive comparison. Structure your answer as:

## Comparison Overview
<What is being compared and why it matters>

## Comparison Table
| Dimension | Option A | Option B | ... |
|-----------|----------|----------|-----|
<Fill in relevant comparison dimensions>

## Detailed Analysis
<For each option, a paragraph analysis>

## Recommendation Framework
<Guidance on when to use each option>

## Implementation Considerations
<Practical notes on tools and processes>
"""


# ---------------------------------------------------------------------------
# DomainPrompts dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DomainPrompts:
    """Holds all prompt text for a single domain.

    Fields
    ------
    domain_name
        Registry key, e.g. ``"wealth_management"``.
    domain_display_name
        Human-readable label, e.g. ``"Wealth Management"``.
    organization_name
        Firm/org name embedded in prompts, e.g. ``"Meridian Wealth Advisors"``.
    knowledge_focus_examples
        Short phrase describing knowledge KB topics for the classification prompt.
    tool_focus_examples
        Short phrase describing tool KB topics for the classification prompt.
    intent_classification_system / intent_classification_template
        Prompt pair used by ``classify_intent`` node.
    synthesize_system
        System message for the synthesis LLM call.
    synthesize_*_template
        Per-intent user templates (filled with ``{query}`` and ``{context}``).
    """

    domain_name: str
    domain_display_name: str
    organization_name: str
    knowledge_focus_examples: str
    tool_focus_examples: str

    intent_classification_system: str
    intent_classification_template: str
    synthesize_system: str
    synthesize_process_template: str
    synthesize_explore_template: str
    synthesize_tool_template: str
    synthesize_compare_template: str

    def get_synthesis_template(self, intent: str) -> str:
        """Return the synthesis user template for the given intent."""
        mapping = {
            "process": self.synthesize_process_template,
            "explore": self.synthesize_explore_template,
            "tool_lookup": self.synthesize_tool_template,
            "compare": self.synthesize_compare_template,
        }
        return mapping.get(intent, self.synthesize_explore_template)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_domain_prompts(
    domain_name: str,
    domain_display_name: str,
    organization_name: str,
    knowledge_focus_examples: str,
    tool_focus_examples: str,
    *,
    intent_classification_system: str | None = None,
    intent_classification_template: str | None = None,
    synthesize_system: str | None = None,
    synthesize_process_template: str | None = None,
    synthesize_explore_template: str | None = None,
    synthesize_tool_template: str | None = None,
    synthesize_compare_template: str | None = None,
) -> DomainPrompts:
    """Create a DomainPrompts by filling the base templates with domain values.

    Any prompt slot can be fully overridden by passing the corresponding keyword
    argument.  Omitted slots are generated from the BASE_* templates above.

    The base templates use ``{domain_display_name}``, ``{organization_name}``,
    ``{knowledge_focus_examples}``, and ``{tool_focus_examples}`` as
    domain-level placeholders.  The per-query ``{query}`` and ``{context}``
    placeholders are left intact (double-braced in the base strings) so that
    the agent can fill them at runtime.
    """
    ctx = dict(
        domain_display_name=domain_display_name,
        organization_name=organization_name,
        knowledge_focus_examples=knowledge_focus_examples,
        tool_focus_examples=tool_focus_examples,
    )

    def _fill(override: str | None, base: str) -> str:
        return override if override is not None else base.format(**ctx)

    return DomainPrompts(
        domain_name=domain_name,
        domain_display_name=domain_display_name,
        organization_name=organization_name,
        knowledge_focus_examples=knowledge_focus_examples,
        tool_focus_examples=tool_focus_examples,
        intent_classification_system=_fill(
            intent_classification_system, BASE_INTENT_CLASSIFICATION_SYSTEM
        ),
        intent_classification_template=_fill(
            intent_classification_template, BASE_INTENT_CLASSIFICATION_TEMPLATE
        ),
        synthesize_system=_fill(synthesize_system, BASE_SYNTHESIZE_SYSTEM),
        synthesize_process_template=_fill(
            synthesize_process_template, BASE_SYNTHESIZE_PROCESS_TEMPLATE
        ),
        synthesize_explore_template=_fill(
            synthesize_explore_template, BASE_SYNTHESIZE_EXPLORE_TEMPLATE
        ),
        synthesize_tool_template=_fill(
            synthesize_tool_template, BASE_SYNTHESIZE_TOOL_TEMPLATE
        ),
        synthesize_compare_template=_fill(
            synthesize_compare_template, BASE_SYNTHESIZE_COMPARE_TEMPLATE
        ),
    )


# ---------------------------------------------------------------------------
# Context formatter (domain-agnostic utility)
# ---------------------------------------------------------------------------

def format_context_for_llm(gathered_context: list[dict[str, Any]]) -> str:
    """Convert the gathered_context list from agent state into structured text
    for LLM consumption."""
    parts: list[str] = []

    for item in gathered_context:
        node_type = item.get("node_type", "unknown")
        heading = item.get("heading", "")
        content = item.get("content", "")
        tools = item.get("tools", [])
        steps = item.get("steps", [])

        section_lines = [f"### [{node_type.upper()}] {heading}"]

        if content:
            section_lines.append(content[:1000])

        if steps:
            section_lines.append("**Process Steps:**")
            for step in steps:
                section_lines.append(
                    f"  - Step {step.get('step_number', '?')}: "
                    f"{step.get('step_name', '')} "
                    f"[System: {step.get('system_used', 'N/A')}]"
                )

        if tools:
            section_lines.append("**Tools in this section:**")
            for tool in tools:
                tool_line = f"  - {tool.get('tool_name', tool.get('heading', ''))} "
                if tool.get("provider"):
                    tool_line += f"({tool['provider']}) "
                if tool.get("purpose"):
                    tool_line += f"– {tool['purpose'][:200]}"
                section_lines.append(tool_line)

        parts.append("\n".join(section_lines))

    return "\n\n---\n\n".join(parts)
