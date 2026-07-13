# GeoAI TKO UI design notes

This document is the extension contract for the map workspace. It records why the layout is structured this way and how future functionality should fit without adding permanent clutter.

## Product model

The map is the primary working surface. Everything else is either:

1. a global control (period, service status);
2. a workspace mode (overview, comparison, change detection, forecast);
3. a map input (layer, opacity, geometry tool);
4. a contextual result (point, zone, transect, change, forecast).

New features should be assigned to one of those four categories before a component is added.

## Layout contract

- `TopBar` contains product identity, pointer coordinates, the global period, and service status.
- `WorkspaceNav` is the registry of major workflows. Add a mode here only when it changes the map's overall task model.
- `LayerPanel` owns map inputs and drawing tools. It can be collapsed and becomes an overlay below 1100 px.
- The map always receives the remaining space and never has a fixed width.
- `AnalysisPanel` is contextual. It is closed on first load, opens when analysis starts, and can be dismissed without deleting the result.
- Change and forecast configuration use contextual bottom bars. Controls unrelated to the active mode should remain hidden.

At compact sizes the panels overlay the map instead of shrinking it. This preserves a usable spatial canvas and keeps the same component hierarchy across breakpoints.

## Adding a future mode

1. Add one entry to the `MODES` registry in `src/components/WorkspaceNav.jsx`.
2. Add one mode state or, when modes grow further, replace the current booleans with a single `activeMode` state machine.
3. Keep mode-specific configuration in a contextual bar, sheet, or panel; do not add it permanently to the top bar.
4. Reuse `AnalysisPanel` for results or create a result component mounted inside it.
5. Supply a text label and icon, a disabled explanation when unavailable, keyboard focus styling, and a non-color-only state indicator.
6. Verify at 1280 px, 900 px, and a narrow mobile viewport.

When a fifth mode is introduced, convert `activateWorkspaceMode` to a reducer. The current UI registry is already separated from the map implementation so that refactor stays local.

## Visual system

All shared values live as CSS custom properties at the top of `src/index.css`:

- deep navy surfaces keep satellite imagery visually dominant;
- cyan is reserved for selection, focus, and interactive state;
- green communicates online/success state, not generic selection;
- spectral colors remain data colors and should not be reused for navigation;
- panel widths are separate tokens (`--panel-left-w`, `--panel-right-w`);
- cards use one border, one radius family, and restrained shadow depth.

Avoid one-off inline colors for navigation or surfaces. Data visualizations can keep semantic colors when a legend or text value communicates the same meaning.

## Accessibility contract

- All icon-only controls require an accessible name.
- Toggle buttons expose `aria-pressed`; panel toggles expose `aria-expanded` and `aria-controls`.
- Pointer targets should be at least 24 × 24 CSS pixels; primary controls in this UI target 34–46 px.
- Keyboard focus uses a 3 px high-contrast outline and must not be clipped by a panel.
- Do not rely on color alone for active, error, improvement, or degradation states.
- Loading and service states should use `role="status"` or `aria-live` where appropriate.
- Respect reduced-motion preferences. Avoid effects that delay access to content; AI output is rendered immediately rather than typed character by character.
- The map needs a non-map alternative as the product matures: downloadable tabular data already exists for reports, and future chart/map results should expose equivalent text or tables.

## Research basis

- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) for contrast, focus visibility, target sizing, and keyboard access.
- [Calcite layout patterns](https://developers.arcgis.com/calcite-design-system/foundations/layouts/) for map-first shells, dockable secondary panels, and adaptive panel placement.
- [Calcite contextual controls](https://developers.arcgis.com/calcite-design-system/sample-code/app-contextual-controls/) for revealing workflow-specific actions only when relevant.
- [Calcite workspace application](https://developers.arcgis.com/calcite-design-system/sample-code/app-workspace-application/) for preserving the spatial canvas and allowing expert users to adjust supporting content.

## Next design priorities

- Persist panel visibility, selected layer, and last mode per user.
- Add a command/search surface when the number of layers or modes becomes too large for direct scanning.
- Add undo for geometry editing before introducing more drawing tools.
- Replace emoji remaining in data-classification content with a consistent SVG icon set.
- Add visual regression snapshots for overview, point result, zone result, comparison, change, forecast, and compact layouts.
- Profile the large production bundle and split heavy PDF/report dependencies from the initial workspace load.
