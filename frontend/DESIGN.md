# TeleReason design

Who reads these screens: maintenance technicians and shift leads at the start of a shift, often on a workshop monitor, and judges watching a 1080p screen recording. Every screen has one job: say what to look at first and, just as clearly, what the data cannot support.

The look is a dark, monochrome operations console: thin rules, corner brackets, technical labels in a monospaced face, and large, tight headings and numbers. Colour is rare and always means something. The reference mockups (`frontend/design/report.png`, `frontend/design/window.png`) show the intended result; every number in them is a placeholder. This file is the rulebook behind them.

## Principles

1. **Calm means nothing to act on.** A healthy screen is black, white and gray. Colour appears only when something needs attention or a state must be read.
2. **Colour means something, or it is not used.** Four meanings, listed under Tokens. No decorative colour, gradients or glow.
3. **Never colour the thing, mark it.** A window, row, signal or robot joint is not recoloured. A separate mark sits on or next to it: corner brackets, a status dot with a word, a triangle. The meaning survives for colour-blind viewers and in a compressed video.
4. **Show values against their normal range.** A number alone is hard to judge. Wherever a value has a normal range, draw the range and place the value on it.
5. **Say what is not known.** Values that were not measured are shown as "Not measured" with a dotted outline, never as zero and never left blank.
6. **One memorable element per screen.** On the morning report it is the gate verdict; on the window page it is the robot arm with the look-first joint marked. Everything else stays quiet.

## Display hierarchy

| Level | Screen | Question it answers |
|---|---|---|
| 1 Overview | Morning report | What happened last night, and where do I look first? |
| 2 Line | Gate | What can TeleReason say about each line, and why? |
| 3 Detail | Window page: robot arm, facts, signal chart, note | Why was this window flagged, in which signal and joint, by how much? |
| 4 Support | Assistant, AMD panel | Ask a question; see how the night ran on the hardware |

One click goes down one level (report row → window). The browser's back button and the breadcrumb go back up.

## Tokens

Define these once (CSS variables or Tailwind theme, one file) and use only these names in components.

| Token | Hex | Use |
|---|---|---|
| `bg` | `#111212` | Page background |
| `panel` | `#18191A` | Panels |
| `raised` | `#202122` | Chips, focused rows, normal-range bands |
| `line` | `#343637` | Panel borders, row dividers |
| `line-strong` | `#5B5E5F` | Neutral corner brackets, scale ticks, unfilled meter segments |
| `text` | `#EEEEEA` | Headings, values, signal lines |
| `muted` | `#A2A5A3` | Secondary text, labels, axis text |
| `dim` | `#6C706E` | Non-text only (minor ticks) |

| Meaning | Token | Hex | Used for |
|---|---|---|---|
| Facts only (verdict `closed`) | `closed` | `#EEEEEA` | Status dot, the AUROC band outline |
| Attention (verdict `screening`, flagged, outside normal range) | `screening` | `#E9A23B` | Status dot, score meter fill, triangles, look-first brackets, the marked robot joint |
| Diagnosis available (verdict `open`), engine ready | `open` | `#4CC27A` | Status dot |
| Failure (API error, failed night run, engine not ready) | `fault` | `#E5484D` | Status dot, square failure mark |

Rules:

- **Text is `text` or `muted`, never a meaning colour,** except the one-line label inside a look-first callout. Measured on `bg`: `text` 16.1:1, `muted` 7.6:1; on `raised`: 13.9:1 and 6.5:1.
- Meaning colours mark: dots, triangles, brackets, meter fills, the robot joint. `screening` is 7.5:1 or more on every surface; `fault` 4.1:1 or more.
- `dim`, `line` and `line-strong` never carry text.
- No light theme. One theme keeps every screen and the video consistent.

## Type

Two families:

- **Plus Jakarta Sans** for headings, body text and numbers. Headings and large numbers are tight: weight 600–700, letter-spacing −0.02 to −0.03 em. Numbers use tabular figures.
- **IBM Plex Mono** for anything a technician matches against the controller or reads as a label: window ids (`W-003114`), signal names (`setpoint_acc_5`), status chips, small section labels, navigation, axis labels.

Self-host both through the Fontsource npm packages (`@fontsource/plus-jakarta-sans`, `@fontsource/ibm-plex-mono`); no font CDN.

Scale for a 1920×1080 recording:

| Use | Size |
|---|---|
| Page title | 48px, 700 |
| Gate verdict statement | 34px, 700 |
| Panel title | 24px, 600 |
| Stat number | 56px, 600 |
| Body, notes | 18–19px |
| Table text | 17px |
| Mono identifiers | 16–17px |
| Mono labels and chips | 13px, uppercase, letter-spacing 0.08 em |

Nothing smaller than 12px. Uppercase only in mono labels and chips; headings and sentences are sentence case.

## Layout

- 8px grid; page margin 40px; gap between panels 24px.
- Panels: 1px `line` border on `panel`, square corners, no shadows.
- Corner brackets (14px arms, 1.5px) mark the frame of the app bar and the one panel or row that matters on the screen. Neutral brackets are `line-strong`; look-first brackets are `screening`.
- Content is left-aligned, max width 1840px at 1920.
- App bar: product name left, navigation centre (mono, uppercase, underline on the active page), last night's run time and the engine chip right.

