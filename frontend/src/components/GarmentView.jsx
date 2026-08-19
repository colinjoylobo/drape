import { useCallback, useEffect, useState } from "react";
import { api, CATEGORIES, fileUrl, IMAGE_SIZES, PROFILES, PROP_SUGGESTIONS, ROLES } from "../api";
import { Field, Lightbox, Modal, QcBadge, useAction } from "./common";

export default function GarmentView({ garmentId, onBack }) {
  const [g, setG] = useState(null);
  const [avatars, setAvatars] = useState([]);
  const [lightbox, setLightbox] = useState(null);
  const { error, setError, run } = useAction();

  const load = useCallback(
    () => api.getGarment(garmentId).then(setG).catch((e) => setError(e.message)),
    [garmentId, setError]
  );
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.listAvatars().then(setAvatars).catch(() => {}); }, []);

  if (!g) return <div className="empty">Loading…</div>;
  const a = g.analysis;

  return (
    <>
      <button className="backlink" onClick={onBack}>← Back to session</button>

      <div className="page-head">
        <div>
          <h1>{g.name}</h1>
          <p>
            {g.category || "No category"}
            {g.size_variant ? ` · ${g.size_variant}` : ""}
            {g.avatar ? ` · ${g.avatar.name}` : ""}
          </p>
        </div>
        {!a && (
          <button className="btn primary"
                  onClick={() => run(async () => { await api.analyze(g.id); load(); })}>
            Analyse garment
          </button>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      <Setup g={g} avatars={avatars} onChange={load} />
      <References g={g} onChange={load} onZoom={setLightbox} />

      {a ? (
        <>
          <Analysis g={g} onChange={load} onZoom={setLightbox} />
          <Looks g={g} onChange={load} onZoom={setLightbox} />
        </>
      ) : (
        <div className="empty">
          <h3>Not analysed yet</h3>
          <p>
            Analysis reads the product photos for colour, print, construction and pieces,
            works out which close-ups are worth cropping, then proposes looks. Everything it
            produces stays editable — nothing is generated until you say so.
          </p>
        </div>
      )}

      <Lightbox path={lightbox} onClose={() => setLightbox(null)} />
    </>
  );
}

/* ---------------- setup: category + model ---------------- */
function Setup({ g, avatars, onChange }) {
  const { run, busy } = useAction();
  const set = (body) => run(async () => { await api.updateGarment(g.id, body); onChange(); });

  // Every model stays selectable — an earlier version hid all but the exact match,
  // which left a picker with a single option and no way to say otherwise. The
  // category/size match is surfaced as a recommendation instead of a restriction,
  // since a size variant genuinely does mean a different body type.
  const fits = (av) =>
    (!g.category || !av.category || av.category === g.category) &&
    (!g.size_variant || av.size_variant === g.size_variant);
  const recommended = avatars.filter(fits);
  const others = avatars.filter((av) => !fits(av));

  return (
    <div className="card">
      <h3>Setup</h3>
      <div className="row" style={{ alignItems: "flex-start", gap: 22 }}>
        {g.avatar && (
          <img src={fileUrl(g.avatar.front_path)} alt=""
               style={{ width: 86, height: 112, objectFit: "cover",
                        borderRadius: "var(--radius-sm)", border: "1px solid var(--line)" }} />
        )}
        <div style={{ flex: 1, minWidth: 190 }}>
          <Field label="Category">
            <select value={g.category || ""} disabled={busy}
                    onChange={(e) => set({ category: e.target.value })}>
              <option value="">None</option>
              {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </Field>
        </div>
        <div style={{ flex: 1, minWidth: 190 }}>
          <Field label="Model"
                 hint={avatars.length === 0
                   ? "No models saved yet. Create one under Models."
                   : recommended.length === 0
                     ? "No model matches this category and size variant, so all are listed. Check the body type suits the garment."
                     : undefined}>
            <select value={g.avatar_id || ""} disabled={busy}
                    onChange={(e) => set({ avatar_id: Number(e.target.value) })}>
              <option value="">Choose a model…</option>
              {recommended.length > 0 && (
                <optgroup label="Recommended for this garment">
                  {recommended.map((av) => (
                    <option key={av.id} value={av.id}>
                      {av.name}{av.category ? ` — ${av.category}` : ""}
                      {av.size_variant ? ` (${av.size_variant})` : ""}
                    </option>
                  ))}
                </optgroup>
              )}
              {others.length > 0 && (
                <optgroup label="All other models">
                  {others.map((av) => (
                    <option key={av.id} value={av.id}>
                      {av.name}{av.category ? ` — ${av.category}` : ""}
                      {av.size_variant ? ` (${av.size_variant})` : ""}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
          </Field>
        </div>
      </div>
    </div>
  );
}

/* ---------------- reference photos ---------------- */
function References({ g, onChange, onZoom }) {
  const { run } = useAction();
  return (
    <div className="card">
      <h3>Product photos</h3>
      <p className="hint" style={{ marginTop: -8, marginBottom: 16 }}>
        Roles decide which photos reach the generator. Change one if it was read wrong — your
        choice sticks even if you re-analyse.
      </p>
      <div className="thumbs">
        {g.images.map((img) => (
          <div key={img.id} className={`thumb ${img.role === "irrelevant" ? "irrelevant" : ""}`}>
            <img src={fileUrl(img.path)} alt="" loading="lazy" onClick={() => onZoom(img.path)} />
            <select value={img.role || ""}
                    onChange={(e) => run(async () => {
                      await api.setImageRole(img.id, e.target.value); onChange();
                    })}>
              <option value="" disabled>unset</option>
              {ROLES.map((r) => <option key={r} value={r}>{r.replace(/_/g, " ")}</option>)}
            </select>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- analysis ---------------- */
function Analysis({ g, onChange, onZoom }) {
  const a = g.analysis;
  const [desc, setDesc] = useState(a.garment_desc || "");
  const [pieces, setPieces] = useState((a.pieces || []).join(", "));
  const [pairing, setPairing] = useState(a.pairing_note || "");
  const [risk, setRisk] = useState(!!a.coverage_risk);
  const [backStruct, setBackStruct] = useState(!!a.back_has_structure);
  const { busy, error, run } = useAction();

  useEffect(() => {
    setDesc(a.garment_desc || "");
    setPieces((a.pieces || []).join(", "));
    setPairing(a.pairing_note || "");
    setRisk(!!a.coverage_risk);
    setBackStruct(!!a.back_has_structure);
  }, [a]);

  const dirty =
    desc !== (a.garment_desc || "") ||
    pieces !== (a.pieces || []).join(", ") ||
    pairing !== (a.pairing_note || "") ||
    risk !== !!a.coverage_risk ||
    backStruct !== !!a.back_has_structure;

  const save = () =>
    run(async () => {
      await api.updateAnalysis(g.id, {
        garment_desc: desc,
        pieces: pieces.split(",").map((p) => p.trim()).filter(Boolean),
        pairing_note: pairing,
        coverage_risk: risk,
        back_has_structure: backStruct,
      });
      onChange();
    });

  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 14 }}>
        <h3 style={{ margin: 0 }}>What this garment is</h3>
        <div className="spacer" />
        {a.edited_by_user ? <span className="badge accent">Edited by you</span> : null}
        <button className="btn ghost small"
                onClick={() => run(async () => { await api.analyze(g.id, true); onChange(); })}>
          Re-analyse
        </button>
      </div>
      {error && <div className="error">{error}</div>}

      <Field label="Description"
             hint="Used word-for-word in the prompt. Naming colour temperature and construction precisely is what stops the generator drifting.">
        <textarea rows={5} value={desc} onChange={(e) => setDesc(e.target.value)} />
      </Field>

      <Field label="Pieces" hint="Comma separated. Every piece listed must appear in the shot.">
        <input type="text" value={pieces} onChange={(e) => setPieces(e.target.value)} />
      </Field>

      <label className="row" style={{ marginBottom: 12, cursor: "pointer" }}>
        <input type="checkbox" checked={risk} onChange={(e) => setRisk(e.target.checked)}
               style={{ width: "auto" }} />
        <span className="small">A piece is missing — add a covering piece</span>
      </label>
      {risk && (
        <Field label="Pairing note"
               hint="What gets added. It should coordinate rather than read as filler. Leave this off for a complete set — adding to one would hide the product.">
          <textarea rows={2} value={pairing} onChange={(e) => setPairing(e.target.value)} />
        </Field>
      )}

      <label className="row" style={{ marginBottom: 18, cursor: "pointer" }}>
        <input type="checkbox" checked={backStruct}
               onChange={(e) => setBackStruct(e.target.checked)} style={{ width: "auto" }} />
        <span className="small">
          The back has construction the front doesn't show — send the back photo too
        </span>
      </label>

      <DetailCrops g={g} onChange={onChange} onZoom={onZoom} />

      {dirty && (
        <button className="btn primary" disabled={busy} onClick={save} style={{ marginTop: 16 }}>
          {busy ? <span className="spin" /> : "Save changes"}
        </button>
      )}
    </div>
  );
}

function DetailCrops({ g, onChange, onZoom }) {
  const regions = g.analysis.detail_regions || [];
  const { run } = useAction();
  return (
    <div style={{ marginTop: 6 }}>
      <span className="label">Close-up references</span>
      <p className="hint" style={{ marginTop: 0, marginBottom: 10 }}>
        Cropped from the product photos and sent alongside them. These are what make fine prints
        and thin straps come out right — a full-garment shot doesn't resolve them.
      </p>
      {regions.length === 0 ? (
        <p className="small faint" style={{ margin: 0 }}>
          None — the garment was read as plain enough not to need any.
        </p>
      ) : (
        <div className="thumbs">
          {regions.map((r, i) => (
            <div key={i} className="thumb" style={{ width: 136 }}>
              <img src={fileUrl(r.source_path)} alt="" style={{ width: 136, height: 104 }}
                   onClick={() => onZoom(r.source_path)} />
              <div className="hint" style={{ marginTop: 4 }}>{r.why}</div>
              <button className="btn ghost small" style={{ marginTop: 5, width: "100%" }}
                      onClick={() => run(async () => {
                        await api.removeDetailCrop(g.id, i); onChange();
                      })}>
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------- looks + generation ---------------- */
function Looks({ g, onChange, onZoom }) {
  const [library, setLibrary] = useState([]);
  const [picking, setPicking] = useState(false);
  // Off by default: every shot should be its own photograph. On keeps this garment
  // inside the same set as the rest of the shoot.
  const [matchSet, setMatchSet] = useState(false);
  const { busy, error, setError, run } = useAction();

  useEffect(() => {
    if (g.category) api.listLibrary(g.category).then(setLibrary).catch(() => {});
  }, [g.category]);

  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 14 }}>
        <h3 style={{ margin: 0 }}>Looks</h3>
        <div className="spacer" />
        {library.length > 0 && (
          <button className="btn ghost small" onClick={() => setPicking(true)}>
            Use a saved look
          </button>
        )}
        <label className="row small faint" style={{ gap: 6, cursor: "pointer" }}
               title="By default each garment gets its own setting. Tick this to keep it in the same set as the rest of the shoot.">
          <input type="checkbox" checked={matchSet} style={{ width: "auto" }}
                 onChange={(e) => setMatchSet(e.target.checked)} />
          Match the rest of the shoot
        </label>
        <button className="btn ghost small" disabled={busy}
                onClick={() => run(async () => {
                  await api.proposeLooks(g.id, { n: 2, replace: false,
                                                 match_existing: matchSet });
                  onChange();
                })}>
          {busy ? <span className="spin" /> : "Suggest more"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}

      {g.looks.length === 0 && (
        <p className="small faint">No looks yet. Suggest some, or add one from the library.</p>
      )}

      {g.looks.map((look) => (
        <Look key={look.id} look={look} g={g} onChange={onChange} onZoom={onZoom}
              onError={setError} />
      ))}

      {picking && (
        <Modal title={`Saved looks — ${g.category}`} onClose={() => setPicking(false)} wide>
          {library.map((t) => (
            <div key={t.id} className="card inset" style={{ marginBottom: 11 }}>
              <p className="small" style={{ margin: "0 0 11px" }}>{t.text}</p>
              <div className="row">
                {t.scene_tag && <span className="badge neutral">{t.scene_tag}</span>}
                <span className="small faint">used {t.times_used}×</span>
                <div className="spacer" />
                <button className="btn primary small" onClick={() => run(async () => {
                  await api.createLook(g.id, { text: t.text, template_id: t.id });
                  setPicking(false);
                  onChange();
                })}>Use this</button>
              </div>
            </div>
          ))}
        </Modal>
      )}
    </div>
  );
}

function Look({ look, g, onChange, onZoom, onError }) {
  const [text, setText] = useState(look.text);
  const [props, setProps] = useState(look.props || "");
  const [editing, setEditing] = useState(false);
  const [preview, setPreview] = useState(null);
  const [imageSize, setImageSize] = useState(IMAGE_SIZES[0]);
  const [profile, setProfile] = useState(PROFILES[0].key);
  const { busy, run } = useAction();

  useEffect(() => setText(look.text), [look.text]);
  useEffect(() => setProps(look.props || ""), [look.props]);

  const suggestions = (PROP_SUGGESTIONS[g.category] || PROP_SUGGESTIONS.Other)
    .filter((sug) => !props.toLowerCase().includes(sug.toLowerCase()));
  const addProp = (sug) => setProps(props.trim() ? `${props.trim()}, ${sug}` : sug);
  // `latest` is the newest attempt of any status, so an in-flight generation is
  // visible rather than looking like a look that was never generated.
  const gen = look.latest;
  const inFlight = gen && (gen.status === "pending" || gen.status === "running");
  const shot = look.generations?.find((x) => x.status === "done" && x.output_path);
  const qc = shot?.qc;
  const repair = qc?.repair;
  const isBack = look.view === "back";

  const doGenerate = () =>
    run(async () => {
      const r = await api.generate({ look_id: look.id, image_size: imageSize, profile });
      if (r?.error) onError(r.error);
      onChange();
    });

  return (
    <div className="look-block">
      <div className="row" style={{ marginBottom: 10 }}>
        <strong className="small">{look.label}</strong>
        {isBack && <span className="badge accent">back view</span>}
        {look.source === "library" && <span className="badge neutral">from library</span>}
        {look.source === "user" && <span className="badge accent">yours</span>}
        <div className="spacer" />
        {inFlight
          ? <span className="badge accent"><span className="spin" /> generating</span>
          : <QcBadge qc={qc} />}
      </div>

      {editing ? (
        <>
          <span className="label">Pose, setting and lighting</span>
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />

          <span className="label" style={{ marginTop: 14 }}>Styling and props</span>
          <input type="text" value={props} onChange={(e) => setProps(e.target.value)}
                 placeholder="tan leather tote, fine gold hoops, espresso cup" />
          <p className="hint" style={{ marginTop: 5 }}>
            Worn, held or in the scene. Kept secondary to the garment in the prompt — nothing
            covers the product or her face.
          </p>
          {suggestions.length > 0 && (
            <div className="chips">
              {suggestions.map((sug) => (
                <button key={sug} type="button" className="chip" onClick={() => addProp(sug)}>
                  + {sug}
                </button>
              ))}
            </div>
          )}

          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn primary small" onClick={() => run(async () => {
              await api.updateLook(look.id, { text, props }); setEditing(false); onChange();
            })}>Save</button>
            <button className="btn ghost small"
                    onClick={() => {
                      setText(look.text); setProps(look.props || ""); setEditing(false);
                    }}>Cancel</button>
          </div>
        </>
      ) : (
        <>
          <p className="small muted" style={{ margin: "0 0 8px", maxWidth: "76ch" }}>{look.text}</p>
          {look.props && (
            <p className="small faint" style={{ margin: "0 0 12px", maxWidth: "76ch" }}>
              <span className="badge neutral" style={{ marginRight: 8 }}>props</span>
              {look.props}
            </p>
          )}
        </>
      )}

      <div className="row" style={{ marginBottom: 14 }}>
        {!editing && (
          <button className="btn ghost small" onClick={() => setEditing(true)}>Edit look &amp; props</button>
        )}
        <button className="btn ghost small" onClick={() => run(async () => {
          setPreview(await api.previewPrompt({ look_id: look.id, profile }));
        })}>See prompt</button>
        <select value={profile} onChange={(e) => setProfile(e.target.value)}
                style={{ width: "auto" }} title="Shoot-craft profile">
          {PROFILES.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </select>
        <select value={imageSize} onChange={(e) => setImageSize(e.target.value)}
                style={{ width: "auto" }}>
          {IMAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button className="btn primary small" disabled={busy || inFlight || !g.avatar_id}
                onClick={doGenerate}>
          {busy || inFlight ? <span className="spin" /> : shot ? "Generate again" : "Generate"}
        </button>
        {!isBack && (
          <button className="btn ghost small" disabled={busy} title="Add a matching back-view shot"
                  onClick={() => run(async () => { await api.addBackView(look.id); onChange(); })}>
            + Back shot
          </button>
        )}
        <div className="spacer" />
        <button className="btn ghost small danger"
                onClick={() => run(async () => { await api.deleteLook(look.id); onChange(); })}>
          Delete
        </button>
      </div>

      {!g.avatar_id && (
        <p className="hint" style={{ marginTop: -8 }}>Assign a model above before generating.</p>
      )}

      {inFlight && (
        <p className="hint" style={{ marginTop: -6 }}>
          <span className="spin" /> Generating this shot — it appears here when it lands.
        </p>
      )}

      {shot?.output_path && (
        <div className="row" style={{ alignItems: "flex-start", gap: 22 }}>
          <img className="shot" src={fileUrl(shot.output_path)} alt=""
               onClick={() => onZoom(shot.output_path)} style={{ width: 288 }} />
          <div style={{ flex: 1, minWidth: 260 }}>
            {qc?.checks?.map((c) => (
              <div key={c.criterion} className="check-line">
                <span className={`dot ${c.pass ? "ok" : "no"}`} />
                <span>
                  <span className="crit">{c.criterion.replace(/_/g, " ")}</span>
                  {!c.pass && <> — {c.reason}</>}
                </span>
              </div>
            ))}

            {qc && qc.overall_pass === 0 && !qc.confirmed && (
              <p className="hint">
                This failure didn't reproduce on the re-check, so it may be a misread rather than
                a real defect. Worth looking at the image yourself.
              </p>
            )}

            {repair && (
              <div className="repair">
                <strong className="small">Suggested fix — {repair.label}</strong>
                <p className="hint" style={{ marginTop: 5, marginBottom: 11 }}>{repair.detail}</p>
                <button className="btn primary small" disabled={busy} onClick={() => run(async () => {
                  await api.applyRepair({ generation_id: shot.id }); onChange();
                })}>
                  {busy ? <span className="spin" /> : "Apply and regenerate"}
                </button>
              </div>
            )}

            <div className="row" style={{ marginTop: 14 }}>
              {qc?.overall_pass === 1 && g.category && (
                <button className="btn ghost small" onClick={() => run(async () => {
                  await api.promoteLook(shot.id); onChange();
                })}>Save look to library</button>
              )}
              <button className="btn ghost small" onClick={() => run(async () => {
                await api.rerunQc(shot.id); onChange();
              })}>Re-run QC</button>
            </div>

            {look.generations.filter((x) => x.output_path).length > 1 && (
              <div style={{ marginTop: 16 }}>
                <span className="label">Earlier attempts</span>
                <div className="thumbs" style={{ marginTop: 2 }}>
                  {look.generations.filter((x) => x.output_path && x.id !== shot.id).map((old) => (
                    <div key={old.id} className="thumb" style={{ width: 70 }}>
                      <img src={fileUrl(old.output_path)} alt=""
                           style={{ width: 70, height: 92 }}
                           onClick={() => onZoom(old.output_path)} />
                      <div className="hint">#{old.attempt_no}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {gen?.status === "error" && <div className="error">{gen.error}</div>}

      {preview && (
        <Modal title="Prompt being sent" onClose={() => setPreview(null)} wide>
          <p className="hint" style={{ marginTop: 0 }}>
            {preview.ref_count} reference images, in this order:
          </p>
          <ol className="small muted" style={{ marginTop: 0 }}>
            {preview.ref_manifest.map((m) => (
              <li key={m.index}><strong>{m.kind}</strong> — {m.filename}</li>
            ))}
          </ol>
          <pre className="prompt">{preview.prompt}</pre>
        </Modal>
      )}
    </div>
  );
}
