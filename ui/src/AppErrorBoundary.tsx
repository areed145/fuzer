import { Component, type ReactNode } from "react";
import { forgetSnapshot, recalledAny } from "./snapshot";
import { Button, Card, PageHeader, ErrorText } from "./ui";

export class AppErrorBoundary extends Component<{ children: ReactNode }, { error?: Error }> {
  state: { error?: Error } = {};

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("Fuzer page rendering failed", error);
    // An answer kept by an earlier version of the app may be what failed to draw (snapshot.ts): forget the kept
    // answers and draw once more from the server's.
    if (recalledAny() && !this.retried) {
      this.retried = true;
      forgetSnapshot();
      this.setState({ error: undefined });
    }
  }
  private retried = false;

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main>
        <PageHeader record>This page could not be displayed</PageHeader>
        <Card>
          <p role="alert">An unexpected interface error occurred. Your saved facility data has not been cleared.</p>
          <ErrorText>{this.state.error.message}</ErrorText>
          <Button onClick={() => window.location.reload()}>Reload page</Button>{" "}
          <Button onClick={() => { window.location.hash = "#/"; window.location.reload(); }}>Return home</Button>
        </Card>
      </main>
    );
  }
}
