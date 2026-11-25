# Plan for F01-T12: Site baseline

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

## Criteria

### AC1

**Mark:** human review

**Scenario:** Given the page served from `zorzo-site` and the local `site/` page (the source of this task),
when the reviewer opens both side by side at 1440 px and 390 px, then the hero (intro, brain, labels, hub
cards, question box, language switch, credit) and the content below it (career, work, projects, contact)
match in layout, text and interaction, and every brain effect of today is there: the dust with the gyri
brightness, the chunk points, the gold entity points inside, the axons, the hubs with labels and cards,
the idle sparks, the impulse wave on click and on a question, the drag, tilt and sway, the token flight,
the citation lines to the brain and the "graph · …" step. Screenshots of both pages at both widths and a
short recording of a question are kept in this task's folder (`screenshots/`).

### AC2

**Mark:** human review

**Scenario:** Given the diff, when the reviewer reads `assets/js/`, then the brain scene, the interface and
the data access live in separate modules with the dependency direction of section "Decisions" (interface →
data, interface → scene; the scene and the data never import the interface), and the interface reaches
answers only through `ask(question, {signal})` in the data module, which today is the local mock.

### AC3

**Mark:** Playwright test

**Scenario:** Given a browser where WebGL is unavailable, when a visitor opens the page, types a question
and submits it, then the still image shows instead of the brain, and the question box, the steps panel and
the answer panel work as with WebGL. The same holds with `prefers-reduced-motion: reduce` (the still image
shows even when WebGL exists).

**Planned test:** Playwright, `tests/e2e/fallback.spec.ts`, "asks without WebGL", "asks with reduced
motion".

### AC4

**Mark:** Playwright test

**Scenario:** Given the published data of the fixture corpus (produced by the real ingest of
`zorzo-api/tests/fixtures/corpus` with the fake embedder, copied to `tests/fixtures/data/`), when a visitor
opens the page, then the page fetches `data/manifest.json` and then `corpus.json` and `examples.json` from
the folder the manifest names; a question matching a fixture document shows that document's title among
the passages; and the example questions shown are the fixture's ones in the interface language, switching
with it.

**Planned test:** Playwright, `tests/e2e/data.spec.ts`, "reads the published data through the manifest",
"shows the example questions of the interface language".

### AC5

**Mark:** document

**Scenario:** Given `zorzo-site`, when the reviewer reads `docs/lld.md` and `AGENTS.md`, then the LLD holds
the stack, the module layout and its dependency rule, the brain's data contract (how a published chunk
becomes a point), the fallback without 3D, and the licenses (brain mesh CC BY-SA 4.0, font OFL); `AGENTS.md`
has the commands to serve the site locally, refresh the development data and run the tests; and the CI
workflow runs the Playwright tests on every pull request, green on this task's PR.

## Contract

The contract doesn't change: no route, stream event or table is created or changed. The front starts
consuming the published data of LLD "Published data" (owned by 02) as it is.

## Contract verification

- Published data, `manifest.json` → `corpus.json` and `examples.json`: AC4.

## Decisions

- **Source:** the local `site/` folder (hero and content below), not `img2html/` (the workbench copies).
  `site/_old/` (the previous résumé page) is not imported; it stays archived in the local folder.
- **Stack:** static HTML, CSS and ES modules, no build step and no framework; the page is served as files
  (S3 in production, `python3 -m http.server` locally). Node and npm only for the tests
  (`@playwright/test`, pinned). Why: the prototype already works this way, and a bundler adds a toolchain
  for a single page.
- **Single origin (ADR 0003):** Three.js (pinned 0.170.0, `three.module.min.js`) and the JetBrains Mono
  font files are vendored into the repo instead of loaded from jsDelivr and Google Fonts. The browser talks
  only to `zorzo.dev.br`: no third-party request on page load, no outage or tracking from a CDN.
- **Module layout and dependency rule:**
  - `assets/js/main.js`: boot (language, data load, scene start or fallback);
  - `assets/js/scene/`: the brain (geometry, render loop, picking, impulses), exposing only
    `createScene(canvas, brain, corpus)` with `light(chunkIds)`, `impulse(chunkId)`, `onPick(fn)`,
    `pause()`, `resume()`;
  - `assets/js/ui/`: question box, steps panel, answer panel, hub cards, intro, language;
  - `assets/js/data/`: `loadPublished()` (manifest → corpus and examples) and `ask(question, {signal})`.
  - The interface may import the scene and the data; the scene and the data never import the interface,
    and the scene never imports the data. The interface works when the scene is absent (AC3).
