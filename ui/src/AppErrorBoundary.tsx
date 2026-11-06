import { Component, type ReactNode } from "react";
import { Button, Card, PageHeader } from "./ui";

export class AppErrorBoundary extends Component<{ children: ReactNode }, { error?: Error }> {
  state: { error?: Error } = {};

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("Fuzer page rendering failed", error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main>
        <PageHeader>This page could not be displayed</PageHeader>
        <Card>
          <p role="alert">An unexpected interface error occurred. Your saved facility data has not been cleared.</p>
          <p className="error small">{this.state.error.message}</p>
          <Button onClick={() => window.location.reload()}>Reload page</Button>{" "}
          <Button onClick={() => { window.location.hash = "#/"; window.location.reload(); }}>Return home</Button>
        </Card>
      </main>
    );
  }
}
