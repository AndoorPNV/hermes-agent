---
name: training-program-design
description: "Plan the user's 4-week training block."
version: 1.0.0
author: Nous Research
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [fitness, training, strength, hypertrophy, spreadsheet, google-sheets]
---

# Training program design

Building and revising this user's training blocks. He runs a 4-week block, logs it in a
spreadsheet, reports results, and expects the next block to start from those numbers.
All plan text, exercise names and notes are in Russian.

## When to Use

Triggers: "составь план тренировки", next 4-week block, revising loads after a reported result,
adding a day or swapping exercises because something hurts, or moving the plan into the user's
spreadsheet (Google Sheets tab or `.xlsx`).

## Who this is for

- Full-body training ~3×/week, body weight ~87 kg, wants every muscle group worked each week.
- Knee: discomfort after leg days; Bulgarian split squat is punishing, not just hard.
- Shoulder: discomfort after bench that makes pulldowns and rows feel bad in the same session.
- Goal is recomposition at a stable ~87 kg: hypertrophy + strength everywhere, cardio only as insurance
  against fat gain. Do not prescribe weekend cardio — he wants it inside the leg day. Put one LISS
  row (~12–18 min, pulse 110–130, bike or incline treadmill) at the end of day A, and accept that
  day A becomes the long session (~80 min with warm-up). Keep B and C near 60 min.
- Volume floor for his hypertrophy goal: ~65 sets/week (biceps ≥5, side delts ≥5, calves 3, core 6).
  Below that the plan is a strength plan with accessory work, and he says so.
- Arms: never ship 3 direct sets per week. Target 6–10 direct sets each for biceps and triceps
  on top of the indirect work from rows, pulldowns and presses. He reads the arm rows first and
  asks why they are so few — one 3-set superset on a single upper day is exactly what he flags.
  In an upper/lower split that means arm work on **both** upper days, the second one lighter.
- Nutrition is not tracked: he takes creatine, vitamin D3, ZMA, beta-alanine and protein powder
  precisely because he suspects he misses the daily protein target. Recomposition advice therefore
  has to be concrete and short: protein 1,6–2,0 g/kg = 140–175 g/day, calories ~2600–2700 (small
  deficit 200–300), creatine 5 g daily (expect +1–1,5 kg water — scale weight is a bad metric),
  beta-alanine 3,2–6,4 g/day split, D3 2000–4000 IU with fat, ZMA optional. Success looks like
  flat weight, waist −1–2 cm per 4 weeks and +2,5–5 kg on the bench, not a falling scale.
- Supplies end-of-block numbers himself (1RM test, working sets, body weight). Those reported
  working weights, not percentages of a single, are the anchor for the next block.

## Deliverable format — use exactly this

Columns: `Неделя | День | Упражнение | Подходы | Повторы | Вес (кг) | Отдых между подходами | Время на упражнение | Заметки ИИ | Мои комментарии`

- 3 days, labelled A/B/C (Пн/Ср/Пт), **6 strength exercises per day**; a 7th row is allowed only
  for calves or the LISS block, and only on the leg day. That matches his existing `Тренировка`
  sheet and keeps a session at ~50–65 min with warm-up; stacking two more barbell movements
  pushes the day past 70 min and the plan stops being followed.
- Week number only in the first row of the week; day letter only in the first row of the day;
  then one `Итого` row per day whose time cell reads `~N мин + разминка M мин = K мин`.
- Weights explicit **per week** (comma decimals, `72,5`), never "+2,5 кг к прошлой неделе" as a
  substitute for a number. Reps as waves when they change (`10/10/8/8`). `—` for bodyweight/time work.
- `Заметки ИИ` carries one concrete cue or progression instruction per row — not prose.
  It is read mid-set on a phone.
- Leave `Мои комментарии` empty: he fills it with what actually happened, and that column is
  the input to the next block.
- Chat answer: one table per week, then a short block on red flags. Also ship the same data as
  `.xlsx` (filters, frozen header) and as a tab-separated file saved `utf-8-sig` for one-paste
  import into Google Sheets.

## Programming rules

1. **Pull before press** on any day containing barbell bench, and never pair heavy bench with
   heavy vertical pulling in the same session — that is exactly the pattern that leaves his
   shoulder sore for the rest of the workout.
