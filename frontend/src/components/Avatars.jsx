import { useEffect, useRef, useState } from "react";
import { api, CATEGORIES, fileUrl } from "../api";
import { Field, Lightbox, Modal, useAction } from "./common";

export default function Avatars() {
  const [avatars, setAvatars] = useState(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(null);
  const [lightbox, setLightbox] = useState(null);

  const load = () => api.listAvatars().then(setAvatars).catch(() => setAvatars([]));
  // Wrapped in a block: load() returns a Promise, and React reads an effect's
  // return value as its cleanup function.
  useEffect(() => { load(); }, []);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Models</h1>
          <p>Saved for good, and reused across every session. Clothes come and go; these stay.</p>
        </div>
        <button className="btn primary" onClick={() => setCreating(true)}>New model</button>
      </div>

      {avatars?.length === 0 && (
        <div className="empty">
          <h3>No models yet</h3>
          <p>
            A model is the person your clothes get photographed on. Create one and she's saved
            for good — every future session can reuse her.
          </p>
          <button className="btn primary" onClick={() => setCreating(true)}>Create a model</button>
        </div>
      )}

      <div className="grid cols4">
        {avatars?.map((av) => (
          <div key={av.id} className="tile" onClick={() => setEditing(av)}>
            <div className="tile-img">
              <img src={fileUrl(av.front_path)} alt="" loading="lazy" />
            </div>
            <div className="tile-body">
              <div className="tile-title">{av.name}</div>
              <div className="tile-meta">
                {av.category || "Any category"}
                {av.size_variant ? ` · ${av.size_variant}` : ""}
              </div>
              {av.styling && (
                <div className="hint" style={{ marginTop: 6 }}>{av.styling}</div>
              )}
              {!av.back_path && (
                <div className="hint" style={{ marginTop: 4 }}>Front only</div>
              )}
            </div>
          </div>
        ))}
      </div>

      {creating && (
        <NewAvatar onClose={() => setCreating(false)}
                   onDone={() => { setCreating(false); load(); }} />
      )}
      {editing && (
        <EditAvatar avatar={editing} onClose={() => setEditing(null)}
                    onZoom={setLightbox}
                    onDone={() => { setEditing(null); load(); }} />
      )}
      <Lightbox path={lightbox} onClose={() => setLightbox(null)} />
    </>
  );
}

function NewAvatar({ onClose, onDone }) {
  const [mode, setMode] = useState("generate");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  const [sizeVariant, setSizeVariant] = useState("");
  const [prompt, setPrompt] = useState(null);
  const frontRef = useRef();
  const backRef = useRef();
  const { busy, error, run } = useAction();

  const showPrompt = () =>
    run(async () => {
      const r = await api.previewAvatarPrompt({ name, description, category });
      setPrompt(r.prompt);
    });

  const create = () =>
    run(async () => {
      await api.createAvatar({
        name: name.trim(), description, category: category || null,
        size_variant: sizeVariant.trim() || null, with_back: true,
      });
      onDone();
    });

  const upload = () =>
    run(async () => {
      if (!frontRef.current?.files?.length) throw new Error("A front image is required.");
      const form = new FormData();
      form.append("name", name.trim());
      if (category) form.append("category", category);
      if (sizeVariant.trim()) form.append("size_variant", sizeVariant.trim());
      form.append("front", frontRef.current.files[0]);
      if (backRef.current?.files?.length) form.append("back", backRef.current.files[0]);
      await api.uploadAvatar(form);
      onDone();
    });

  return (
    <Modal title="New model" onClose={onClose} wide>
      {error && <div className="error">{error}</div>}

      <div className="row" style={{ marginBottom: 18 }}>
        <button className={`btn small ${mode === "generate" ? "secondary" : "ghost"}`}
                onClick={() => setMode("generate")}>Generate one</button>
        <button className={`btn small ${mode === "upload" ? "secondary" : "ghost"}`}
                onClick={() => setMode("upload")}>Upload images</button>
      </div>

      <Field label="Name">
        <input type="text" value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Maya" />
      </Field>

      <Field label="Category (optional)"
             hint="Restricts which garments she's offered for. Leave blank to use her anywhere.">
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">Any category</option>
          {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </Field>

      <Field label="Size variant (optional)"
             hint="Set this to match garments with the same variant — e.g. Plus size. Without it she won't be offered for those.">
        <input type="text" value={sizeVariant} onChange={(e) => setSizeVariant(e.target.value)}
               placeholder="Plus size" />
      </Field>

      {mode === "generate" ? (
        <>
          <Field label="Who is she?"
                 hint="Age, build, ethnicity, hair, and the energy she should have. Everything about the photography itself is handled for you — warmth and an easy, natural presence are built in.">
            <textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)}
                      placeholder="Late twenties, South Asian, athletic build, long dark wavy hair, easy natural warmth" />
          </Field>
          <div className="row">
            <button className="btn primary" disabled={!name.trim() || !description.trim() || busy}
                    onClick={create}>
              {busy ? <span className="spin" /> : "Generate model"}
            </button>
            <button className="btn ghost" disabled={!description.trim() || busy}
                    onClick={showPrompt}>See prompt first</button>
          </div>
          {prompt && <pre className="prompt" style={{ marginTop: 14 }}>{prompt}</pre>}
        </>
      ) : (
        <>
          <Field label="Front image (required)">
            <input type="file" accept="image/*" ref={frontRef} />
          </Field>
          <Field label="Back image (optional)"
                 hint="Used as a second identity reference when a shot drifts off her face.">
            <input type="file" accept="image/*" ref={backRef} />
          </Field>
          <button className="btn primary" disabled={!name.trim() || busy} onClick={upload}>
            {busy ? <span className="spin" /> : "Add model"}
          </button>
        </>
      )}
    </Modal>
  );
}

function EditAvatar({ avatar, onClose, onDone, onZoom }) {
  const [styling, setStyling] = useState(avatar.styling || "");
  const [name, setName] = useState(avatar.name);
  const { busy, error, run } = useAction();

  const save = () =>
    run(async () => {
      await api.updateAvatar(avatar.id, { name: name.trim(), styling });
      onDone();
    });

  return (
    <Modal title={avatar.name} onClose={onClose} wide>
      {error && <div className="error">{error}</div>}

      <div className="thumbs" style={{ marginBottom: 20 }}>
        {[avatar.front_path, avatar.back_path].filter(Boolean).map((p) => (
          <div key={p} className="thumb">
            <img src={fileUrl(p)} alt="" onClick={() => onZoom(p)} />
          </div>
        ))}
      </div>

      <Field label="Name">
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>

      <Field label="Signature styling"
             hint="Carried into every shot she appears in — her constants, like hair and jewellery she always wears. Leave blank unless she genuinely has a look of her own; per-shot props belong on the look instead.">
        <input type="text" value={styling} onChange={(e) => setStyling(e.target.value)}
               placeholder="hair loose and centre-parted, fine gold studs" />
      </Field>

      <div className="row">
        <button className="btn primary" disabled={busy || !name.trim()} onClick={save}>
          {busy ? <span className="spin" /> : "Save"}
        </button>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <div className="spacer" />
        <button className="btn ghost small danger" disabled={busy} onClick={() => run(async () => {
          await api.updateAvatar(avatar.id, { archived: true });
          onDone();
        })}>Archive</button>
      </div>
      <p className="hint">
        Archiving hides her from the pickers without deleting anything — past shots keep working.
      </p>
    </Modal>
  );
}
