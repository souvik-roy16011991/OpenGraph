Classify the following user query about wealth management.

Query: "{query}"

Respond with this exact JSON structure:
{{
  "intent": "<one of: explore | process | tool_lookup | compare>",
  "kb_focus": "<one of: knowledge | tool | both>",
  "extracted_topics": ["<topic1>", "<topic2>", ...],
  "reasoning": "<1 sentence explanation>"
}}

Intent definitions:
- explore: User wants to understand a concept, topic, or domain area
- process: User wants step-by-step instructions for a workflow or procedure
- tool_lookup: User wants to find specific tools, systems, or software
- compare: User wants to compare options, strategies, or tools

kb_focus:
- knowledge: Focus on domain knowledge (financial planning, investment, compliance concepts)
- tool: Focus on software/systems/technology (CRM, trading platforms, data feeds)
- both: Mix of domain knowledge and tools
