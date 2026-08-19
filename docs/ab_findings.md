# A/B findings — resolution and avatar reference count

Run before fixing Drape's generation defaults. Three garments, each chosen because it
cost repeated regenerations in the first catalogue run for a *different* reason:

| Garment | Historic failure mode |
|---|---|
| `Dresses/443395852_grey` | colour temperature drift (cool grey → warm off-white) |
| `Tops/443402058_blueprinted` | fine print type/scale drift |
| `Lingerie/443396678_wine` | fine strap geometry / structure |

Prompt, garment references, reference order, model and org were held constant by reusing
each garment's exact stored prompt. One variable per comparison:

| Condition | Avatar refs | Image size | Isolates |
|---|---|---|---|
| A | 1 | `portrait_4_3` | baseline (production settings) |
| B | 1 | `auto_2K` | A→B = resolution |
| C | 2 | `auto_2K` | B→C = second avatar reference |

## Result: 9/9 passed QC. Neither variable changed the pass rate.

No criterion failed under any condition, so neither a second avatar reference nor the
higher resolution produced a measurable accuracy gain on these garments.

## The finding that mattered was incidental

`auto_2K` is **not aspect-stable**. Output sizes:

| Garment | A (`portrait_4_3`) | B (`auto_2K`) | C (`auto_2K`) |
|---|---|---|---|
| grey dress | 1536×2048 | 1776×2368 | 1776×2368 |
| blueprinted top | 1536×2048 | 1696×2272 | 1776×2368 |
| wine lingerie | 1536×2048 | **2496×1664 (landscape)** | 1776×2368 |

`auto_*` lets the model choose its own aspect ratio, and on one of three garments it
returned landscape. For a catalogue, where every shot must share a frame, that is a
defect — and it is one QC does not catch, because a landscape image can be a perfectly
good photograph of the right garment.

`auto_2K` does yield ~30-35% more pixels when it stays portrait. That is real, but it is
not worth an unpredictable orientation for the default path.

## Decisions

* **`DEFAULT_IMAGE_SIZE = "portrait_4_3"`** — aspect-stable at 1536×2048. `auto_2K` stays
  available per generation for anyone who wants the extra resolution and will check the frame.
* **`DEFAULT_AVATAR_REF_COUNT = 1`** — no measured benefit, and a second reference costs an
  extra upload while competing with the garment for the model's attention. A second avatar
  reference remains the *repair* for a confirmed `avatar_identity` failure, which is where
  there is an actual reason to reach for it.

## Caveat

All three garments pass at baseline, so this run measures whether the variables help on
already-working prompts — not whether they rescue a failing one. It is evidence against
changing the default, not proof the variables never matter.