2. 4-week wave: W1 re-introduction (RPE 6,5–7, ~90–95% of last block's working weights),
   W2 accumulation (RPE 7,5), W3 peak (RPE 8–8,5), W4 volume −40% + retest on day C.
   Leave 1–2 reps in reserve everywhere except tests.
3. Derive loads from the reported working sets and sanity-check with Epley
   (`weight × (1 + reps/30)`): 80×6 ≈ 96 kg e1RM, which is why a "90 felt easy, could have done
   100" single is consistent — plan off the working set, not the single.
4. Retests happen with a spotter or rack pins, and the plan must say so in the test row. He
   tested 1RM alone and stopped out of caution; the instruction "без страхующего выше 90 не идти"
   belongs in the cell.
5. Every prescribed range must be pain-free. If an exercise only works in a shortened range,
   write that range into the note — it is a programming constraint, not a discipline problem.

## Other splits, and defending the program

He asks conceptual questions between blocks ("какие виды тренировок бывают кроме фуллбоди",
"на чём основана эта тренировка", "почему так мало"). Answer with the actual numbers from his
sheet, not with principles:

- Name the mechanism behind every choice: ~10–20 sets per muscle per week, frequency 2–3×,
  5–6 reps on main lifts with 2,5–3 min rests for strength, 8–15 on accessories, 1–2 reps in
  reserve, 4-week accumulation + deload + retest. Epley as the sanity check on his reported sets.
- Then give an honest table of **his** sets per muscle per week and say which muscles are
  underdosed. If a muscle is low by design (quads, because of the knee), say that out loud
  instead of defending it. He accepts limitations stated plainly and dislikes both padding and
  silent gaps.
- When he asks "почему так мало" he is usually right: fix the sheet in the same turn and report
  what changed, do not explain why the number was fine.

Split catalogue for this user, in the order worth offering: full body A/B/C (default at 3 days),
upper/lower (4 days normally), push/pull/legs (4–6), body-part bro split (5–6), push-pull (4),
A/B full body (2), then the method axis — linear, undulating, block periodization, 5×5 / 5/3/1,
powerbuilding, circuits/metabolic, HIIT, functional, isometrics for rehab.

Upper/lower on **3 days** is inherently lopsided — one half of the body gets two sessions, the
other one. If he asks for it anyway, build it as an alternating cycle: week 1 Верх A / Низ A /
Верх B, week 2 Низ B / Верх A / Низ A, and so on, naming the days Верх A, Верх B, Низ A, Низ B
in the `День` column. Flag in the reply that weekly volume per muscle oscillates and that the
split only becomes even at 4 days. Put it on a separate tab rather than replacing the full-body
plan, and keep the retest in the week-4 upper day.

## Injury-aware selection

Knee — drop: Bulgarian split squat, long-step lunges, deep squats, heavy leg extensions with a
bounce. Keep: leg press (3-1-1 tempo, no lockout, ROM to first discomfort), single-leg leg press,
hip thrust, leg curls, Romanian deadlift, trap-bar deadlift.

Shoulder — neutral-grip dumbbell press and paused bench carry part of the barbell volume;
pulldowns only neutral/parallel grip; chest-supported or single-arm supported row when it is
grumpy; face pulls and band external rotations are scheduled work every week, plus 5 min daily
prehab.

Red flags — say it plainly and refer out, do not silently program around it: pain >3/10,
night pain, swelling, morning stiffness >30 min, clicking with pain, weakness or giving way.

Full substitution table, cues and prehab doses: `references/exercise-substitutions.md`.

## Build it programmatically

One Python data structure generates the chat tables, the `.xlsx` and the `.tsv`; hand-writing any
one of them guarantees they drift apart.

- `openpyxl`: if the Hermes venv has no pip (`No module named pip`), run
  `python -m ensurepip --upgrade` once, then `python -m pip install openpyxl`.
- Writing into the user's Google Sheet needs real API access, not the browser: the anonymous
  browser session is read-only (clicking add-sheet silently does nothing, and the export URL
  redirects). Confirm write access with the API before planning an upload.
  Reading an existing tab's conventions still works anonymously:
  `curl -sL "https://docs.google.com/spreadsheets/d/<ID>/export?format=csv&gid=0"`.
- Upload recipe that works here (token at `~/AppData/Local/hermes/google_token.json`):
  `batchUpdate {addSheet: {title, index: 1}}`, then `values.update` over `A1:J<n>` with
  `USER_ENTERED`, then one `batchUpdate` for header fill, frozen rows/cols, column widths,
  week banding and bold `Итого`. Write numbers as numbers, prefix every other non-empty cell
  with `'` so `10/10/8/8`, `5/3/1/1/1` and `40→60→…` stay text instead of turning into dates
  or divisions. The sheet's locale is `ru_RU`, so `72,5` lands as the float 72.5 — verify with
  `valueRenderOption=UNFORMATTED_VALUE`.
- This `setup.py` build has no `--services/--format` flags: `--auth-url` alone, then
  `--auth-code "<full localhost:1 URL>"`. The consent link must be pasted into a real browser,
  not opened in the desktop chat preview — that webview has no JavaScript and Google answers
  with «Не удалось войти в аккаунт».
- Keep **one reusable uploader** and call it per tab: `python gsheet_put.py <file.tsv> "<tab name>"`
  — it creates the tab if missing, clears it if present, writes `USER_ENTERED`, reapplies header
  fill / frozen rows+cols / column widths / week banding / bold `Итого`, then reads the tab back.
  Copy the tab title exactly as he names it (`Тренировка (2 вар)`, `Верх-Низ (3 дня)`).
- His spreadsheet is `19ZqVFd5UKF3mpUBB_xC-1Zbwa3As9ov0pg2iFRox8Ac`; tabs as of now:
  `Тренировка (1 вар)`, `Замеры`, `Тренировка Лара`, `Упражнения`, plus the plan tabs we added.
  New plan variants go on a new tab — never overwrite `Тренировка (1 вар)`, it is his own log.
- Verify before delivering: total rows = 4 × (18 exercises + 3 `Итого`) plus test rows, every row
  the same width, and spot-check the first and last week. Print counts, don't eyeball.
- Google trims trailing empty cells, so a read-back comes with widths like `{8, 9, 10}` and
  `Итого` rows look like `['', '', 'Итого']` — that is normal, not a truncated write. Read a
  window that includes the time column (`A1:J10`) before concluding anything is missing.

## Pitfalls

- **Row field-shape mismatch silently shifts columns.** A test row holding one string where
  ordinary rows hold a 4-element per-week list indexes to a single character, and the weight
  cell ends up showing `6` instead of `40→60→75→87,5→92,5–95`. Normalize (`weights[wi] if
  isinstance(weights, list) else weights`) before writing.
- **A one-element marker tuple must keep its comma.** `("__TOTAL__")` is a string, so
  `item[0]` is `_` and the row unpacks into characters — write `("__TOTAL__",)`.
- Don't collapse the four weeks into one "weights" cell to save space; he reads the plan week by
  week and edits the comment column per session.
- Don't restate the block as a narrative list of principles. The table *is* the deliverable.
