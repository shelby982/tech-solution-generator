# 标书图示设计核心规范（产品精简版）

## 1. Philosophy
**The highest-quality move is usually deletion.**

Applied to schematics:

- Every node represents a distinct idea. Two nodes that always travel together are one node.
- Every connection carries information. If the relationship is obvious from layout, remove the line.
- Coral is **editorial, not a flag.** 1–2 focal nodes per diagram. Using it on 5 nodes erases the signal.
- The schematic isn't done when everything is added. It's done when nothing can be removed.

**Target density: 4/10.** Enough to be technically complete. Not so dense it needs a guide. Above 9 nodes, it's probably two diagrams.

---

## 4. Universal Anti-patterns
These mark "AI slop" schematics of any type:

| Anti-pattern | Why it fails |
|---|---|
| Dark mode + cyan/purple glow | Looks "technical" without design decisions |
| JetBrains Mono as blanket "dev" font | Mono is for *technical* content — ports, commands, URLs. Names go in Geist sans. |
| Identical boxes for every node | Erases hierarchy |
| Legend floating inside the diagram area | Collides with nodes |
| Arrow labels with no masking rect | Bleeds through the line |
| Vertical `writing-mode` text on arrows | Unreadable |
| 3 equal-width summary cards as default | Generic grid — vary widths |
| Shadow on any element | Shadows are out. Borders are in. |
| `rounded-2xl` on boxes | Max radius 6–10px or none |
| Coral on every "important" node | Coral is 1–2 editorial accents, not a signaling system |
| Reproducing Mermaid's renderer layout | Imports automatic spacing and routing instead of making an editorial layout |
| Any breach of the six §6 connector rules | Automatic fail: diagonal slants, labels touching their stroke, masks clipped by a later node, overlapping paths, shared attach points, transit behind a non-endpoint box |

Type-specific anti-patterns live in each type reference linked in the guide.

---

## 5. Design System
**The design system is skinnable.** [`references/style-guide.md`](references/style-guide.md) is the single source of truth for colors, typography, tokens, and the default palette; this file names semantic roles (`paper`, `ink`, `muted`, `accent`, `link`, …). To apply a brand, edit `style-guide.md` or run the URL-based flow in [`references/onboarding.md`](references/onboarding.md).

> When specs below or in type references mention "ink", "accent", "muted", etc., look up the current hex value in `style-guide.md`.

Type-reference snippets write roles as placeholders: `{ink}`, `{ink @ 0.40}` for a role at an opacity, and `{node-name}`, `{sublabel}`, `{eyebrow}`, `{arrow-label}`, `{title}`, `{callout}` for fonts. Resolve every placeholder against `style-guide.md` before emitting.

### Semantic roles (at a glance)

| Role | Purpose |
|---|---|
| `paper`, `paper-2` | Page bg and container bg |
| `ink` | Primary text / stroke |
| `muted`, `soft` | Secondary text, default arrows, sublabels |
| `rule`, `rule-solid` | Hairline borders |
| `accent`, `accent-tint` | 1–2 focal elements per diagram |
| `link` | HTTP/API calls, external arrows |

**Focal rule:** `accent` goes on 1–2 elements max. Everything else is `ink` / `muted` / `soft`. If you're tempted to accent 4 things, you haven't decided what's focal yet.

