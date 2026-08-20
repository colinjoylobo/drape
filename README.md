# Drape

Turn flat product photos into publishable campaign shots.

Give Drape a garment's product photography and a saved model, and it produces the frames a brand
would actually publish: the real garment, on a consistent model, posed and lit for the piece — with
colour, print and construction held to the reference rather than reinterpreted. Every shot is judged
against the source photographs before you see it.

## Why it is built this way

A model asked to "dress her in this" will happily produce something *similar* to the garment. Similar
is worthless here: a customer receives the actual piece, so a drifted print or a shifted shade is a
return. Almost every decision below follows from that.

- **Analysis and art direction are separate calls at different temperatures.** Reading a garment off
  a photograph wants determinism; inventing a shot does not. Fused into one call, proposed scenes
  collapse onto the same few backdrops and a whole catalogue looks like one shoot.
- **Close-ups are cropped, not requested.** The analyzer returns bounding boxes for fine print and
  fine construction, and those regions are cropped and upscaled into extra references. A full-garment
  shot does not resolve a ditsy floral or a thin strap at generation resolution.
- **A garment photographed on a person is cropped to the garment.** Otherwise that person's face,
  hair and background compete with — and often beat — the model you selected. The extractor returns a
  garment box *and* a head box; the garment box is clamped below the head, and the crop never pads
  upward.
- **Colour is invariant to scene light.** A look may call for warm lamplight or cool overcast; the
  garment must still read as its own colour either way.
- **Nothing regenerates on its own.** Generation is the only expensive step, so batches skip finished
  work, prompt preview is free, and repairs are user-approved.

## Sample data

The repository ships with a worked shoot — models, garments, analyses, looks, finished shots and QC
verdicts — so a clone opens onto real work rather than an empty screen.

After cloning, point the bundled database at your checkout:

```bash
cd backend && ./.venv/bin/python rebase_paths.py
```

Image paths are stored absolute, so without this they still name the machine the data was created
on. The script is idempotent and safe to re-run after moving the checkout.

Source photography is downscaled to 2000px on the long edge — enough to see the app work and to
regenerate from, without carrying camera-resolution originals in git. Generated shots are unmodified.

## Getting started

```bash
cd backend && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cd ../frontend && npm install
cp backend/.env.example backend/.env      # then fill it in
./run.sh
```

Backend on `:8077`, frontend on `:5173`. Open http://localhost:5173.

Browsing and analysis work without credentials; generation does not. `run.sh` says so on startup
rather than letting you discover it after setting up a whole session.

## The flow

1. **Models** — create at least one. Models are persistent and reused across every session.
2. **New session** — one per batch of clothes. Upload garments, or import a folder with one
   subfolder per garment.
3. **Analyse** — reads colour, print, construction, pieces, coverage risk and the close-up regions
   worth cropping, then proposes looks.
4. **Edit anything** — description, pieces, coverage, photo roles, close-up crops, look text,
   styling and props. Nothing is generated until you say so.
5. **Generate** — inspect the exact prompt first if you want. Every reference image is labelled by
   role in the prompt itself.
6. **QC** — seven criteria, automatic. A failure arrives with a suggested fix and the reasoning;
   applying it creates a new attempt rather than replacing the old one.
7. **Back shots** — any look can spawn a matching back view. Scene, light and mood are held constant
   so the pair reads as one sitting.
8. **Save the look** — a look that produced a passing shot can be filed in the Look Library, which
   grounds future suggestions for that category.
9. **Export** — a self-contained HTML catalogue, everything embedded, shareable as one file.

## Quality control

Every generated shot is scored against the source photographs on seven criteria: model identity,
garment colour, pattern, structure, coverage and pieces, photorealism, and presence — whether she
reads as a comfortable, present person rather than a mannequin.

A failure is re-judged before it is believed, and only criteria that fail twice are reported. A pass
is never re-judged, which makes the judge harder to fool rather than softer.

Each failure maps to a concrete repair: colour drift asks for a cropped patch of the actual fabric,
because an adjective cannot specify a colour; a pattern miss asks for a tighter close-up; a coverage
failure on a high-slit or sheer skirt switches to a standing pose, because crossing the legs pulls
such a skirt open and no wording prevents it.

