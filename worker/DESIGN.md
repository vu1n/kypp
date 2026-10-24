---
name: kypp
description: The owner's gate pages for a self-hosted agent memory server
colors:
  ground: "light-dark(#f5f7f6, #0f1412)"
  raised: "light-dark(#fff, #161d1a)"
  ink: "light-dark(#16201b, #e3eae6)"
  muted: "light-dark(#4b5952, #a1afa8)"
  line: "light-dark(#cfd8d3, #2c3833)"
  field: "light-dark(#7a8781, #66756d)"
  accent: "light-dark(#0f6a50, #5ec8a4)"
  on-accent: "light-dark(#fff, #03201a)"
  danger: "light-dark(#b1261d, #ff9187)"
  note: "light-dark(#fff4d6, #2a2210)"
  note-line: "light-dark(#d7a33a, #806423)"
  note-ink: "light-dark(#4a3300, #f2d894)"
typography:
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "16px"
    lineHeight: 1.5
  headline:
    fontFamily: "system-ui"
    fontSize: "1.375rem"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.015em"
  code:
    fontFamily: "ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "2rem"
    fontWeight: 600
    letterSpacing: "0.14em"
  target:
    fontFamily: "ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "1.5rem"
    fontWeight: 600
  label:
    fontFamily: "system-ui"
    fontSize: "1rem"
    fontWeight: 600
rounded:
  control: "8px"
  panel: "10px"
spacing:
  gutter: "1rem"
  group: "1.25rem"
  section: "1.75rem"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.control}"
    padding: "0.5rem 1.25rem"
    height: "44px"
  button-secondary:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0.5rem 1.25rem"
    height: "44px"
  input:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    height: "44px"
  code-block:
    backgroundColor: "{colors.raised}"
    typography: "{typography.code}"
    rounded: "{rounded.panel}"
---

# Design System: kypp

## Overview

**The Gatehouse Ledger.** Every page is a gate the owner passes on the way back to another app, so
each one is a short, quiet document that puts the decision and the facts the owner can check above
everything else. Operate mode: system type, native controls, one accent, no ornament. The source of
truth is `src/page.ts` (`STYLE`, `render`, `htmlResponse`); consent (`src/index.ts`) and pairing
(`src/pairing.ts`) only write the main column.

## Colors

Restrained, light and dark from `color-scheme: light dark` and `light-dark()`; the choice follows
the owner's system, since these pages open from whatever app they were in.

### Primary
- **Gate green** (`accent`): the approving button, links, focus rings, radios, selection. Never decoration.

### Neutral
- **Ground** and **Raised**: a faintly green-grey page with white (or lifted dark) panels for facts, the code, choices and fields.
- **Ink** and **Muted**: text, and secondary text (dates, hints, fact labels, the instance host). Muted
  stays ≥6.8:1. A page's main message is always ink, even when it says there is nothing to show.
- **Line** for panel borders; **Field** (≥3:1) for the passphrase field's border.

### Named Rules
- **Danger** is for errors and Unlink only. **Note** (amber) is for warnings the owner must read
  before acting, such as access going to a local app. A success note tints from Gate green; a status
  note (nothing to choose, so approving does the default) is raised and neutral.
- Outcome icons take their meaning's colour: the check is Gate green, the stop is Danger, and x,
  clock and info are Muted. Two pages that report the same kind of outcome look the same.

## Typography

One family, `system-ui`. The h1 is a fixed 1.375rem at weight 650, balanced, and asks the page's
question. On pairing the code is the one large element: 2rem monospace, tracked, in its own panel,
spelled out to screen readers. On consent the redirect host is: 1.5rem monospace in the facts panel,
larger than the h1's self-asserted name. Hostnames are monospace because they are data.

## Layout

One column, `max-width: 34rem`, 1rem gutters. A header row names kypp and this install's host, under
a hairline. Spacing grows outward: 0.375rem label to field, 1rem between paragraphs, 1.25rem before
actions, 1.75rem before a new form section.

## Elevation & Depth

Flat. Panels separate by a 1px line and the raised tone; nothing casts a shadow.

## Shapes

8px corners on controls, 10px on panels. Borders are 1px all round; no side stripes.

## Components

### Buttons
Actions sit in a wrapping flex row with 0.75rem gaps, every button at least 44px tall. The approving
action comes first and is the only filled button (Enter submits it). Deny is the outlined secondary
and skips validation. Rare paths ("Continue an earlier link…") are underlined text buttons below the row.

### Inputs / Fields
One field, "Owner passphrase": a block label above a full-width 44px input. Every refusal is shown
directly under the field (`passphrase({ error })` in `src/page.ts`), never at the top of the form:
the field is `aria-invalid`, points at the error line (`role="alert"`, drawn icon) and takes focus.
A muted hint can follow ("Enter the passphrase again to approve.").

### Facts
Consent states what can be checked as a stacked list on one raised panel: a muted label over each
value, divided by a hairline. "Access goes to" (the host, large monospace) comes first, then what it
allows. Stacking keeps it the same at 390px and 1440px.

### Choice lists
Radio rows inside one bordered panel, divided by hairlines, label first and a muted detail line below.

### Icons
Drawn inline SVG, 20px, 2px round strokes: warning, alert, stop, check, x, clock, info. Each icon
carries its tone class (`i-ok`, `i-bad`, `i-idle`); notes and error lines colour theirs from the text.

### Outcome pages
A drawn icon and an h1 that names what happened, one sentence of what it means, and a muted next step.
Dead ends a person can reach from a link (a sign-in that can't continue, a cut-off pairing link) are
pages like these, never plain text or JSON.

## Do's and Don'ts

### Do:
- Make the verifiable fact (the code, the redirect host) more prominent than the self-asserted name, and quote the name.
- Re-render a refused form in place with the error; never send a plain-text dead end.
- Name what happened on every outcome page, and give each page its own `<title>`.

### Don't:
- Load fonts, scripts or images from anywhere; the pages are single strings.
- Add a second accent, shadows, or a card per paragraph.
- Autofocus the passphrase before the owner has had a chance to check the code.