Morning report: page title and one-line summary; gate panel on the left (both lines and a status legend); four stat panels across the top right; "Look first" (AURSAD) and "Largest deviations" (FactoryWave) below them.

Window page: breadcrumb (`REPORT / AURSAD / W-003114`), title, status chips; robot arm panel and signal chart on the left; note, facts table and "Ask about this window" on the right.

## Components

**Status chip.** Mono, uppercase, on `raised`: a dot in the meaning colour and a word ("● SCREENING", "● FACTS ONLY", "● ENGINE READY"). The word is required; the dot alone never carries the state.

**Gate verdict.** Line name (mono label), status chip, then the verdict as a plain statement ("No diagnosis on this line", "Unusual behaviour only") and one sentence from the API's `explanation`. Below: the chance-line scale.

**Chance-line scale.** A 0 to 1 scale with ticks every 0.1 and a dashed `text` line at 0.5 labelled "CHANCE". The AUROC interval is a band: outlined in `closed` for a facts-only line, filled with `screening` for a screening line. When the band crosses the dashed line, the picture says "no better than chance".

**Stat panel.** Mono label, large number with a small unit, one muted line of context, and a segmented meter. Segments are 3px bars with 4px gaps; filled segments are `text` (or `screening` for flagged counts), unfilled are `line-strong`.

**Report row.** Window id (mono, muted), look-first signal (mono), score as a segmented meter in `screening` with its value, the note (or "Facts only…" in `muted` when `verified` is false), and a "VERIFIED" or "FACTS ONLY" chip. The row is never coloured. The top row gets `screening` corner brackets. The whole row opens the window page.

**Normal-range scale.** A line with the normal range as a `raised` band between two `muted` ticks. Inside the range the value is a `text` tick; outside it, the value becomes a `screening` triangle. The plain reading sits next to it ("Clearly off normal, 3.7 standard deviations above").

**Facts table.** Signal, window mean, normal mean, normal-range scale, reading. Numbers right-aligned, tabular. The look-first row sits on `raised`.

**Signal chart.** Normal band as a `raised` area between dashed `line-strong` edges labelled HIGH and LOW; the signal line in `text`; segments outside the band in `screening`, slightly thicker. At most four horizontal guides; axis labels in mono.

**Robot arm (3D, window page).** A line drawing of the robot cell: each part a dark fill with a light outline, on a faint floor grid, at a fixed three-quarter view. The look-first joint is outlined in `screening` with a bracketed marker, and a dashed `screening` leader line connects it to a callout: "LOOK FIRST: WRIST 3", the signal name, and the plain reading. Rules:

- Build with three.js (React Three Fiber) from the robot's URDF through `urdf-loaders`. Joint angles are fixed for the demo pose; the mapping from a signal's index to a joint comes from the data dictionary and is checked before it is shown.
- The joint is marked, not recoloured from the inside out: outline, marker and callout only.
- Only for lines whose robot model and joint mapping are known (AURSAD, UR3e). Other lines show the facts table and chart without the arm.
- Fallback: a static render of the same view as an image with the marker drawn over it, if WebGL is unavailable or time runs short. The interactive version (rotate, zoom) is optional and comes last.
- Check the licence of the robot's mesh files before adding them to the repository, and credit them in the README.

**Step log (assistant).** Tool steps as a numbered list (they are a real sequence): tool name in mono, a one-line result, duration ("Not measured" when the API sends `null`). Steps appear as they stream in and collapse after the answer. Answers carry a "FROM TOOL" or "FROM MODEL" chip. A refused action is shown as the assistant's plain answer, not as an error.

**Night timeline (AMD panel).** Gate, facts, notes and report as four nodes joined by dotted lines, each with its duration. Done steps get an `open` dot, the running step a segmented progress meter, queued steps a `line-strong` outline, a failed step a `fault` dot and the word "Failed". Next to GPU memory in use: "vLLM reserves 85% of GPU memory for its cache."

**States.** Every view has loading (rows of `raised` blocks), empty and error states. Before the assistant's first step arrives, show "Reading the window…" rather than a spinner alone; the wait can reach 10 s.

**Motion.** None on page load. Only the step log streams in, and expandable parts open on request. Respect `prefers-reduced-motion`.

## Words

- Technician language, active verbs: "Open window", "Ask", "Start a small night".
- One name per thing on every screen: "window", "line", "look first", "facts only".
- Errors say what happened and what to do: "Can't reach the engine. It may still be starting; this takes about three minutes after launch."
- Empty states invite the next step: "No windows were flagged on this line last night."
- Never on any screen: a fault name on a `closed` line, a cause on `screening`, a number the API did not send, speed comparisons between GPUs.

## References

Principles and mood only; no product's visuals, icons or layouts are copied.

- ISA-101 high-performance HMI practice (Control.com, "Going Gray"; Inductive Automation, "High Performance HMI Techniques"): colour only for abnormal states, mark instead of recolouring, analog indicators against the normal range.
- Dark monitoring-console dashboards (corner brackets, monospaced labels, status chips with dots) and modern finance dashboards (large, tight headings and numbers), collected by the team as mood references.
- Universal Robots ROS description packages (URDF) and `urdf-loaders` for the robot arm; licences to be checked before use.