- **The mock speaks the stream vocabulary:** `ask()` returns an async iterator of events shaped like
  `contracts/sse-v1.schema.json` (`stage`, `retrieval`, then `degraded` with the passages, `done`), produced
  locally by the existing BM25 search. 03 then swaps the transport (fetch and the SSE reader) without
  touching the interface. Consequence: the answer panel shows matching passages with title and link
  instead of today's extractive "answer", which was a fake of an AI answer; each passage keeps its
  citation line to its point in the brain. Rejected: keeping today's panel here and leaving the change to
  03, which would then touch the interface too.
- **Brain data contract (how a published chunk becomes a point):**
  - `brain.json` keeps what comes from the mesh: dust points, brightness, hubs, and a pool of candidate
    surface points per region. It no longer holds one anchor per chunk, the entities or the edges.
  - In the browser, each chunk goes to a region by its `kind`: `resume`, `narrative` and `faq` → frontal;
    `project_doc` and `repo` → temporal; `site_doc` → parietal; `post` → occipital. Within a region, chunks
    take free pool points in the order of their direction in `p`, so similar chunks sit close.
  - Axons link each chunk to its 2 nearest neighbours in `p`, drawn as the same inward curves (today's
    edges were nearest neighbours too, from TF-IDF).
  - **Entities stay, as front data:** the 35 entities of today (companies, technologies, concepts,
    projects: names and types) move to `assets/data/entities.json` in `zorzo-site`. At load, an entity
    links to every chunk whose heading or text names it (case- and accent-insensitive, whole words), and
    sits inside the brain near those chunks, as `build_brain.py` places them today. The gold points, the
    entity firing, the token flight to an entity and the "graph · …" step keep working, with no change to
    02's published data. Why front data: they are presentation (which names light up), not retrieval.
  - `tools/build_brain.py` is updated to write the new `brain.json` and imported with `pack_brain.py` and
    `make_poster.py`; the 43 MB source mesh is not committed (the tool documents where to download it).
- **Development data keeps today's brain:** today's mock corpus (127 chunks from the
  public sources already on the site: résumé, spec, Prosa, GitHub) is converted once to the published
  format and committed as `demo-data/`, which the local server uses when `data/` (copied from the API's
  `DATA_DIR`) is absent. The brain then looks as it does today until the real corpus exists. The tests
  keep the fixture from the real ingest (`tests/fixtures/data/`), served by request interception. AC4's
  last clause changed accordingly (approved). Rejected: development on the fixture alone, which shows only
  7 chunk points, a visible loss.
- **Licenses:** the repo stays MIT; `assets/data/brain.json` is CC BY-SA 4.0 (derived from the NIH 3D mesh)
  and the font OFL, each with its license file next to it and the credit line kept on the page.
- **No fallback hiding the box:** today, without WebGL the question box is hidden because the search
  needs the scene. After the split, the fallback only swaps the canvas for the still image.

## Files and interfaces

zorzo-site:

- `index.html`: the page, with the module entry `assets/js/main.js` and the local font and Three.js.
- `assets/css/styles.css`, `assets/fonts/` (JetBrains Mono 400 and 500, OFL), `assets/vendor/three.module.min.js`.
- `assets/js/main.js`, `assets/js/scene/*.js`, `assets/js/ui/*.js`, `assets/js/data/published.js`
  (`loadPublished()`), `assets/js/data/mock.js` (`ask(question, {signal})`, async iterator of events).
- `assets/data/brain.json` (new format) and its `LICENSE`; `assets/data/entities.json`; `assets/poster.webp`;
  `andre-zorzo-resume.pdf`; `demo-data/` (manifest, corpus, examples) and `tools/convert_mock_corpus.py`.
- `tools/build_brain.py`, `tools/pack_brain.py`, `tools/make_poster.py`.
- `tests/e2e/fallback.spec.ts`, `tests/e2e/data.spec.ts`, `tests/fixtures/data/` (manifest, corpus,
  examples), `playwright.config.ts`, `package.json`, `package-lock.json`.
- `.github/workflows/ci.yml`: Playwright on pull requests.
- `docs/lld.md` (new), `AGENTS.md` (commands, code practices), `.gitignore` (`data/`, `node_modules/`).

## Existing tests that change

None (`zorzo-site` has no tests yet).

## Data change

None.

## Updated documents

- `docs/lld.md`, section "Published data": the front maps `p` into the brain per `zorzo-site`'s LLD,
  section "Brain data".
- `docs/lld.md`, "Where each fact lives": a row for the front's rendering and brain data, owned by
  `zorzo-site`'s `docs/lld.md`.
- `AGENTS.md`, "Planning": a task added after the breakdown takes the next free number (this task).
- `tasks/03-web-foundation/task.md`: blocked by 12 and consumes its decisions.
