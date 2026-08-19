import { useEffect, useState } from "react";
import { api } from "../api";
import { useAction } from "./common";

export default function Library() {
  const [tab, setTab] = useState("looks");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{tab === "looks" ? "Look library" : "What Drape has learned"}</h1>
          <p>
            {tab === "looks"
              ? "Looks that produced a shot which passed QC. They ground future suggestions for the same category, so the tool gets better the more you use it."
              : "Mistakes this pipeline has actually made, and the correction that fixed them. Only a fix that demonstrably worked is applied to future shots."}
          </p>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 22, gap: 6 }}>
        <button className={`btn small ${tab === "looks" ? "secondary" : "ghost"}`}
                onClick={() => setTab("looks")}>Looks</button>
        <button className={`btn small ${tab === "lessons" ? "secondary" : "ghost"}`}
                onClick={() => setTab("lessons")}>Lessons</button>
      </div>

      {tab === "looks" ? <Looks /> : <Lessons />}
    </>
  );
}

function Looks() {
  const [templates, setTemplates] = useState(null);
  const { run } = useAction();
  const load = () => api.listLibrary().then(setTemplates).catch(() => setTemplates([]));
  useEffect(() => { load(); }, []);

  const byCategory = (templates || []).reduce((acc, t) => {
    (acc[t.category] ||= []).push(t);
    return acc;
  }, {});

  if (templates?.length === 0)
    return (
      <div className="empty">
        <h3>Nothing saved yet</h3>
        <p>
          When a shot passes QC, save its look from the garment screen and it lands here —
          ready to reuse, and used to ground future suggestions in the same category.
        </p>
      </div>
    );

  return Object.entries(byCategory).map(([category, items]) => (
    <div key={category} style={{ marginBottom: 28 }}>
      <div className="section-title">{category}</div>
      {items.map((t) => (
        <div key={t.id} className="card inset">
          <p className="small" style={{ margin: "0 0 10px" }}>{t.text}</p>
          <div className="row">
            {t.scene_tag && <span className="badge neutral">{t.scene_tag}</span>}
            <span className="small faint">used {t.times_used}×</span>
            <div className="spacer" />
            <button className="btn ghost small danger"
                    onClick={() => run(async () => { await api.deleteTemplate(t.id); load(); })}>
              Remove
            </button>
          </div>
        </div>
      ))}
    </div>
  ));
}

function Lessons() {
  const [lessons, setLessons] = useState(null);
  const { run } = useAction();
  const load = () => api.listLessons().then(setLessons).catch(() => setLessons([]));
  useEffect(() => { load(); }, []);

  if (lessons?.length === 0)
    return (
      <div className="empty">
        <h3>Nothing learned yet</h3>
        <p>
          A lesson is earned when a shot fails QC, you apply the suggested fix, and the next
          attempt passes. Until a fix is proven it is only counted, never applied.
        </p>
      </div>
    );

  const proven = (lessons || []).filter((l) => l.times_proven > 0);
  const observed = (lessons || []).filter((l) => l.times_proven === 0);

  const row = (l) => (
    <div key={l.id} className="card inset">
      <div className="row" style={{ marginBottom: 8 }}>
        <span className={`badge ${l.scope === "global" ? "accent" : "neutral"}`}>
          {l.scope === "global" ? "all garments" : l.category}
        </span>
        <strong className="small">{l.criterion.replace(/_/g, " ")}</strong>
        <div className="spacer" />
        {l.times_proven > 0 ? (
          <span className="badge pass">fixed {l.times_proven}×</span>
        ) : (
          <span className="badge warn">seen {l.times_seen}× · not yet proven</span>
        )}
      </div>
      {l.guidance ? (
        <p className="small muted" style={{ margin: "0 0 10px" }}>{l.guidance}</p>
      ) : (
        <p className="small faint" style={{ margin: "0 0 10px" }}>
          {l.last_reason || "No successful fix recorded yet, so nothing is applied."}
        </p>
      )}
      <div className="row">
        {l.times_proven > 0 && (
          <button className="btn ghost small" onClick={() => run(async () => {
            await api.updateLesson(l.id, { enabled: !l.enabled }); load();
          })}>
            {l.enabled ? "Stop applying this" : "Apply this again"}
          </button>
        )}
        <div className="spacer" />
        <button className="btn ghost small danger"
                onClick={() => run(async () => { await api.deleteLesson(l.id); load(); })}>
          Forget
        </button>
      </div>
    </div>
  );

  return (
    <>
      {proven.length > 0 && (
        <div style={{ marginBottom: 28 }}>
          <div className="section-title">Applied to new shots</div>
          {proven.map(row)}
        </div>
      )}
      {observed.length > 0 && (
        <div>
          <div className="section-title">Watching — not applied yet</div>
          <p className="hint" style={{ marginTop: -8, marginBottom: 14 }}>
            These failures have been seen but no fix has been proven for them, so nothing is
            added to prompts. Apply a suggested repair on a failed shot to promote one.
          </p>
          {observed.map(row)}
        </div>
      )}
    </>
  );
}
