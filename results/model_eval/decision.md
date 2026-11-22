# Intake Model Evaluation Decision

Status: awaiting real development-set experiments.

## Method

Evaluate every candidate on at least 40 development documents, including at least 10 scanned documents. Do not use final benchmark bundles. Measure field-level F1, messy-document F1, scanned-document F1, schema-valid rate, median latency, cost per document, and failures.

## Candidate identifiers

Record the verified provider model identifier and provider documentation date for each candidate before executing live calls. The logical candidates are Claude Haiku 5.5, Claude Sonnet 5.5, Gemini 3.5 Flash-Lite, Gemini 3.8 Flash, and one OCR plus text-model route.

## Selection rule

Choose the cheapest candidate whose messy-document F1 and scanned-document F1 are each within 0.02 of the best measured value for that subgroup.

## Results

No experiment has been run. Do not name a winner until measured outputs are attached here.

## Decision

Pending.
