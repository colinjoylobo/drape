import { Component } from "react";

/** Without this, any render-time throw leaves a blank white page with nothing to
 *  act on — the failure mode is indistinguishable from the server being down. */
export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Drape crashed:", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{ padding: 40, maxWidth: 720 }}>
        <h1 style={{ fontSize: 22, marginBottom: 10 }}>Something broke</h1>
        <p className="muted small">
          The interface hit an error and stopped rendering. The details are below and in the
          browser console.
        </p>
        <pre className="prompt" style={{ marginTop: 16 }}>
          {String(this.state.error?.stack || this.state.error)}
        </pre>
        <button className="btn" style={{ marginTop: 16 }}
                onClick={() => this.setState({ error: null })}>
          Try again
        </button>
      </div>
    );
  }
}
