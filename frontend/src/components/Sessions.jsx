import { useEffect, useState } from "react";
import { api, fileUrl } from "../api";
import { Field, Modal, useAction } from "./common";

export default function Sessions({ onOpen }) {
  const [sessions, setSessions] = useState(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const { busy, error, run } = useAction();

  const load = () => api.listSessions().then(setSessions).catch(() => setSessions([]));
  // Wrapped in a block: load() returns a Promise, and React reads an effect's
  // return value as its cleanup function.
  useEffect(() => { load(); }, []);

  const create = () =>
    run(async () => {
      const s = await api.createSession(name.trim());
      setCreating(false);
      setName("");
      onOpen(s.id);
    });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Sessions</h1>
          <p>
            One session per batch of clothes. Your models and saved looks carry across
            all of them.
          </p>
        </div>
        <button className="btn primary" onClick={() => setCreating(true)}>New session</button>
      </div>

      {sessions === null && <div className="empty">Loading…</div>}

      {sessions?.length === 0 && (
        <div className="empty">
          <h3>Nothing here yet</h3>
          <p>
            A session holds one batch of clothes from upload through to finished shots.
            Start one and drop your product photos in.
          </p>
          <button className="btn primary" onClick={() => setCreating(true)}>
            Start your first session
          </button>
        </div>
      )}

      <div className="grid cols3">
        {sessions?.map((s) => (
          <div key={s.id} className="tile" onClick={() => onOpen(s.id)}>
            <div className="strip">
              {(s.preview?.length ? s.preview : [null]).slice(0, 4).map((p, i) =>
                p ? <img key={i} src={fileUrl(p)} alt="" loading="lazy" />
                  : <div key={i} style={{ flex: 1, height: 116, background: "var(--surface-3)" }} />
              )}
            </div>
            <div className="tile-body">
              <div className="tile-title">{s.name}</div>
              <div className="tile-meta">
                {s.garment_count === 0
                  ? "No clothes yet"
                  : `${s.garment_count} garment${s.garment_count === 1 ? "" : "s"}`}
              </div>
              <div className="row" style={{ marginTop: 10, gap: 6 }}>
                {s.passed_count > 0 && (
                  <span className="badge pass">{s.passed_count} shots</span>
                )}
                <span className="badge neutral">{s.created_at?.slice(0, 10)}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {creating && (
        <Modal title="New session" onClose={() => setCreating(false)}>
          {error && <div className="error">{error}</div>}
          <Field label="Session name"
                 hint="Whatever helps you find it later — a drop name, a date, a client brief.">
            <input type="text" autoFocus value={name} onChange={(e) => setName(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && name.trim() && create()}
                   placeholder="August drop" />
          </Field>
          <div className="row">
            <button className="btn primary" disabled={!name.trim() || busy} onClick={create}>
              {busy ? <span className="spin" /> : "Create session"}
            </button>
            <button className="btn ghost" onClick={() => setCreating(false)}>Cancel</button>
          </div>
        </Modal>
      )}
    </>
  );
}
