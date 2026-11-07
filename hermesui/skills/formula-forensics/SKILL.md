---
name: formula-forensics
description: Derive a scoring formula from screenshots or raw data.
---

# Formula forensics

Use when a user shows a leaderboard, tracker or score sheet and asks "how is this
computed", "check their math", or "grade my contribution". The deliverable is a
reconstructed formula, verified to zero residual on real rows, plus a breakdown of
which lever earns the points and what each lever costs.

## Procedure

1. **Get the numbers, not the impression.** Read the artifact cell by cell (see
   screenshot pitfall below) and write the rows into a table you can compute over.
2. **Propose the formula from domain constants first.** Most community sheets are
   `sum(metric_i x weight_i)` over 3-4 metrics plus a goal expressed as a multiple
   of the player's starting stat. Read the goal column against the power column
   early: an exact ratio (e.g. goal = 2 x base power) identifies the rule instantly.
3. **Verify on EVERY visible row, not one.** Compute the score for all rows and
   require zero residual. A formula that matches 5 of 6 rows is wrong; the mismatch
   is usually an OCR misread, so re-read that cell before believing a different rule.
4. **Decompose.** Report each metric's share of the total. The useful sentence is
   "you cleared the bar on kills alone at 100.1%, the margin came from deaths" —
   not "your score is 159M".
5. **Price each lever.** Convert every scoring lever into unit cost (resources,
   time, gold) so the user can see which way of scoring is cheapest. Compare against
   their actual blended cost per point.
6. **Show sensitivity to the rule.** Recompute the same performance under the other
   weightings in use in the community. The point: a percentage is a policy, not a
   fact, and the user must know that before arguing with officers.
7. **Name what did not fit.** If a column resists ~10-15 hypotheses, say it plainly,
   give the best residual, and keep it out of the score rather than inventing a
   definition.

## Pitfalls

- **Big screenshots time out in vision analysis.** Crop the region and upscale 2-3x
   with PIL into the scratch dir, then analyze the saved crop. Whole-image analysis
   succeeds where a native `region` crop times out.
- **Never trust one OCR pass on digits.** 3 vs 8, 83 vs 85 silently poison the fit.
   The proof of a formula is exact agreement across several rows, so re-read any row
   whose residual is non-zero and prefer arithmetic cross-checks (weights x counts
   must equal the published total) over visual reading.
- **Do not mix scoring scales.** The same game/profile usually has 2-3 different
   point systems (per-unit stats, event-board points, season currency). Mixing them
   produces a ratio that looks plausible and is meaningless. State which scale each
   number is on, every time.
- **Underdetermined systems have a reportable invariant.** With more unknowns than
   equations (4 resource totals + 1 point total vs 6 troop types), do Gauss with
   `fractions.Fraction` on the augmented matrix, find the null space, and check
   whether some *aggregate* is constant across all free parameters. If yes, that
   aggregate is the honest answer; the per-category split is not.
- **No numpy in the execute_code sandbox.** Use stdlib: `Fraction` plus a hand-rolled
   Gauss elimination or 2x2 normal equations. Normal equations on a rank-deficient
   matrix are singular — parametrize instead of pseudo-inverting.

## Reporting (this user)

- Answer in Russian; tables for numbers, prose only for verdicts.
- Every constant carries a source (patch note, guide site, community calculator).
- State the caveat where it changes the number (weighting, scale, snapshot timing),
  not as a disclaimer at the end.
- Close with the 2-3 cheapest levers with numbers attached, and ask for the missing
  inputs only when the score genuinely cannot be computed without them.

## Reference

- `references/rise-of-kingdoms-kvk.md` — RoK constants: kill-point scales, board
  weights, honor rates, Acclaim, healing/training costs, DKP weightings in the wild.
