# Model comparison log: DeepSeek v4.1 vs Gemma 4 (vs Gemma 5, pending)

Purpose: record how the `fa` and `venus` Hermes profiles behave on different models,
so we can re-test when Gemma 5 is released.

## Setup history
- Through Nov 11: `fa` and `venus` on DeepSeek v4.1.
- Nov 18: `fa` and `venus` switched to Gemma 4 (same model as `orbit`), to get a
  baseline for the Gemma 5 comparison.
- Next: when Gemma 5 is available, switch `fa` and `venus` (and optionally `orbit`) to it.

## Observed: DeepSeek v4.1 > Gemma 4

### FA agent (critical thinking)
- DeepSeek noticed poor recent performance (negative alpha) and reasoned about it.
  Advisors cited the trailing numbers, weighed them against the long-term record
  (since-inception +54.2%), and gave mixed answers (Nov 11: two of five declined).
- Gemma 4 (Nov 18): 3/3 YES, with short generic replies ("thanks for the invite,
  I'd love to catch the game"). No mention of performance, although YTD alpha was
  -4.3% and 6-month alpha -9.6%, the weakest numbers so far.
- Gemma 4 misread Venus's "I saw you went to <school>" as a shared affiliation
  ("fellow Titan").
- Target behavior: a realistic acceptance rate between 0% and 100% that visibly
  depends on the performance data the advisor was shown.

### Venus (writing quality)
- Gemma 4 emails follow the Week 1 rules (Jay hosts, no "Jay here", no "fellow ...")
  but are plainer than DeepSeek's: generic subject lines, and one email with no greeting.
- Debrief: Gemma 4 called a single $119K close on a $195M fund a "huge win";
  DeepSeek's debriefs were better calibrated.

## Not a clean comparison (confounders)
- The prompts changed between the DeepSeek and Gemma runs: full track record added to
  the FA context (Nov 3) and the Week 1 "Jay hosts" rewrite (Nov 11 to 18). DeepSeek's
  Nov 11 Venus emails were unclear about who attends; Gemma 4 plus the new prompt fixed
  that, but we cannot separate the model from the prompt.
- Different advisors and market data each week; samples are 3 to 5 advisors.
- For a fair Gemma 5 test, replay the same week's data (same advisors, same fund
  numbers) on DeepSeek, Gemma 4 and Gemma 5 where possible.

## Checklist for the Gemma 5 run
1. Does the FA mention negative alpha / recent underperformance in its reasoning or reply?
2. Is the acceptance rate between 0% and 100%, and does it move with the performance data?
3. Does the FA avoid misreading Venus's profile references ("fellow ...")?
4. Venus emails: greeting present, specific subject line, Jay hosts, no invented documents.
5. Venus debriefs: proportionate language for the size of the result.
6. Venus Week 2: honors the advisor's requested meeting type (call vs in person).
