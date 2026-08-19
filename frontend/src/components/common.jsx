import { useEffect, useState } from "react";
import { fileUrl } from "../api";

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
