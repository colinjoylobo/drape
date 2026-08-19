# Drape

Turns flat product photos into branding shots: a saved model, dressed in the real garment,
posed and lit for the piece — with the garment's colour, print and construction held to the
reference rather than reinterpreted.

## Running it

```bash
./run.sh
```

Backend on `:8077`, frontend on `:5173`. Open http://localhost:5173.

First run only:

```bash
cd backend && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cd ../frontend && npm install
```

## Worked examples

A finished shoot ships as sample data — 7 models, 19 garments, 38 QC-passed shots, and a Look
Library seeded from the looks that produced them:

```bash
cd backend && ./.venv/bin/python import_examples.py --reset
```

`backfill_detail_regions.py` then adds real close-up crop coordinates to those imported garments
— the old format recorded only "needs a close-up", never where. It merges *only* the regions and
leaves descriptions untouched, so the examples keep describing what you can actually see.

It imports from an existing pipeline manifest (`--manifest PATH`) and references the image files
where they already sit rather than copying them. Delete the "Sample shoot" session any time; the
models and library entries are persistent and stay until you remove them.

## The flow

1. **Models** — create at least one. Persistent, reused across every session.
2. **New session** — one per batch of clothes. Upload garments, or import a folder with one
   subfolder per garment.
3. **Analyse** — reads the photos: colour, print, construction, pieces, coverage risk, and the
   close-up regions worth cropping. Then proposes two looks.
4. **Edit anything** — description, pieces, coverage, photo roles, close-up crops, look text,
   and styling/props. Nothing is generated until you say so.
5. **Generate** — see the exact prompt first if you want. Every reference image is labelled by role.
6. **QC** — seven criteria, automatic. A failure comes with a suggested fix and the reasoning;
   applying it is one click and creates a new attempt rather than replacing the old one.
7. **Back shots** — any front look can spawn a matching back view. The scene, light and mood are
   held constant and only the camera side changes, so the pair reads as one sitting.
8. **Save the look** — a look that produced a passing shot can be filed in the Look Library,
   which grounds future suggestions for that category.
9. **Export** — a self-contained HTML catalogue, everything embedded, shareable as one file.

## Layout

```
backend/app/
  config.py               routing, credentials, defaults
  db.py                   SQLite schema
  core/
    vision.py             shared Gemini plumbing (EXIF, block handling)
    extractor.py          factual garment reading, temperature 0
    art_director.py       look proposals, temperature 0.95, library-aware
    detail_crop.py        bounding box -> real cropped reference
    prompt_builder.py     prompt + ordered, role-labelled references
    generator.py          Seedream, pinned to the Servicing org
    qc.py                 judging + failure-to-fix mapping
    pipeline.py           orchestration and DB state
  routers/                HTTP API
frontend/src/             React (Vite)
docs/ab_findings.md       why the generation defaults are what they are
```

## Shoot-craft profiles

Prompts are versioned. **v1** is the original behaviour, kept intact so earlier work stays
reproducible. **v2** (the default for new work) adds a craft layer drawn from how fashion campaigns
are actually lit and posed — key-light angle and modifier, rim separation at a stop under key,
negative fill, lens choice, catchlights and gaze, contrapposto and hand articulation — plus
per-category direction for lingerie, activewear, dresses, nightwear, tops and outerwear.
See `core/shoot_style.py` and `docs/shoot_craft_sources.md`.

The profile is selectable per generation in the UI and recorded on every row, so any shot can be
explained by the rules that made it. Existing rows are labelled v1 by the migration default.

Note it encodes **technique, not brand names**. Naming a label asks the model to imitate that
house's campaign identity — a trade-dress problem — and is weaker direction than stating the actual
setup.

## The learning loop

QC failures become standing guardrails instead of being repaired once and forgotten.

* **Observed** — a confirmed failure is counted so a recurring problem becomes visible. Never fed
  back into a prompt on its own.
* **Proven** — a shot failed a criterion, a repair was applied, and the next attempt passed *that
  same criterion*. Only proven lessons reach prompts.

Lessons start scoped to one category, because many pitfalls genuinely are category-specific — sheer
skirts open up when seated, activewear goes flat under frontal light. But some defects belong to the
**generator** rather than the garment type. Colour drift is the clear case: it appeared independently
in dresses, nightwear and tops, and each had to rediscover it at credit prices. Once a criterion has
been proven in two distinct categories it is **promoted to global** and applies everywhere. A
category-specific lesson still wins over a global one for the same criterion, since the more specific
guidance is the better guidance.

Only the criterion the repair actually targeted is credited. Failures cascade — one artefact can
fail identity, realism and presence at once, and a single fix flips all three — so crediting every
recovered criterion would file identity guidance under "garment colour" and inject irrelevant advice
into every later shot. Each generation can teach only once, so re-running QC cannot re-credit it.

