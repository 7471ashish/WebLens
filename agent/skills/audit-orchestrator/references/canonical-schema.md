# Canonical Schema & Evidence Gating Reference

## Schema Contract
Every audit finding produced by the marketplace must adhere to the standard schema:

- id (string): Unique identifier (e.g. MM-ALT-001, ENG-001).
- title (string): Concise summary of the objective defect.
- severity (string): 'critical' | 'high' | 'medium' | 'low'.
- evidence (string): Grounded factual evidence (DOM selector, attributes, status code, contrast ratio).
- suggested_action (object): Contains 'summary' (actionable string) and 'priority' ('critical'|'high'|'medium'|'low').
- confidence (float): 0.0 to 1.0.

## Tier-1 Evidence Rules
1. High and Critical severities are strictly prohibited unless accompanied by verifiable, quantitative evidence.
2. Subjective UX/copy opinions without empirical measurements must be placed in the suggestions array.
