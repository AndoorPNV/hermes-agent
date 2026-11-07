# Rise of Kingdoms — KvK scoring constants

Reusable numbers for grading a governor's KvK output. Verify against a current patch
note or calculator before quoting; the patch level matters (Acclaim appeared in 1.1.12).

## Three point scales that must never be mixed

| Scale | T1 | T2 | T3 | T4 | T5 |
|---|---|---|---|---|---|
| Profile kill points | 0.2 | 2 | 4 | 10 | 20 |
| KvK board kill points / death points | — | — | — | 1 | 5 |
| Healing points (observed) | — | — | — | 1 | 5 |

A governor's board number is 4-5x smaller than the profile number for the same fight.
Board death points accrue from severely wounding/killing enemy-camp units and stop
when the Great Ziggurat opens.

## Honor (Honor Roll) — individual / alliance / kingdom rankings

Gather node 3 · barb 26-30 5 · 31-35 8 · 36-40 10 · fort 6/7/8/9/10 = 15/25/35/45/60 ·
Ancient Ruin 15/min held · Dark Altar 75/min held. One hour on an altar beats a night
of barb farming; honor is a farming metric and a poor war proxy.

## Acclaim (RU: «очки доблести»)

Earned only from PvP in the Lost Kingdom against other kingdoms (KvK 1-2) or other
camps (season 3 "King of All Britain"). Converts to acclaim coins at an undisclosed
rate; the Acclaim Shop sells healing potions with per-period purchase caps, and
unspent coins are reclaimed at season end. The per-kill rate is not published —
derive it from a governor's own (kills, acclaim) pair instead of quoting a formula.
Observed: ~1 acclaim per 72 profile KP, ~0.27 per killed unit.

## Training / healing cost per unit (food, wood, stone, gold)

| | T4 | T5 |
|---|---|---|
| Infantry train | 300 / 300 / – / 20 | 800 / 800 / – / 40 |
| Cavalry train | 300 / – / 225 / 20 | 800 / – / 800 / 600 |
| Archer train | – / 300 / 225 / 20 | – / 800 / 800 / 600 |
| Infantry heal | 120 / 120 / – / 8 | 320 / 320 / – / 160 |
| Cavalry heal | 120 / – / 90 / 8 | 320 / – / 240 / 160 |
| Archer heal | – / 120 / 90 / 8 | – / 320 / 240 / 160 |

T5 heals ~3.2x the resources of T4 per unit and 20x the gold. Heuristic for a low
spender: T5 in open field only where it out-trades, T4 for garrison/rally/flags where
troops die outright — deaths need no healing.

## Kingdom DKP weightings seen in the wild

- `T4x10 + T5x20 + deathsx30`, goal = 2 x pre-KvK power, `Reached = DKP/Goal`. Shape
  used by the AK19-family sheets; reproduces to the digit across every row.
- `T4x1 + T5x2 + deathsx4`, goal = 30% of pre-KvK power (rok-vault default).
- `T4x10 + T5x20 + deathsx50` (kd2708 style).
- `deaths x 8 + kills x 1` with separate death/KP requirements (roksteward).

Same governor reads 72% / 106% / 110% of goal under three of these — show the spread
before agreeing that someone "missed the norm".

Trade Ratio columns in such sheets often do not follow from the other columns; check
the fit before using them.
