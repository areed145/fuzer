# UI style guide

The UI uses a small local component kit in `ui/src/ui/`. Use it for shared page structure and controls; keep specialized visualizations and editor integrations in their own components.

## Components

- `PageHeader` is the page title and optional subtitle/actions. Each page has one `PageHeader` and one `h1`; use `h2` for page sections and `h3` inside a section.
- `Button` uses `primary` for the main action, `secondary` for ordinary actions, `ghost` for low emphasis, `link` for inline actions, and `danger` for destructive actions. Use `sm` for compact table actions and `md` elsewhere. Use `ButtonLink` for navigation styled as a button and `ButtonLabel` for labels that open hidden file inputs.
- `Card` (also exported as `Section`) groups related content. Supply `title` and `actions` when the group needs a heading or controls.
- `Table` provides the shared scroll container and table base. Use it for data tables and choose a compact density for dense lists; use specialized classes only for domain-specific layout.
- `Field`, `Input`, and `Select` keep form controls aligned. Widths are `xs`, `sm`, `md`, `lg`, and `full`; prefer `full` for long text and `sm` or `xs` for short values.
- `Badge` shows a compact status with `ok`, `warn`, `danger`, or `muted` tone.
- `Stat` presents a summary number; `EmptyState` explains an empty result; `Bar` displays a percentage or other bounded value from 0 to 100.

## Layout and tokens

Use the existing spacing rhythm: 4px for tight inline gaps, 8px for control groups, 12–16px between related blocks, and 20–24px between major page areas. Keep page content within the app's existing 1200px reading width.

Use theme variables for color: `--bg`, `--panel`, `--text`, `--muted`, `--line`, `--accent`, `--accent-soft`, `--ok`, `--warn`, and `--danger`. Entity graph colors use `--kind-*`; map services and points use `--map-*`. Do not add literal colors to TSX.

Tailwind v4 utilities and colors mapped from these variables are available throughout the UI. Its Preflight reset is intentionally omitted so existing pages remain stable while they move onto the shared components.

Keep `ui/src/styles.css` limited to theme tokens and app-wide base styles. Put shared primitive styles in `ui/src/styles/ui-kit.css`; keep page and visualization styles in `ui/src/styles/` and import them from the component that owns the markup.
