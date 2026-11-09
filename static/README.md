# static/

Browser assets for the viewer served by `application/web.py`.

| File | Role |
| --- | --- |
| `index.html` | Viewer markup only: sidebar (drop zone, stats, progress, re-index button, proxy toggles, reference tree, download link), the three.js `<script type="importmap">` block, and the module script references. |
| `styles.css` | All viewer styling, extracted verbatim from the former inline `<style>` block. |
| `app.js` | All viewer logic, extracted verbatim from the former `<script type="module">` block: three.js scene setup, proxy styling (placeholder boxes / see-through envelopes), drag-drop conversion flow, reference-tree rendering, and job polling. |
| `favicon.svg` | Browser tab icon. |

Serving: `/` returns `static/index.html` via `FileResponse`; `/static` is
mounted with `StaticFiles` so `styles.css` and `app.js` are served at
`/static/styles.css` and `/static/app.js`.
