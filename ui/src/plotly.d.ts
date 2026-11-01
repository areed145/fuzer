// plotly.js ships no types for its dist bundles; Fuzer uses only these calls.
declare module "plotly.js-cartesian-dist-min" {
  const Plotly: {
    react(el: HTMLElement, data: unknown[], layout: Record<string, unknown>, config?: Record<string, unknown>): Promise<unknown>;
    purge(el: HTMLElement): void;
  };
  export default Plotly;
}
