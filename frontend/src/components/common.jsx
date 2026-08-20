import { useEffect, useState } from "react";
import { api, fileUrl } from "../api";

export function Lightbox({ path, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (!path) return null;
  return (
    <div className="lightbox" onClick={onClose}>
      <img src={fileUrl(path)} alt="" onClick={(e) => e.stopPropagation()} />
      <div className="row">
        <a className="btn" href={fileUrl(path)} download onClick={(e) => e.stopPropagation()}>
          Download
        </a>
        <button className="btn secondary" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

export function Modal({ title, children, onClose, wide }) {
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" style={wide ? { maxWidth: 760 } : undefined}
           onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <div className="hint">{hint}</div>}
    </label>
  );
}

export function QcBadge({ qc }) {
  if (!qc) return <span className="badge neutral">Not judged</span>;
  if (qc.overall_pass === 1 || qc.overall_pass === true)
    return <span className="badge pass">QC pass</span>;
  if (qc.overall_pass === null || qc.overall_pass === undefined)
    return <span className="badge warn">QC error</span>;
  // An unconfirmed failure did not reproduce on re-check, so it is flagged as
  // uncertain rather than presented as a defect.
  return <span className="badge fail">{qc.confirmed ? "QC fail" : "QC fail?"}</span>;
}

/** Wraps an async action with its own pending + error state, so every button in
 *  the app reports failure in place instead of failing silently. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const run = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, setError, run };
}

/** Confirmation for deleting a session.
 *
 *  Generated shots cost credits, so the dialogue states exactly what will be lost
 *  before the button exists, and says plainly what survives — models, saved looks
 *  and lessons are the things people worry about and they are untouched.
 *
 *  A session holding finished shots requires its name to be typed. An empty one
 *  does not: ceremony should be proportional to what is actually being destroyed.
 */
export function DeleteSession({ sessionId, onClose, onDeleted }) {
  const [preview, setPreview] = useState(null);
  const [typed, setTyped] = useState("");
  const { busy, error, run } = useAction();

  useEffect(() => {
    api.deletionPreview(sessionId).then(setPreview).catch(() => setPreview(null));
  }, [sessionId]);

  if (!preview)
    return (
      <Modal title="Delete session" onClose={onClose}>
        <p className="muted small">Checking what this would remove…</p>
      </Modal>
    );

  const needsTyping = preview.shots > 0;
  const confirmed = !needsTyping || typed.trim() === preview.name;
  const mb = preview.bytes / 1e6;

  return (
    <Modal title={`Delete “${preview.name}”?`} onClose={onClose}>
      {error && <div className="error">{error}</div>}

      <p className="small" style={{ marginTop: 0 }}>This permanently removes:</p>
      <ul className="small muted" style={{ marginTop: 6 }}>
        <li>{preview.garments} garment{preview.garments === 1 ? "" : "s"} and their photos</li>
        <li>{preview.looks} look{preview.looks === 1 ? "" : "s"}</li>
        <li>
          <strong>{preview.shots} generated shot{preview.shots === 1 ? "" : "s"}</strong>
          {preview.shots > 0 && " — these cost credits and cannot be recovered"}
        </li>
        <li>{preview.files} file{preview.files === 1 ? "" : "s"} on disk
          {mb >= 0.1 && ` (${mb.toFixed(mb >= 10 ? 0 : 1)} MB)`}</li>
      </ul>

      <p className="small muted">
        Your {preview.kept.join(", ")} are kept — they are not part of this session.
      </p>

      {needsTyping && (
        <Field label={`Type “${preview.name}” to confirm`}>
          <input type="text" autoFocus value={typed} placeholder={preview.name}
                 onChange={(e) => setTyped(e.target.value)} />
        </Field>
      )}

      <div className="row" style={{ marginTop: 16 }}>
        <button className="btn danger-solid" disabled={!confirmed || busy}
                onClick={() => run(async () => {
                  await api.deleteSession(sessionId);
                  onDeleted();
                })}>
          {busy ? <span className="spin" /> : "Delete permanently"}
        </button>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}
