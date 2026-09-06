# CET Subjective Grading Rubric & Confidence Reference

## 1. Official Holistic 5-Band Architecture

CET Writing and Translation utilize a holistic 15-point scale structured around 5 official anchor bands:

| Anchor Band | Point Range | Target Standard Description |
| :---: | :---: | :--- |
| **14** | 13–15 | **Excellent / Fluent:** Completely fulfills prompt requirements. Thoughts expressed clearly, logically organized, seamless cohesion. Rich and precise vocabulary, natural and varied sentence structures. Minor slips only. |
| **11** | 10–12 | **Good / Competent:** Fulfills prompt requirements well. Clear central ideas, coherent progression. Good vocabulary breadth with appropriate collocations; syntactic structures varied with few distracting errors. |
| **8** | 7–9 | **Adequate / Borderline:** Basic requirements fulfilled. Main ideas comprehensible despite occasional digressions. Simple vocabulary and sentences predominate. Noticeable grammatical/lexical errors that do not severely impair communication. |
| **5** | 4–6 | **Weak / Deficient:** Inadequately addresses prompt. Fragmented organization, poor logical sequence. Restricted vocabulary, frequent repetitive sentence patterns. Numerous major grammatical/lexical errors impairing comprehension. |
| **2** | 1–3 | **Very Poor / Minimal:** Fails to respond to the prompt or barely produces intelligible text. Disjointed words or sentence fragments. Severe language deficiencies throughout. |
| **0** | 0 | Blank paper, completely off-topic, or copied irrelevant passages. |

---

## 2. Point Score & Uncertainty Interval Guidelines

Because LLM evaluation inherently contains non-zero variance, every subjective assessment must output:
1. `estimated_score`: Point score (integer or float, 1.0–15.0).
2. `[score_low, score_high]`: Plausible scoring interval encompassing assessor uncertainty.
3. `confidence`: High, Medium, or Low.

### Confidence Interval Rules
- **High Confidence:** Narrow range (span = 1 to 2 points, e.g. [11.0, 12.0]). Applied when the essay clearly fits squarely into a band without conflicting signals.
- **Medium Confidence:** Moderate range (span = 2 to 3 points, e.g. [10.0, 12.0]). Applied when the text has mixed traits (e.g., strong vocabulary but weak paragraph transitions).
- **Low Confidence:** Wide range (span = 3 to 4 points, e.g. [9.0, 13.0]). Applied when the text is borderline between two anchor bands or highly polarized.

---

## 3. Pedagogical Diagnostic Dimensions (0–100 Scale)

In addition to official anchor scores, evaluate 4 internal diagnostic dimensions (0–100) for student coaching:

### Writing Dimensions:
1. `task_fulfillment`: Prompt responsiveness, argument relevance, topic completeness.
2. `cohesion_organization`: Paragraph structuring, transitional phrasing, logical flow.
3. `vocabulary_breadth`: Lexical range, idiomatic expressions, absence of wrong word forms.
4. `syntactic_variety`: Variety of sentence structures (complex clauses, inversion, participle clauses), grammatical precision.

### Translation Dimensions:
1. `fidelity_accuracy`: Information completeness, absence of mistranslation or omissions.
2. `syntactic_structure`: Natural English syntactic patterns vs Chinglish word-for-word translation.
3. `lexical_appropriateness`: Domain-specific cultural/historical vocabulary precision.
4. `fluency_readability`: Register appropriateness, punctuation, and overall stylistic coherence.

> [!IMPORTANT]
> Diagnostic dimension scores are pedagogical feedback only and are NOT part of official CET score certificates.