**Node treatments** (focal, backend/API/step, store/state, external/cloud, input/user, optional/async, security/boundary): fill and stroke per [style-guide.md § Node type → treatment](references/style-guide.md#node-type--treatment).

**Typography:** Instrument Serif for the H1 title and italic callouts, Geist sans 600 for node names, Geist Mono for sublabels, eyebrows, and arrow labels. Sizes, weights, and the font `<link>`: [style-guide.md § Typography](references/style-guide.md#typography); per-preset type ramp: [output-spec.md](references/output-spec.md). When the user asks for offline output, system fonts, or no Google Fonts, follow [style-guide.md § Font source](references/style-guide.md#font-source).

**Non-Latin labels** — extend the family: [Korean](references/style-guide.md#korean-labels), [Chinese](references/style-guide.md#traditional-chinese-labels), [Cyrillic](references/style-guide.md#cyrillic-labels).

**Mono is for technical content only** — never as a blanket "dev" font, and never JetBrains Mono.

---

## 6. Core SVG Primitives
Universal building blocks. Type-specialized primitives (lifeline, activation bar, region) live in the relevant type reference linked in the guide. Optional primitives:

- Editorial callouts → [primitive-annotation.md](references/primitive-annotation.md)
- Hand-drawn variant → [primitive-sketchy.md](references/primitive-sketchy.md)
- Icon set (laptop, server, DB, K8s, Docker, AWS, …) → [primitive-icons.md](references/primitive-icons.md). Browse the gallery at [`assets/icons.html`](assets/icons.html).
- Terminal / CLI-window variant → [primitive-terminal.md](references/primitive-terminal.md)
- Optional explanatory motion → [animation.md](references/animation.md)

Exact markup (background, dotted paper, markers, node box, arrow label, legend) and the long form of each connector rule: [`references/primitives-core.md`](references/primitives-core.md). The static templates (`template.html`, `template-dark.html`, `template-full.html`) already define the background and the `arrow`, `arrow-accent`, and `arrow-link` markers; `template-motion.html` defines only its own prefixed marker, so add the others from primitives-core.md when a motion diagram needs them.

- **Arrows:** `muted` by default, `accent` for the headline path, `link` for HTTP/API and external calls, dashed `5,4` for optional, passive, return, or async. Draw arrows before boxes so lines sit behind nodes.
- **Node box:** an opaque paper mask rect, then the styled box at `rx=6`, a rectangular type tag at `rx=2` (not a pill), the name in Geist 600, and a Geist Mono sublabel.

### Mandatory connector rules

Non-negotiable, and §9 checks each one. Full text and edge cases: [primitives-core.md § Mandatory connector rules](references/primitives-core.md#mandatory-connector-rules).

1. **Orthogonal only.** Connectors between off-axis nodes are rounded right-angle elbows at `r=8` (`r=6` minimum in tight layouts); a straight `<line>` only when both ends share x or y. Diagonals fail.
2. **Label gap.** Every arrow label (14 characters max, all caps, centered on its segment) sits on an opaque mask with a visible 6 to 10px gap from its stroke, beside vertical segments, never on the line.
3. **No overlaps.** No shared or stacked strokes: offset parallel routes by 12px or more, and use the bridge/hop at a single crossing.
4. **Fan attach points.** Connectors on one box edge each get their own point at `L * k / (N + 1)`, 12px or more apart (8px on very small boxes).
5. **No transit behind a non-endpoint box.** Reroute. Only when the box is geometrically unavoidable: dashed stroke (`4,3`), label at the visible end, no marker on the intervening box.
6. **Mask before node.** A label mask must not overlap a node drawn after it; badge masks fully inside a node and masks over earlier zones are fine. From a repository checkout, verify with `python3 <repo-root>/scripts/verify-geometry.py <file>`.

---

## 7. Layout & Spacing
Structural geometry sits on a 4px grid: node origins, widths, heights, gaps, and padding divide by 4. Type sizes follow the role ramp in [output-spec.md](references/output-spec.md), not the grid. Allowed values, the off-grid exceptions, and page layout: [`references/layout-budget.md`](references/layout-budget.md).

### Complexity budget (per diagram)

| Limit | Rule |
|---|---|
| Max nodes | 9 |
| Max arrows / transitions | 12 |
| Max coral elements | 2 |
| Max annotation callouts | 2 |
| Max motion (optional) | 8 steps, 12 marked items, 2 simultaneous items — see [animation.md](references/animation.md) |

Per-type limits (lifelines, lanes, series, bars, stages, and the rest): [layout-budget.md § Complexity budget](references/layout-budget.md#complexity-budget-per-diagram). Check your type's row before drawing.

If you exceed, split into two diagrams (overview + detail).

---

## 9. Pre-Output Checklist (Taste Gate)
Run before producing any diagram.

**Type fit:**

- [ ] If behavior matters, did I choose one semantic pattern before the visual type and load `semantic-patterns.md`?
- [ ] Right visual type for the layout? (§3 visual-type guide)
- [ ] Stated type, pattern, size preset, and planned cuts before drawing — confirmed, or assumptions noted? (§3)
- [ ] Would a table / paragraph do the same job? (If yes — don't draw.)
- [ ] Loaded the matching type reference linked in the visual-type guide?
- [ ] If this is an import — format, size, detail level, and audience set? `viewBox` and type ramp match the size preset? (§11, [output-spec.md §6](references/output-spec.md))
- [ ] If this is an import — fidelity ledger ready to report? (§11)

**Remove test:**

- [ ] Can I remove any node? (Would a reader still understand?)
- [ ] Can I merge any two nodes? (Do they always travel together?)
- [ ] Can I remove any arrow? (Is the relationship obvious from layout?)
- [ ] Can I remove any label? (Does color or shape already signal it?)

**Signal:**

- [ ] Coral used on ≤2 elements? If more, which actually deserve focal status?
- [ ] Legend covers every type used — and nothing extra?
- [ ] Within the type's complexity budget (§7)?

**Technical:**

- [ ] Diagram `<svg>` has `role="img"` and `aria-labelledby` resolving to its `<title>` and `<desc>`?
- [ ] `<title>` is the first child of `<svg>` (before `<defs>`) and both `<title>` and `<desc>` are filled in?
- [ ] `<title>` / `<desc>` IDs are prefixed for this diagram and variant — never bare `title` / `desc`?
- [ ] Arrows drawn before boxes?
- [ ] **§6 rule 1:** off-axis connectors are `r=8` elbows, no diagonal slants?
- [ ] **§6 rule 2:** a visible 6 to 10px gap between every label mask and its connector?
- [ ] **§6 rule 3:** no overlapping or stacked connectors; bridge/hop at crossings?
- [ ] **§6 rule 4:** a distinct attach point per connector on a shared edge, 12px or more apart, none hiding another?
- [ ] **§6 rule 5:** no transit behind a non-endpoint box, except the unavoidable case (dashed, label at the visible end)?
- [ ] **§6 rule 6:** no label mask overlapping a node drawn after it? (From a repository checkout, run `python3 <repo-root>/scripts/verify-geometry.py <file>`.)
- [ ] Every arrow label has an opaque `fill="{paper}"` rect behind it?
- [ ] Legend is a horizontal bottom strip, not floating?
- [ ] No vertical `writing-mode` text?
- [ ] `viewBox` expanded for the legend strip (~60px)?
- [ ] **`min-width` equals the viewBox width, and the SVG sits in a local `overflow-x: auto` wrapper? (Otherwise a phone scrolls the whole page — or an `overflow: hidden` ancestor clips the diagram with no scrollbar at all. See [output-spec.md](references/output-spec.md).)**
- [ ] Node origins, dimensions, gaps, padding on the 4px grid; type sizes on the role ramp?
- [ ] From the installed skill directory, did `python3 scripts/self_check.py <file>` pass? (Accessible-SVG contract, single-file safety, document completeness, content inside the viewBox, motion basics.)
- [ ] If animated, does the complete static/no-JS frame work, does reduced motion hide/disable playback, and is the controller copied verbatim from `assets/template-motion.html`? From a repository checkout, also run `python3 <repo-root>/scripts/verify-motion.py path/to/generated.html` plus the skin linter; from an installed skill, manually check print and static-query states on top of the self-check.

**Typography:**

- [ ] Brand match uses exact public families/weights, verified via `getComputedStyle`; fallbacks disclosed?
- [ ] Human-readable names in Geist sans, not Geist Mono?
- [ ] Technical sublabels (ports, commands, URLs) in Geist Mono?
- [ ] Page title in Instrument Serif?
- [ ] Annotation callouts (if any) in *italic* Instrument Serif? (see [primitive-annotation.md](references/primitive-annotation.md))
- [ ] No JetBrains Mono anywhere?

---

## 12. Output
Always produce a single self-contained `.html` file:

- Embedded CSS (no external except Google Fonts)
- Inline SVG (no external images)
- Static by default; minimal inline JavaScript only for explicit animation controls/state

Renders correctly in any modern browser. Motion-enabled output must render its complete meaning without JavaScript; under `prefers-reduced-motion: reduce` it shows the complete static frame and hides/disables playback controls.

### Accessible SVG contract

Every diagram is an accessible figure by default (long form: [primitives-core.md § Accessible SVG contract](references/primitives-core.md#accessible-svg-contract)):

1. `<svg>` carries `role="img"` and `aria-labelledby` naming its `<title>` and `<desc>`.
2. `<title>` is the first child of `<svg>`, before `<defs>`.
3. IDs are `<slug>-title` / `<slug>-desc`, the slug matching the file (`loop`, `loop-dark`, `loop-full`); never bare `title` / `desc`.
4. `<title>` is the subject's short name, roughly the page `<h1>`, 60 characters or fewer.
5. `<desc>` is one sentence about the content, not the geometry.
6. Decorative-only SVG, such as the glyphs in `assets/icons.html`, carries `aria-hidden="true"` instead.

### Exporting to PNG / SVG

When the user asks to export, save, rasterize, or convert a generated diagram to `.png` or `.svg`, load [`references/export.md`](references/export.md) and follow the procedure there. Run the packaged helpers `scripts/export_svg.py` and `scripts/export_png.py`; the reference owns their arguments and availability checks. Both formats deliver the diagram only (the `<svg>` node) — editorial wrappers like cards and headers are dropped by design. Export is **manual** — never produce export files unprompted.

For an imported diagram, pixel dimensions come from the `viewBox` × scale factor, so its size decision belongs to §11, not to export. For any diagram that needs an exact frame (an OG card or a slide image), see [`export.md` § Sizing the export](references/export.md).

## Tokens
### Semantic roles

Every token is referred to by **semantic role**, not by its hex value. Type references (`type-*.md`) and SKILL.md say `accent`, not `#bf4520`.

| Role | Purpose | Default (light) | Default (dark) |
|---|---|---|---|
| `paper` | Page background, default node fill | `#ffffff` (white) | `#ffffff` (white) |
| `paper-2` | Diagram container bg, secondary fill | `#f7f8fa` | `#f7f8fa` |
| `ink` | Primary text, primary stroke | `#1f2937` (slate-900) | `#1f2937` (slate-900) |
| `ink-strong` | High-contrast text on accent fills | `#111827` | `#111827` |
| `muted` | Secondary text, default arrow stroke | `#4b5563` (slate-600) | `#4b5563` (slate-600) |
| `soft` | Sublabels, boundary labels | `#6b7280` (slate-500) | `#6b7280` (slate-500) |
| `rule` | Hairline borders | `rgba(31,41,55,0.12)` | `rgba(31,41,55,0.12)` |
| `rule-solid` | Stronger borders, baselines | `#d1d5db` | `#d1d5db` |
| `accent` | Focal / 1–2 max per diagram | `#1d4ed8` (bid blue) | `#1d4ed8` (bid blue) |
| `accent-tint` | Fill for accent-bordered boxes | `rgba(29,78,216,0.08)` | `rgba(29,78,216,0.08)` |
| `link` | HTTP/API calls, external arrows | `#2563eb` | `#2563eb` |

> **产品口径（投标）:** 白底、深灰墨、单一品牌蓝 accent；不使用暖橙/糖果色。accent 只用于 1–2 个焦点（关键路径/核心系统）。所有文字与线条按下方对比度契约执行。

### Contrast contract

Every token must clear the threshold for each role it plays, against the background it actually sits on, in both skins. `scripts/verify-contrast.py` computes the WCAG 2.x ratio for every row above against the `paper` of its skin (`ink-strong` against the 0.85 `accent` fill it labels) and fails CI when one falls short.

| Use | Minimum vs. its actual background |
|---|---|
| Regular text, including any `soft`, `link`, or `accent` used as text | 4.5:1 |
| Large text (24px+, or 18.7px+ at weight 600+) | 3:1 |
| Essential non-text marks: lines, arrows, borders, focus outlines, data points | 3:1 against the adjacent color |
| Decorative rules and translucent tints (`rule`, `rule-solid`, `paper-2`, `accent-tint`, fills at opacity) | No minimum, but never the only carrier of meaning |

- **A token used in several roles must pass each one.** `accent` is a stroke in one place and a label in another, so it is held to 4.5:1, not the 3:1 its strokes alone would need. Don't assume a token is non-text because it usually is.
- **Translucent colors are composited first.** An `rgba()` fill or stroke is blended over `paper` before its ratio is measured. A value that clears the bar at full strength can fail at 0.5 opacity.
- **Surfaces other than `paper` cost contrast.** Inside an `accent-tint` box or on `paper-2` every ratio drops. Measure text placed there against that surface, not against `paper`.
- **Current defaults:** `accent #1d4ed8`、`soft #6b7280`、`link #2563eb`、`muted #4b5563` on `#ffffff`；均按 WCAG 对比度契约校验。

### Inversion rule (light → dark)

Any `rgba(45,49,66, X)` in light becomes `rgba(245,245,245, X)` in dark. Same opacities, RGB flipped. The accent gets a slight hue-shift brighter to read on dark paper.

## Typography
| Role | Family | Size | Weight | Usage |
|---|---|---|---|---|
| `title` | 'Noto Serif SC', 'Songti SC', serif | 1.75rem | 400 | Page H1 / 图标题 |
| `node-name` | 'Noto Sans SC', 'PingFang SC', sans-serif | 12px | 600 | 节点名称（中文） |
| `sublabel` | Geist Mono | 9px | 400 | Port, protocol, URL, field type |
| `eyebrow` | Geist Mono | 7–8px | 500, tracked 0.18em, uppercase | Type tags, axis labels |
| `arrow-label` | Geist Mono | 8px | 400, tracked 0.06em | Arrow annotations |
| `callout` | 'Noto Serif SC', 'Songti SC', serif *italic* | 14px | 400 | 注解（少用） |

### Font stack（产品固定 system）

不输出 Google Fonts `<link>`，不请求任何外网字体。使用系统中文字体：

```css
font-family: 'Noto Serif SC', 'Songti SC', serif;       /* 标题 */
font-family: 'Noto Sans SC', 'PingFang SC', sans-serif; /* 节点/正文 */
font-family: 'Geist Mono', ui-monospace, monospace;     /* 仅英文/编号/端口 */
```

### Font source

The font source decides whether a diagram makes any network request. It is a skin setting, so a saved profile carries it too.

Font source: `system`

| Value | What ships | Network |
|---|---|---|
| `web`（不使用） | Google Fonts `<link>`；产品禁止 | 有外网请求，禁止 |
| `system`（产品默认） | 无 font `<link>`；中文走系统 Noto Sans SC / Songti SC / PingFang SC；英文走 system-ui | None |

Use `system` when the user asks for offline output, system fonts, or no Google Fonts, or when this line says `system`. In `system` mode:

- Omit the font `<link>` (and the export `@import`) entirely. Keep every `font-family` stack as written: each one already ends in a generic family (`serif`, `system-ui, sans-serif`, `ui-monospace, monospace`), so a viewer who has Geist installed still gets it and everyone else gets their system face.
- Fallback faces have different metrics from Geist, so keep the 8px label padding intact rather than trimming boxes to the Geist measurement.
- Export with `python3 scripts/export_svg.py <file> --system-fonts`, and verify with `python3 scripts/self_check.py --offline <file>`, which fails if the Google Fonts link is still present.

The render linter's default run (`lint-render.py` with network blocked) measures exactly this fallback rendering, so every shipped template and example is already checked in system-font mode.

To keep the editorial faces with no network at all, install Instrument Serif, Geist, and Geist Mono locally (all three are SIL Open Font License) and use `system`: the stacks resolve to the installed faces first.

### Traditional Chinese labels

Geist and Instrument Serif carry no Han. A Traditional Chinese `<text>` element extends its own family — never swap the skin:

```svg
<text font-family="'Geist', 'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei', sans-serif">請求項比對</text>
```

Both Noto TC faces ship in the font link above, so the web font resolves before any locally installed one and the same file renders identically on macOS, Windows, and a reviewer's browser. The local families follow it for offline viewing. Page titles need the serif equivalent — `'Instrument Serif', 'Noto Serif TC', serif` — or a mixed Latin/Han title resolves Han through the platform's generic serif and the two halves disagree. Google's `css2` endpoint slices Chinese by unicode-range, so a diagram with a handful of Chinese labels downloads only the slices it touches.

**Width budget.** The per-character contract above is unchanged: every Unicode wide or full-width character costs 1em, every other character costs its face's Latin advance, and nonspacing marks cost nothing. Full-width punctuation — `（）「」，。：` — is wide and costs 1em as well, which is the part most often dropped.

Counting by script is the trap. `請求項 v2.1` is three full-width characters and five narrow ones; a formula that tallies Han and Latin letters silently drops `2`, `.`, and `1` and sizes the box for six of its nine characters.

Three rules follow from Han metrics, mirroring the Hangul ones:

- **Sublabels stay Latin.** Ports, protocols, field types, and URLs are Latin anyway — keep `Geist Mono` there and don't translate them. Han in a 9px mono sublabel is unreadable and has no mono face to fall back to. A sublabel that is prose rather than a value may be Chinese, but it then switches register by the third rule below.
- **Floor of 12px.** Han packs more strokes than Hangul into the same em box, so the 12px floor binds at least as hard here. If a Chinese name doesn't fit at 12px, cut the name — don't shrink the type.
- **Arrow labels, eyebrows, and legend text switch register.** Those slots are 7–8px Geist Mono, uppercase and tracked, which Han has neither a face nor legibility for. A Chinese label in one of those slots becomes 12px sans at weight 500 with no tracking and no uppercase transform, and its mask rect grows to match (16px tall, width from the budget above, still rounded to a multiple of 4). Latin labels in the same diagram keep the mono treatment.

Simplified Chinese takes the same three rules with the Simplified stack (`'Noto Sans SC'`, `'PingFang SC'`, `'Microsoft YaHei'`). That face does not ship in the link, so Simplified labels still resolve through whatever the viewer has locally.

## Stroke, radius, spacing
| Token | Value | Use |
|---|---|---|
| `stroke-thin` | `0.8` | Tag-box outlines, leaf nodes |
| `stroke-default` | `1` | Most strokes |
| `stroke-strong` | `1.2` | Emphasis strokes |
| `radius-sm` | `4` | Small tags |
| `radius-md` | `6` | Node boxes |
| `radius-lg` | `8` | Containers, rings |
| `grid` | `4` | Every coord, size, and gap is divisible by 4 (hard rule) |

---

## Node type → treatment
Semantic role combinations — reference these by name in type specs.

| Type | Fill | Stroke |
|---|---|---|
| `focal` (1–2 max) | `accent-tint` | `accent` |
| `backend` | `#ffffff` (white) | `ink` |
| `store` | `ink @ 0.05` | `muted` |
| `external` | `ink @ 0.03` | `ink @ 0.30` |
| `input` | `muted @ 0.10` | `soft` |
| `optional` | `ink @ 0.02` | `ink @ 0.20` dashed `4,3` |
| `security` | `accent @ 0.05` | `accent @ 0.50` dashed `4,4` |

---

## Customizing the skin
Four options:

1. **Run onboarding** — see [`onboarding.md`](onboarding.md). Drop a URL; the skill extracts the palette + fonts and rewrites this file.
2. **Edit by hand** — change the hex values in the tables above. Run the pre-output taste gate afterward to verify the accent still reads as "focal" against the new paper color.
3. **Brand handoff** — paste your existing design-token JSON into a new section here and map its tokens to the semantic roles above.
4. **Client profiles** — save and switch named skins, or bind one to a project, using [`profiles.md`](profiles.md).

### Constraints (don't break these)

- **Contrast**: every text token (`ink`, `muted`, `soft`, `link`, and `accent`) must clear 4.5:1 on `paper`, in both skins. See the [contrast contract](#contrast-contract); `scripts/verify-contrast.py` checks it.
- **One accent**: pick one color for `accent`. Two accents erases the focal signal.
- **No rainbow palette**: if your brand ships 8 colors, pick 3 (paper, ink, accent). The rest become `muted` variants.
- **Serif + sans + mono**: three families, not more. If brand typography is all sans, keep Instrument Serif for `title` and `callout` anyway — the contrast is load-bearing.
- **Paper is warm-neutral, not pure white**: pure white turns the design sterile. Pick a cream, bone, or light grey with a hint of warmth.
- **Dot pattern is optional, not default**: the 22×22 dot pattern is an opt-in "dotted paper" variant (good for long-form editorial hero diagrams). The default background is a clean `paper` fill, no pattern. When the pattern is enabled, it should sit at ~10% opacity of `ink` on `paper` — visible but quiet.
- **Container is clean by default**: the diagram sits directly on the page paper, no secondary container background or border. A framed variant (`paper-2` bg + `rule` border + 8px radius + padding) is available as an opt-in for card-heavy layouts, but don't reach for it by default — the extra chrome fights the figure.

## 1. Format
| Format | Deliverable | Keeps | Drops |
|---|---|---|---|
| `html` | self-contained `.html` (default) | header, diagram, summary cards, footer, live fonts | nothing |
| `svg` | `.svg` next to the source | the `<svg>` node, vector text | editorial wrapper; fonts substitute in offline tools |
| `png` | `.png` at `device_scale_factor` | pixels exactly as the browser renders them | vector editability |
| `html+png` | both | — | — |

Always generate the HTML first — `svg` and `png` are produced *from* it via [`export.md`](export.md). Never hand-author an SVG file directly; the HTML is the source of truth and the only artifact the taste gate (SKILL.md §9) is written against.

Pick by destination:

| Destination | Format | Size preset |
|---|---|---|
| Blog post, README, docs site | `html` (embed) or `png` | `doc-inline` |
| Keynote / PowerPoint / Google Slides | `png` @2 | `slide-16x9` |
| Figma / Illustrator / further editing | `svg` | `fit` |
| X / LinkedIn / OG link card | `png` @2 | `social-og` |
| Printed handout, PDF deck | `png` @3 | `print-a4-landscape` |
| Confluence / Notion / internal wiki | `png` @2 | `doc-wide` |

---

## 2. Size
The preset sets the SVG `viewBox`. Every value below is divisible by 4, so the grid rule in SKILL.md §7 still holds.

| Preset | viewBox | Aspect | PNG @2 | Type ramp | Use |
|---|---|---|---|---|---|
| `doc-inline` (default) | `0 0 960 600` | 8:5 | 1920×1200 | standard | Body-width diagram in a post or README |
| `doc-wide` | `0 0 1280 720` | 16:9 | 2560×1440 | standard | Full-width docs, wiki pages |
| `slide-16x9` | `0 0 1280 720` | 16:9 | 2560×1440 | presentation | Deck slide, projected |
| `slide-4x3` | `0 0 1024 768` | 4:3 | 2048×1536 | presentation | Legacy deck templates |
| `social-og` | `0 0 1200 632` | ~1.9:1 | 2400×1264 | presentation | Link preview card |
| `social-square` | `0 0 1080 1080` | 1:1 | 2160×2160 | presentation | Feed post, carousel |
| `print-a4-landscape` | `0 0 1120 792` | ~1.41:1 | @3 → 3360×2376 | print | A4 landscape, ~10mm margins at 96dpi |
| `print-a3-landscape` | `0 0 1584 1120` | ~1.41:1 | @3 → 4752×3360 | print | A3 landscape, ~10mm margins at 96dpi |
| `print-letter-landscape` | `0 0 1056 816` | ~1.29:1 | @3 → 3168×2448 | print | US Letter landscape |
| `fit` | derived from content | any | @2 | standard | Vector hand-off; no fixed frame |

### Deriving `fit`

Round the content bounding box **up** to the next multiple of 4, then add the fixed chrome: 40px outer margin on every side, plus 60px at the bottom for the legend strip. Never let the content touch the viewBox edge.

### Type ramp per size class

Node names shrink relative to the canvas as it grows — resist that. Scale the ramp with the preset so a projected slide stays readable from the back row.

| Role | standard | presentation | print |
|---|---|---|---|
| Title (Instrument Serif) | 28 | 40 | 32 |
| Node name (Geist 600) | 12 | 16 | 12 |
| Sublabel (Geist Mono) | 9 | 12 | 9 |
| Arrow label (Geist Mono) | 8 | 12 | 8 |
| Eyebrow / tag (Geist Mono) | 8 | 8 | 8 |
| Node box min height | 48 | 64 | 48 |
| Min gap between nodes | 24 | 40 | 24 |

The families named in these tables are the default skin's, and they are what `scripts/verify-docs-sync.py` binds each size to. Under a custom skin, read them as the `title`, `node-name`, `sublabel`, `arrow-label`, and `eyebrow` roles from [`style-guide.md`](style-guide.md); type references write those roles as `{node-name}` and so on.

Every `font-size` is one of the role values above for the preset in use, or one of these named exceptions:

| Exception | Font | Sizes |
|---|---|---|
| Dense annotation: legend keys, axis ticks, chart data labels, source lines, in-box tags | Geist Mono or Geist regular | 7 to 11, half steps allowed |
| Chart series or row name: bar category, line or bump series, gantt row, matrix header | Geist 600 | 10 to 11 |
| Group or entity heading | Geist 600 | 14 |
| Decorative watermark numerals at or under 0.08 opacity | any | any |

An exception is bound to the font beside it, weight included: Geist at 600 or heavier is the node-name voice, lighter Geist is annotation. So a Geist 600 node name cannot borrow the dense-annotation range down to 7, and a Geist Mono tick cannot borrow the 14 reserved for headings. A chart row carries a name in the same Geist 600 voice at a rank the ramp has no row for, which is why it has an exception of its own rather than a licence to shrink: `type-bar.md`, `type-gantt.md` and `type-line.md` all set that name at 10 or 11.

Anything else is a bug in the diagram, not a new size. The one standing carve-out is the closed inventory below.

### Safe areas

- **All presets:** 40px outer margin; legend strip is the bottom 60px and nothing else lives there.
- **`social-og`:** keep the outer 64px clear on every side — link-card crops are unpredictable across platforms.
- **`slide-*`:** keep the bottom 80px clear if the deck template has a footer bar; ask if unsure.

---

## 6. Checklist
Run alongside the SKILL.md §9 taste gate.

- [ ] All four dials set — explicitly requested, inferred from the destination, or defaulted and stated?
- [ ] `viewBox` matches the size preset exactly, values divisible by 4?
- [ ] `min-width` equals the preset's viewBox width, and the SVG sits in a local `overflow-x: auto` wrapper (inside any `overflow: hidden` ancestor)?
- [ ] `@media print` releases `min-width` and `overflow-x`, placed after the `svg` rule?
- [ ] Type ramp matches the size class — not the standard ramp on a slide?
- [ ] 40px outer margin honoured (64px for `social-og`)?
- [ ] Node count inside the detail level's ceiling?
- [ ] `faithful` above 9 nodes → zoned, and split above 24?
- [ ] Node names, sublabels, and edge labels all at the same audience level?
- [ ] CJK labels given a font fallback?
- [ ] Fidelity ledger reported for anything cut?
- [ ] Diagram `<svg>` has `role="img"`, resolving `aria-labelledby`, a non-empty first-child `<title>`, a non-empty `<desc>`, and per-diagram/variant prefixed IDs?
- [ ] Requested non-HTML formats produced via [`export.md`](export.md), not hand-authored?

# Layout grid, per-type budgets, page layout, and summary cards

Routed from SKILL.md §7 and §8. SKILL.md keeps the grid rule in one line, the universal complexity limits, and the split rule. This file holds the full grid table, every per-type budget row, the page layout, and the summary card pattern.

## Layout & Spacing

### 4px grid

**Structural geometry, divisible by 4:** node origins, widths, heights, gaps, padding. Off-grid by design: type sizes (role ramp in `references/output-spec.md`), radii, data-derived positions, text baselines, arrow markers, `.5` offsets that keep 1px strokes crisp, stroke widths, opacity, and the 22×22 dot-pattern.

| Category | Allowed values |
|---|---|
| Node width / height | 80, 96, 112, 120, 128, 140, 144, 160, 180, 200, 240, 320 |
| Gap between nodes | 20, 24, 32, 40, 48 |
| Padding inside boxes | 8, 12, 16 |
| Border radius | 4, 6, 8 |

### Complexity budget (per diagram)

These are the per-type limits. The universal rows in SKILL.md §7 (9 nodes, 12 arrows or transitions, 2 coral elements, 2 annotation callouts, and the motion limits) apply to every type on top of these, and a semantic pattern's own budget in [semantic-patterns.md](semantic-patterns.md) can only tighten them.

| Limit | Rule |
|---|---|
| Max unique components / relationships / ledger entries (architecture delta) | 8 / 10 / 8 |
| Max admin bands / traffic scopes / nodes (architecture containment) | 2 / 4 / 8 |
| Max lifelines (sequence) | 5 |
| Max combined fragments (sequence) | 1 (default); 2 only if each is single-region `opt`/`loop` |
| Max `alt` regions (sequence) | 2 |
| Max fragment nesting (sequence) | 1 |
| Max lanes (swimlane) | 5 |
| Max items (quadrant) | 12 |
| Max entities (ER) | 8 |
| Max nesting levels (nested) | 6 |
| Max tree depth | 4 |
| Max org chart depth | 4 |
| Max org chart nodes | 12 |
| Max layers (layer stack) | 6 |
| Max parts / levels / focal parts (exploded axonometric) | 5 / 5 / 1, three detail levels |
| Max tagged rooms or buildings / boxes / focal (axonometric plan) | 8 / 40 / 1 |
| Max circles (venn) | 3 |
| Max layers (pyramid) | 6 |
| Max radar axes | 5 |
| Max radar series | 5 |
| Max focal radar series | 1 |
| Max polar categories | 8 |
| Max polar series | 1 |
| Max focal polar categories | 1 |
| Max bars (bar chart) | 8 |
| Max bars (waterfall) | 8 incl. totals, 1 subtotal |
| Max cells (treemap) | 8 |
| Max series (line chart) | 5 |
| Max tasks (Gantt) | 12 |
| Max points (scatter plot) | 30 |
| Max series / outliers (box plot) | 8 / 8, 1 focal series |
| Max stages / nodes / flows (sankey) | 3 / 8 / 12 |
| Max categories (fishbone) | 6 bones, 3 sub-causes each |
| Max components / links (wardley) | 9 / 12, 2 movement arrows |
| Max columns / cards (kanban) | 5 / 12 total, 4 per column |
| Max stages / rows (user journey) | 6 / 3, 2 pain markers |
| Max stages / layers (service blueprint) | 6 stages; required customer / frontstage / backstage; optional evidence / support; 1 focal cell |
| Max zones / nodes / paths (deployment) | 3 / 6 / 8, 9 artifacts |
| Max nodes / edges (dependency) | 9 / 14, 4 ranks, 1 cycle |
| Max classes / relationships (UML class) | 7 / 8, 5 members per compartment |
| Max activities / slices / cards (story map) | 5 / 3 / 12 |
| Max tables / columns / FKs (db schema) | 5 / 8 shown / 6 |

If you exceed, split into two diagrams (overview + detail).

### Page layout

1. **Header** — `eyebrow` role, `title` role, optional subtitle (`node-name` family, `muted`).
2. **Diagram container** — default: **clean, borderless**, no background — the SVG sits directly on the page paper. Optional *framed* variant (for card-heavy layouts or hero placements): `paper-2` bg + 1px `rule` border + 8px radius + `1.5rem` padding + `overflow-x: auto`.
3. **Summary cards** — 2–3 col grid with *varied* widths (e.g., `1.1fr 1fr 0.9fr`).
4. **Footer** — colophon in the `sublabel` role, `muted`, hairline top border.

## Summary Card Pattern

Don't use 3 identical generic cards. Vary the treatment:

```html
<div class="card">
  <p class="eyebrow">SECTION LABEL</p>
  <div class="card-header">
    <span class="card-dot coral"></span>
    <h3>Card Title</h3>
  </div>
  <ul><li>Item</li></ul>
</div>
```

Rules:

- `background: #ffffff` (not paper — slight lift without shadow)
- `border: 1px solid` the `rule` color
- `border-radius: 6px`, `padding: 1.25rem`
- **No `box-shadow`**
- Card dots: 7px, `border-radius: 50%` — ink / muted / coral / link / soft variants

# Core SVG primitives

Routed from SKILL.md §6. SKILL.md keeps the six connector rules as one line each, the arrow roles, and a one-line node box summary. This file holds the markup and the long form of every rule. The copied template already carries the background rect, the commented dotted-paper pattern, and all three arrow markers, so load this file when you need exact markup or a rule's edge cases.

Type-specialized primitives (lifeline, activation bar, region) live in the relevant type reference linked in SKILL.md §3.

Snippets here and in every type reference name colors and fonts by role, never by value: `{ink}`, `{muted}`, `{paper}`, `{accent}`, `{link}`, `{soft}` for colors, `{ink @ 0.10}` for a role at an opacity, and `{title}`, `{node-name}`, `{sublabel}`, `{eyebrow}`, `{arrow-label}`, `{callout}` for font families. Resolve each against the active skin in [`style-guide.md`](style-guide.md) before emitting: `{ink @ 0.10}` becomes the `ink` color as `rgba(r,g,b,0.10)`, and `{sublabel}` becomes that role's full family stack. Uppercase names (`X`, `FILL`, `STROKE`) are geometry and per-node values you compute.

## Background

**Default: clean paper, no dot pattern.** Single `<rect>` filled with `paper`. Don't wrap the diagram in a secondary container background — the diagram sits directly on the page.

```svg
<rect width="100%" height="100%" fill="{paper}"/>
```

**Optional: dotted paper variant.** When a long-form editorial diagram benefits from textured ground (essays, hero diagrams on a dedicated page), opt in by adding the `dots` pattern and a second rect:

```svg
<defs>
  <pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse">
    <circle cx="1" cy="1" r="0.9" fill="{ink @ 0.10}"/>
  </pattern>
</defs>
<rect width="100%" height="100%" fill="{paper}"/>
<rect width="100%" height="100%" fill="url(#dots)" opacity="0.6"/>
```

Don't use the dot pattern when the diagram sits inside a product page, slide, or card — the texture compounds with surrounding chrome and reads as noise.

## Arrow markers (define all three, always)

```svg
<marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
  <polygon points="0 0, 8 3, 0 6" fill="{muted}"/>
</marker>
<marker id="arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
  <polygon points="0 0, 8 3, 0 6" fill="{accent}"/>
</marker>
<marker id="arrow-link" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
  <polygon points="0 0, 8 3, 0 6" fill="{link}"/>
</marker>
```

| Arrow | Stroke | When |
|---|---|---|
| Default | `muted` | Internal, generic |
| Accent | `accent` | Primary / highlighted / headline |
| Link-blue | `link` | HTTP/API calls, external systems |
| Dashed | `stroke-dasharray="5,4"` + any color | Optional, passive, return, async |

**Draw arrows before boxes** so z-order puts lines behind nodes.

The geometry source gate excludes shapes inside definition-only containers (`defs`, `symbol`, `marker`, `pattern`, `clipPath`, `mask`) because those shapes do not paint at their local coordinates. It does not resolve `<use>` instances; check referenced drawings with rendered lint and visual review. Inert HTML comments and `>` inside quoted tag attributes do not alter definition custody. The gate reads single- and double-quoted SVG attributes.

## Mandatory connector rules

These six rules are **non-negotiable**. Run the pre-output checklist (SKILL.md §9) to verify before producing any diagram.

1. **Rounded right-angle (orthogonal) connectors are mandatory.** Never use diagonal `<line>` or straight slanted paths between nodes that don't share an x or y axis. Every bend must be a quarter-arc with `r=8` (or `r=6` minimum for tight layouts). See `references/type-architecture.md` for the elbow-path formula. Reserve plain straight `<line>` only for connections whose endpoints share the same x or y coordinate. Diagonal connectors are an automatic fail. A connector also leaves and enters each box perpendicular to the edge it touches, at a port on the straight part of that edge at least 8px from a corner. A horizontal segment that starts on a top or bottom edge, or a vertical one that starts on a side edge, runs along the border behind the node fill and reads as an arrow coming out of the corner. From a repository checkout, `python3 <repo-root>/scripts/verify-geometry.py <file>` reports diagonal connectors, border-riding segments, and corner ports.

2. **Label-to-connector margin: 6–10px gap, always.** A label must never sit *on* its arrow — the connector must remain visible. Place the label centered above (or beside, for vertical segments) the line with a **minimum 6px gap** between the bottom of the label's mask rect and the connector stroke. The opaque mask rect prevents the arrow from bleeding through, but the *visible* gap between mask edge and line preserves the reader's ability to trace the connection. If the label is large enough that 6px feels cramped, push it to 8–10px. Never let the mask rect touch or overlap the stroke.

3. **No overlapping connectors.** Two connectors must never share the same stroke path, run parallel on top of each other, or be drawn on top of each other for any segment. When two orthogonal arrows must cross at a single point, apply the **bridge / hop** primitive (see `references/type-architecture.md` § Crossing arrows). When two arrows naturally want to overlap, offset their routing by ≥12px so each line is independently traceable. If you find yourself stacking connectors, redesign the layout — it means two nodes are too close, or the diagram is over budget (split into overview + detail).

4. **Shared edge → fan the attach points.** When two or more connectors enter or exit the *same edge* of a box, each must have its own distinct attach point along that edge — **no two connectors may share a single point on a box**. Spread the attach points evenly along the edge with **≥12px** between adjacent points (8px minimum for very small boxes). Routing rules:
   - For N connectors on an edge of length L, attach point `k` (1..N) sits at offset `L * k / (N + 1)` from the edge's leading corner.
   - When the connectors fan out to destinations on different sides, route each one orthogonally from its own attach point — no merging strokes near the box.
   - When two parallel connectors run in the same direction, keep them ≥12px apart along their entire length, not just at the attach point. Each arrow must remain independently traceable end-to-end.

   No connector may hide another. If you can't tell two arrows apart at a glance, the layout has failed.

5. **A connector must not pass behind a box that isn't its source or destination — except when the box is geometrically unavoidable on a direct orthogonal path.** Reroute around intervening boxes by default. The only legitimate exception is when a cross-cutting node (e.g., a footer service, a horizontal layer bar) physically sits between the connector's source and destination on the only straight path between them. In that exception:
   - The stroke must be **dashed** (e.g., `stroke-dasharray="4,3"`) to signal "transit, not interaction" — it tells the reader the intervening box is not an endpoint.
   - The label sits at the **visible end** of the connector (typically near the source) so it doesn't fall behind the intervening box.
   - No marker (arrowhead) may land on the intervening box's edge — the marker resolves at the true destination only.

   When in doubt, reroute. The exception exists for the narrow case where rerouting is geometrically impossible, not as a shortcut to avoid layout work.

6. **A label mask must not overlap a node drawn after it.** Compare their bounds after supported group and element `translate()` offsets, in the shared canvas rather than separate panel-local coordinates. Rule 2 keeps the label off its own connector; this one keeps it off the boxes. Because nodes are painted after labels, a mask that lands partly inside a node is covered by the node fill and the text renders as a fragment sitting on the node border. Place the label on a segment of the connector that runs through open canvas — for a connector leaving a node's right edge, that means clearing the node's `x + width` before the mask starts. A mask fully *inside* a node is a badge chip and is fine; a mask overlapping a zone container is fine too, since zones are painted first. From a repository checkout, verify with `python3 <repo-root>/scripts/verify-geometry.py <file>`.

## Node box — full pattern

```svg
<!-- 1. Opaque paper mask — prevents arrows bleeding through transparent fills -->
<rect x="X" y="Y" width="W" height="H" rx="6" fill="{paper}"/>
<!-- 2. Styled box -->
<rect x="X" y="Y" width="W" height="H" rx="6" fill="FILL" stroke="STROKE" stroke-width="1"/>
<!-- 3. Rectangular type tag (rx=2, NOT a pill) -->
<rect x="X+8" y="Y+6" width="28" height="12" rx="2" fill="transparent" stroke="STROKE@0.40" stroke-width="0.8"/>
<text x="X+22" y="Y+15" fill="STROKE@0.8" font-size="7" font-family="{eyebrow}"
      text-anchor="middle" letter-spacing="0.08em">API</text>
<!-- 4. Node name (node-name role, sans: human-readable) -->
<text x="CX" y="CY+2" fill="{ink}" font-size="12" font-weight="600"
      font-family="{node-name}" text-anchor="middle">Node Name</text>
<!-- 5. Technical sublabel (sublabel role, mono) -->
<text x="CX" y="CY+18" fill="{muted}" font-size="9"
      font-family="{sublabel}" text-anchor="middle">tech:port</text>
```

## Arrow labels — always mask, always with margin

Every arrow label needs an opaque rect behind it. Without one it bleeds through the line. **And the label must sit with a visible gap above the connector — never on top of it.**

```svg
<!-- Mask sits 14px above the arrow (8px text height + 6px gap). Stroke is at ARROW_Y. -->
<rect x="MID_X-18" y="ARROW_Y-20" width="36" height="12" rx="2" fill="{paper}"/>
<text x="MID_X" y="ARROW_Y-11" fill="{soft}" font-size="8"
      font-family="{arrow-label}" text-anchor="middle" letter-spacing="0.06em">WRITE</text>
```

Rules:

- ≤14 characters, all-caps, centered on segment midpoint.
- **Mandatory 6–10px gap** between the bottom of the mask rect and the arrow stroke. The connector must remain visible — a label that hides its own arrow is a hard fail.
- Never `writing-mode` vertical.
- For vertical segments, place the label to the side (not on the line) with the same 6–10px horizontal gap.

**Font substitution.** Don't estimate per-character widths — use the `style-guide.md` budget (0.60em sans / 0.62em mono per char). A production run sized mono labels at ~0.47em/char and clipped twice; the calibrated budget fits both the default skin's mono face (Geist Mono) and the common substituted mono faces (Menlo, Courier New, Monaco, all ≈0.60em). Previews via `rsvg`/`inkscape` can't fetch the injected Google Fonts, so verify with `python3 <repo-root>/scripts/verify-geometry.py` — it checks masks against later-painted nodes, not text fit; holding the budget is yours.

## Legend — horizontal strip at the bottom

**Never put the legend inside the diagram area.** Place as a horizontal strip after all nodes, with a hairline separator:

```svg
<line x1="30" y1="LEGEND_Y-8" x2="VIEWBOX_W-30" y2="LEGEND_Y-8"
      stroke="{ink @ 0.10}" stroke-width="0.8"/>
<text x="30" y="LEGEND_Y+8" fill="{muted}" font-size="8" font-family="{eyebrow}"
      letter-spacing="0.14em">LEGEND</text>
<!-- Items — horizontal row, ~160px apart -->
```

Expand SVG `viewBox` height by ~60px.

## Accessible SVG contract

SKILL.md §12 lists these six points one line each; this is the long form.

Every diagram is an accessible figure by default:

1. Its `<svg>` carries `role="img"` and `aria-labelledby` naming the diagram's `<title>` and `<desc>`.
2. `<title>` is the first child of `<svg>`, before `<defs>`. Assistive technology may ignore a title placed later.
3. The IDs are prefixed per diagram and variant: `<slug>-title` / `<slug>-desc`, where the slug matches the file (`loop`, `loop-dark`, `loop-full`). Bare `title` / `desc` IDs are banned — two inline diagrams would otherwise share one ID, and the second could be announced with the first's name. Each naming ID must also be unique across the whole document, including non-SVG elements; the packaged `scripts/self_check.py` rejects these collisions.
4. `<title>` is the short name of the subject — roughly the page `<h1>`, and about 60 characters or fewer.
5. `<desc>` is one sentence stating what the diagram shows in terms a reader needs without the image. Describe the content, not the geometry: “Org chart showing a command center routing work to specialist agents and escalation owners,” not “A box at the top with five boxes below it.” A shape-by-shape narration is worse than no useful description.
6. Decorative-only SVG, such as the specimen glyphs in `assets/icons.html`, carries `aria-hidden="true"` instead.
