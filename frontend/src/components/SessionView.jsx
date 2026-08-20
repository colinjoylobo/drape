import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, CATEGORIES, fileUrl } from "../api";
import { DeleteSession, Field, Lightbox, Modal, useAction } from "./common";

const SORTS = [
  { key: "added", label: "Date added" },
  { key: "name", label: "Name" },
  { key: "category", label: "Garment type" },
  { key: "model", label: "Model" },
  { key: "status", label: "Needs attention" },
];

export default function SessionView({ sessionId, onBack, onOpenGarment }) {
  const [session, setSession] = useState(null);
  const [shots, setShots] = useState([]);
  const [avatars, setAvatars] = useState([]);
  const [progress, setProgress] = useState(null);
  const [tab, setTab] = useState("garments");
  const [sort, setSort] = useState("added");
  const [selected, setSelected] = useState([]);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busyIds, setBusyIds] = useState([]);
  const [notice, setNotice] = useState(null);
  const [lightbox, setLightbox] = useState(null);
  const { error, setError, run } = useAction();

  const load = useCallback(async () => {
    try {
      setSession(await api.getSession(sessionId));
      setShots(await api.sessionShots(sessionId));
    } catch (e) {
      setError(e.message);
    }
  }, [sessionId, setError]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.listAvatars().then(setAvatars).catch(() => {}); }, []);

  // Poll while anything is in flight, and stop as soon as it settles — a click on
  // "Generate" previously had no visible consequence for minutes.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const p = await api.sessionProgress(sessionId);
        if (!alive) return;
        setProgress(p);
        if (p.in_flight > 0) setTimeout(tick, 4000);
        else load();
      } catch { /* transient; the next user action will refresh */ }
    };
    tick();
    return () => { alive = false; };
  }, [sessionId, load]);

  const garments = session?.garments || [];
  const modelName = useMemo(
    () => Object.fromEntries(avatars.map((a) => [a.id, a.name])), [avatars]);

  const sorted = useMemo(() => {
    const list = [...garments];
    const byName = (a, b) => a.name.localeCompare(b.name);
    if (sort === "name") list.sort(byName);
    if (sort === "category")
      list.sort((a, b) => (a.category || "~").localeCompare(b.category || "~") || byName(a, b));
    if (sort === "model")
      list.sort((a, b) => (modelName[a.avatar_id] || "~")
        .localeCompare(modelName[b.avatar_id] || "~") || byName(a, b));
    if (sort === "status") {
      // Anything blocking work first, finished work last.
      const rank = (g) => g.status === "uploaded" ? 0 : !g.avatar_id ? 1
        : g.passed_count < g.look_count ? 2 : 3;
      list.sort((a, b) => rank(a) - rank(b) || byName(a, b));
    }
    return list;
  }, [garments, sort, modelName]);

  const unanalyzed = garments.filter((g) => g.status === "uploaded");
  const needAvatar = garments.filter((g) => !g.avatar_id);
  const toggle = (id) =>
    setSelected((s) => s.includes(id) ? s.filter((x) => x !== id) : [...s, id]);

  const analyzeAll = () =>
    run(async () => {
      const targets = selected.length
        ? unanalyzed.filter((g) => selected.includes(g.id)) : unanalyzed;
      for (const g of targets) {
        setBusyIds((b) => [...b, g.id]);
        try {
          await api.analyze(g.id);
        } catch (e) {
          setError(`${g.name}: ${e.message}`);
        } finally {
          setBusyIds((b) => b.filter((x) => x !== g.id));
          await load();
        }
      }
    });

  const generate = (ids) =>
    run(async () => {
      const r = await api.generateBatch({
        session_id: sessionId,
        garment_ids: ids?.length ? ids : undefined,
      });
      setNotice(r.note);
      setProgress(await api.sessionProgress(sessionId));
      if (r.queued) setTimeout(async () => setProgress(await api.sessionProgress(sessionId)), 2500);
    });

  if (!session) return <div className="empty">Loading…</div>;

  return (
    <>
      <button className="backlink" onClick={onBack}>← All sessions</button>

      <div className="page-head">
        <div>
          <h1>{session.name}</h1>
          <p>
            {garments.length} garment{garments.length === 1 ? "" : "s"}
            {shots.length > 0 && ` · ${shots.length} shots`}
            {unanalyzed.length > 0 && ` · ${unanalyzed.length} not analysed`}
            {needAvatar.length > 0 && ` · ${needAvatar.length} without a model`}
          </p>
        </div>
        <div className="row">
          <button className="btn secondary" onClick={() => setAdding(true)}>Add clothes</button>
          {unanalyzed.length > 0 && (
            <button className="btn secondary" onClick={analyzeAll}>
              Analyse {selected.length
                ? unanalyzed.filter((g) => selected.includes(g.id)).length : unanalyzed.length}
            </button>
          )}
          <button className="btn primary" onClick={() => generate(null)}>Generate all</button>
          <a className="btn ghost" href={`/api/export/session/${sessionId}`} target="_blank"
             rel="noreferrer">Export</a>
          <button className="btn ghost danger" onClick={() => setDeleting(true)}>Delete</button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {progress?.in_flight > 0 && (
        <div className="banner">
          <span className="spin" />
          <strong>Generating {progress.in_flight} shot{progress.in_flight === 1 ? "" : "s"}…</strong>
          <span className="faint small">
            {progress.done} finished so far. You can keep working — this updates itself.
          </span>
        </div>
      )}

      {notice && !progress?.in_flight && (
        <div className="banner quiet">
          {notice}
          <div className="spacer" />
          <button className="btn ghost small" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}

      {garments.length === 0 && (
        <div className="empty">
          <h3>No clothes in this session</h3>
          <p>Upload a garment's product photos, or import a folder with one subfolder per garment.</p>
          <button className="btn primary" onClick={() => setAdding(true)}>Add clothes</button>
        </div>
      )}

      {garments.length > 0 && (
        <div className="toolbar">
          <div className="row" style={{ gap: 6 }}>
            <button className={`btn small ${tab === "garments" ? "secondary" : "ghost"}`}
                    onClick={() => setTab("garments")}>Garments</button>
            <button className={`btn small ${tab === "shots" ? "secondary" : "ghost"}`}
                    onClick={() => setTab("shots")} disabled={!shots.length}>
              All shots{shots.length ? ` (${shots.length})` : ""}
            </button>
          </div>
          <div className="spacer" />
          {tab === "garments" && (
            <>
              <span className="label" style={{ margin: 0 }}>Sort</span>
              <select value={sort} onChange={(e) => setSort(e.target.value)}
                      style={{ width: "auto" }}>
                {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
              </select>
            </>
          )}
        </div>
      )}

      {tab === "garments" && selected.length > 0 && (
        <div className="banner accent">
          <strong>{selected.length} selected</strong>
          <div className="spacer" />
          <button className="btn primary small" onClick={() => generate(selected)}>
            Generate selected
          </button>
          <button className="btn ghost small" onClick={() => setSelected([])}>Clear</button>
        </div>
      )}

      {tab === "shots" ? (
        <div className="grid cols4">
          {shots.map((s) => (
            <div key={s.id} className="tile" onClick={() => setLightbox(s.output_path)}>
              <div className="tile-img">
                <img src={fileUrl(s.output_path)} alt="" loading="lazy" />
              </div>
              <div className="tile-body">
                <div className="tile-title">{s.garment_name}</div>
                <div className="tile-meta">{s.category}</div>
                {s.overall_pass === 1 && (
                  <span className="badge pass" style={{ marginTop: 9 }}>QC pass</span>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="grid cols4">
          {sorted.map((g) => (
            <div key={g.id} className={`tile ${selected.includes(g.id) ? "picked" : ""}`}>
              <div className="tile-img" onClick={() => onOpenGarment(g.id)}>
                {g.thumbnail ? (
                  <img src={fileUrl(g.thumbnail)} alt="" loading="lazy" />
                ) : (
                  <div style={{ aspectRatio: "3/4", background: "var(--surface-3)" }} />
                )}
                <button className="pick" onClick={(e) => { e.stopPropagation(); toggle(g.id); }}
                        title="Select for generation">
                  {selected.includes(g.id) ? "✓" : ""}
                </button>
              </div>
              <div className="tile-body" onClick={() => onOpenGarment(g.id)}
                   style={{ cursor: "pointer" }}>
                <div className="tile-title">{g.name}</div>
                <div className="tile-meta">
                  {g.category || "No category"}
                  {g.avatar_id ? ` · ${modelName[g.avatar_id] || "model"}` : ""}
                </div>
                <div className="row" style={{ marginTop: 10, gap: 6 }}>
                  {busyIds.includes(g.id) ? (
                    <span className="badge accent"><span className="spin" /> analysing</span>
                  ) : g.status === "uploaded" ? (
                    <span className="badge neutral">Not analysed</span>
                  ) : !g.avatar_id ? (
                    <span className="badge warn">Needs a model</span>
                  ) : g.passed_count > 0 ? (
                    <span className="badge pass">{g.passed_count}/{g.look_count} passed</span>
                  ) : (
                    <span className="badge neutral">{g.look_count} looks ready</span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {adding && (
        <AddClothes sessionId={sessionId} onClose={() => setAdding(false)}
                    onDone={() => { setAdding(false); load(); }} />
      )}
      {deleting && (
        <DeleteSession sessionId={sessionId} onClose={() => setDeleting(false)}
                       onDeleted={onBack} />
      )}
      <Lightbox path={lightbox} onClose={() => setLightbox(null)} />
    </>
  );
}

function AddClothes({ sessionId, onClose, onDone }) {
  const [mode, setMode] = useState("upload");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [sizeVariant, setSizeVariant] = useState("");
  const [root, setRoot] = useState("");
  const [result, setResult] = useState(null);
  const fileRef = useRef();
  const { busy, error, run } = useAction();

  const upload = () =>
    run(async () => {
      const files = fileRef.current?.files;
      if (!files?.length) throw new Error("Choose the product photos for this garment first.");
      const form = new FormData();
      form.append("name", name.trim());
      if (category) form.append("category", category);
      if (sizeVariant.trim()) form.append("size_variant", sizeVariant.trim());
      for (const f of files) form.append("files", f);
      await api.uploadGarment(sessionId, form);
      onDone();
    });

  const importFolder = () =>
    run(async () => {
      const r = await api.importFolder(sessionId, root.trim(), category || undefined);
      setResult(`Added ${r.created.length} garments.`);
      onDone();
    });

  return (
    <Modal title="Add clothes" onClose={onClose} wide>
      {error && <div className="error">{error}</div>}
      {result && <div className="card inset small" style={{ marginBottom: 16 }}>{result}</div>}

      <div className="row" style={{ marginBottom: 20, gap: 6 }}>
        <button className={`btn small ${mode === "upload" ? "secondary" : "ghost"}`}
                onClick={() => setMode("upload")}>Upload one garment</button>
        <button className={`btn small ${mode === "folder" ? "secondary" : "ghost"}`}
                onClick={() => setMode("folder")}>Import a folder</button>
      </div>

      <Field label="Category"
             hint="Sets which saved looks are offered and which models suit it. Changeable later.">
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">Choose a category…</option>
          {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </Field>

      {mode === "upload" ? (
        <>
          <Field label="Garment name" hint="A SKU or a short description — whatever you'll recognise.">
            <input type="text" value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="Navy ditsy floral crop top" />
          </Field>
          <Field label="Size variant (optional)"
                 hint="Only if this needs a different body type — e.g. Plus size. It gets its own model rather than reusing the standard one.">
            <input type="text" value={sizeVariant} onChange={(e) => setSizeVariant(e.target.value)}
                   placeholder="Plus size" />
          </Field>
          <Field label="Product photos"
                 hint="Every angle you have, including the back. Photos of someone wearing the garment are fine — the garment is cropped out so their face never reaches the generator.">
            <input type="file" multiple accept="image/*" ref={fileRef} />
          </Field>
          <button className="btn primary" disabled={!name.trim() || busy} onClick={upload}>
            {busy ? <span className="spin" /> : "Add garment"}
          </button>
        </>
      ) : (
        <>
          <Field label="Folder path"
                 hint="One subfolder per garment, each holding that garment's photos. Files stay where they are — nothing is copied.">
            <input type="text" value={root} onChange={(e) => setRoot(e.target.value)}
                   placeholder="/Users/you/shoots/august" />
          </Field>
          <button className="btn primary" disabled={!root.trim() || busy} onClick={importFolder}>
            {busy ? <span className="spin" /> : "Import folder"}
          </button>
        </>
      )}
    </Modal>
  );
}
