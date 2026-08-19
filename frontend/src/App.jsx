import { useState } from "react";
import Sessions from "./components/Sessions";
import SessionView from "./components/SessionView";
import GarmentView from "./components/GarmentView";
import Avatars from "./components/Avatars";
import Library from "./components/Library";

/** Simple view-stack routing. The app is a handful of screens with one drill-down
 *  path (session -> garment), which a router dependency would not simplify. */
export default function App() {
  const [view, setView] = useState({ name: "sessions" });

  const go = (name, params = {}) => setView({ name, ...params });

  const nav = (name, label) => (
    <button className={view.name === name ? "active" : ""} onClick={() => go(name)}>
      {label}
    </button>
  );

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">Drape</div>
        <div className="brand-sub">Branding shots for clothing</div>
        <nav className="nav">
          {nav("sessions", "Sessions")}
          {nav("avatars", "Models")}
          {nav("library", "Look library")}
        </nav>
        <div className="sidebar-foot">
          Every shot is judged against the real garment before you see it.
        </div>
      </aside>

      <main className="main">
        {view.name === "sessions" && (
          <Sessions onOpen={(id) => go("session", { sessionId: id })} />
        )}
        {view.name === "session" && (
          <SessionView
            sessionId={view.sessionId}
            onBack={() => go("sessions")}
            onOpenGarment={(id) => go("garment", { garmentId: id, sessionId: view.sessionId })}
          />
        )}
        {view.name === "garment" && (
          <GarmentView
            garmentId={view.garmentId}
            onBack={() => go("session", { sessionId: view.sessionId })}
          />
        )}
        {view.name === "avatars" && <Avatars />}
        {view.name === "library" && <Library />}
      </main>
    </div>
  );
}
