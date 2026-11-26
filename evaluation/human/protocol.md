# Human Planning Evaluation Protocol

## Purpose

Measure planning quality, elapsed time, and estimated labor cost for human plans using the same plan structure and scoring code as the AI systems.

## Participants and cases

- Participants: Jisfaaz and Shazan.
- Use six test bundles selected by Ajey and Hatheem to cover S1, S3, S5, S6, S8, and S9 and all three mess levels.
- Default allocation is three bundles per participant. If time permits, both may complete all six.
- Participants must not see these bundles or their answer keys before their timed sessions.

## Allowed resources

- Excel and a calculator only.
- No AI tools, answer keys, generator state, prior scored plans, or help from another participant.
- The participant may inspect only the bundle's `inputs/` files.

## Session procedure

1. Ajey records the participant ID, bundle ID, start timestamp, and the hourly-rate assumption and source in the workbook.
2. Start timing immediately before the participant opens the input folder.
3. The participant fills the provided workbook. Blank tables must remain explicitly blank when there are no shortages, purchase orders, or flags.
4. Stop at submission or after 90 minutes, whichever occurs first. Record the end timestamp even for incomplete plans.
5. Save an untouched submitted workbook. Convert a copy with `human_to_plan.py`.
6. Score the converted plan with the same evaluator used for AI plans. Record conversion or schema failures as failed runs rather than repairing the participant's decisions.

## Cost

Human cost equals elapsed hours multiplied by the documented hourly rate. Ajey must choose and cite the wage source before sessions; this repository does not invent a rate. Record the currency, publication or URL, publication date, role/geography, and any conversion method. Keep the same assumption for every participant unless the final report explains a justified change.

## Data handling

- Ajey retains the answer keys and performs scoring after submission.
- Jisfaaz and Shazan must not receive answer keys until all assigned sessions are complete.
- Report timeouts, missing sheets, conversion errors, and incomplete plans as measured limitations.
- Do not edit submitted workbooks after timing ends.

## Limitations to report

This baseline uses two team members and six synthetic bundles. They are familiar with the project but are not a representative sample of professional production planners.