## The learning loop

Repairs would be worth little if each one only fixed a single shot, so verified fixes become standing
rules.

- **Observed** — a confirmed failure is counted so a recurring problem becomes visible. Never fed
  back into a prompt on its own; acting on an unverified guess is how a pipeline teaches itself a
  superstition.
- **Proven** — a shot failed a criterion, a repair was applied, and the next attempt passed *that
  same criterion*. Only these reach prompts.

Only the criterion a repair actually targeted is credited. Failures cascade — one artefact can fail
identity, realism and presence at once, and a single fix flips all three — so crediting every
recovered criterion would file identity guidance under "garment colour".

Lessons start scoped to one category, because many pitfalls genuinely are category-specific. But some
defects belong to the generator rather than the garment type, so a criterion proven in two distinct
categories is promoted and applies everywhere.

Nothing mutates a prompt template silently. Lessons append as a readable block, visible and
switchable in the Look Library.

## Shoot craft

Prompts are versioned. **v1** is the original behaviour, kept so earlier work stays reproducible.
**v2**, the default, adds a craft layer drawn from how fashion campaigns are actually lit and posed:
key-light angle and modifier, rim separation about a stop under key, negative fill, lens choice,
catchlights and gaze, contrapposto and hand articulation — plus per-category direction for lingerie,
activewear, dresses, nightwear, tops and outerwear.

The profile is selectable per generation and recorded on every row, so any shot can be explained by
the rules that produced it.

It encodes technique, not brand names. Naming a label asks the model to imitate that house's campaign
identity, and in practice it is weaker direction than stating the actual setup.

## Command line

`drape.py` drives the same core as the UI — same analysis, same craft profile, same QC, same learning
loop — so a scripted bulk run and a click in the browser share one brain and one lesson store.

```bash
./.venv/bin/python drape.py sessions
./.venv/bin/python drape.py import   --session "August drop" --root ~/shoots/aug --category Tops
./.venv/bin/python drape.py analyze  --session "August drop"
./.venv/bin/python drape.py assign   --session "August drop" --model Maya
./.venv/bin/python drape.py generate --session "August drop" --dry-run
./.venv/bin/python drape.py repair   --session "August drop"
./.venv/bin/python drape.py lessons
```

`--dry-run` prints exactly what would be generated before anything is spent.

## Layout

```
backend/
  app/
    config.py             provider, credentials, defaults — all from the environment
    db.py                 SQLite schema and migrations
    core/
      vision.py           shared vision plumbing (EXIF, refusal handling)
      extractor.py        factual garment reading, temperature 0
      art_director.py     look proposals, temperature 0.95, library-aware
      detail_crop.py      bounding box -> cropped close-up reference
      garment_crop.py     isolate a garment from a photo shot on a person
      prompt_builder.py   prompt + ordered, role-labelled references
      shoot_style.py      versioned shoot-craft profiles
      qc.py               judging + failure-to-fix mapping
      lessons.py          the learning loop
      generator.py        image generation
      pipeline.py         orchestration and state
    routers/              HTTP API
  drape.py                command line over the same core
  tests/                  guardrail tests
frontend/src/             React (Vite)
docs/                     A/B findings, shoot-craft sources
```

## Tests

```bash
cd backend && ./.venv/bin/python -m pytest tests -q
```

49 tests, none of them "does it import". Each covers a mistake this pipeline actually made: a face
surviving into a garment crop, coverage added to a complete set, a reference losing its role label, a
lesson credited to the wrong criterion, a batch re-billing finished work.

## Deploying

Drape is built to run on one machine for one operator. Before it runs anywhere else, read
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — in particular that **there is no authentication**, and one
of the endpoints spends money.

## Configuration

All settings come from `backend/.env`, which is gitignored — see `.env.example` for the full list.
Nothing capability-bearing is committed: the provider session id in particular is a credential, since
anyone holding it can spend the associated account's credits.

Storage — the database, uploads and generated images — lives in `backend/storage/` and is also
gitignored, so a fresh clone starts empty.

## License

Not currently licensed for reuse.