Nothing mutates a template silently. Lessons append as a readable block, visible and switchable via
`/api/library/lessons` or `drape.py lessons`. A rule learned from one fluke that quietly degrades
every future shot is worse than no rule.

## Command line

`drape.py` drives the same core as the UI — same analysis, same craft profile, same QC, same
learning loop — so a scripted bulk run and a click in the browser share one brain.

```bash
./.venv/bin/python drape.py sessions
./.venv/bin/python drape.py import   --session "August drop" --root ~/shoots/aug --category Tops
./.venv/bin/python drape.py analyze  --session "August drop"
./.venv/bin/python drape.py assign   --session "August drop" --model Leila
./.venv/bin/python drape.py generate --session "August drop" --dry-run
./.venv/bin/python drape.py repair   --session "August drop"
./.venv/bin/python drape.py lessons
```

`generate` and `repair` never touch work that already succeeded, and `--dry-run` prints exactly what
would be spent before anything is.

## Things worth knowing before changing them

**Generation routing is not a parameter.** Every call goes through the Servicing org, and the
session ID is bound to a Labs project entity. That binding is not cosmetic — it is what satisfies
the backend's tool-access check for an org whose standalone Model Garden toggle is off. Setting
the org header without the bound session produces confusing 403s.

**Credential load order matters.** `G5/.env` defines `access_token`/`refresh_token`/`base_url`
too, but its tokens are empty and its `base_url` points elsewhere. `load_dotenv` will not replace
an already-set variable, so the credentials file is loaded with `override=True` after it. Get this
wrong and generation fails with "Missing credentials" long after startup. `backend/.env` overrides
everything, so Drape can be given its own credentials instead of borrowing another project's.

**Detail crops are the accuracy mechanism.** The extractor returns bounding boxes and
`detail_crop.py` produces real cropped, upscaled references. An earlier version of this pipeline
flagged that a close-up was needed and then passed the *whole* source photo through — often the
same file already sent as the front reference — so the generator never received the close-up it
had asked for. Fine prints and thin straps depend on this working.

**Extraction and art direction run at different temperatures on purpose.** Reading a garment off a
photograph wants determinism; inventing a shot does not. Fused into one temperature-0 call, the
proposed scenes collapse onto the same few backdrops and a whole catalogue looks like one shoot.

**A QC failure is re-checked before it is believed.** Only criteria that fail twice are reported as
real; a failure that does not reproduce is marked uncertain in the UI. A *pass* is never re-checked,
so this makes the judge harder to fool rather than softer.

**Warmth is requested, not suppressed.** An earlier rubric fixed identity drift by banning smiles
and mandating flat even light, which fixed drift by removing life. The constraints that actually
protect identity are head geometry (no full back-turn or profile) and light quality on the face —
not expression. `presence` is a QC criterion for the same reason: a lifeless model is a defect.

**A garment photo shot on a person gets cropped before it is sent.** Otherwise that person's face,
hair and styling override the model you chose, and their backdrop comes with them — we watched a
reference model's identity beat the selected avatar outright. The extractor returns a garment box
*and* a head box; the garment box is clamped to start below the head plus a clearance margin, and
the crop applies no upward padding. Asking a model in words to "ignore the face" does not work when
it can see one; removing it does. The prompt still carries an explicit ignore-the-person clause as a
second line of defence.

**Back views change which references lead.** The model's back becomes image 1 (her hair from behind
is what identifies her), the garment's back leads the garment references, and QC is told it is
judging a back view — without that it fails `avatar_identity` every time, for a face it cannot see.

**Props are subordinated in the prompt, deliberately.** Styling comes after the garment and
carries an explicit clause saying nothing may cover, overlap or draw the eye from the product, or
obscure her face. Every prop is a chance to hide the thing being sold, so the art director is also
capped at two or three and told to keep them quiet and coordinated. A model's *signature styling*
(persistent, on the avatar) and a look's *props* (per shot) are separate fields — one is who she
always is, the other is this particular photograph.

**Adding a column?** Put it in `SCHEMA` for fresh installs *and* in `MIGRATIONS` in `db.py`, which
applies idempotently on startup. Existing databases hold real work and must not need rebuilding.

**Nothing regenerates automatically.** Generation costs credits, so batch generation skips looks
that already have an image (`only_ungenerated`, on by default — a second click on "Generate all"
cannot re-bill a finished session), garments can be selected individually, prompt preview is free,
and repairs are always user-approved. QC and analysis run on Gemini Flash and are cheap; image
generation is the expensive step, so everything is arranged to avoid spending it twice.
