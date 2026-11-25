# Visual check (AC1)

The page served from `zorzo-site` (branch of F01-T12) next to the local `site/` folder it came from, on
2026-11-25, headless Chromium with software WebGL (SwiftShader), locale pt-BR. Local data: the demo copy of
the prototype's corpus (127 chunks), so the brain has the same chunk points as before.

| File | What it shows |
| --- | --- |
| `screenshots/before-1440.png`, `screenshots/after-1440.png` | Hero at 1440 × 900: intro, brain, hub labels, box, language switch, credit |
| `screenshots/before-1440-question.png`, `screenshots/after-1440-question.png` | "Quantos anos de AWS ele tem?": steps panel, lit chunks and entities, answer panel |
| `screenshots/before-390.png`, `screenshots/after-390.png` | Phone at 390 × 844 |
| `screenshots/before-390-question.png`, `screenshots/after-390-question.png` | The same question on the phone |
| `screenshots/after-question.webp` | Short recording: idle sway, pointer tilt, a question with token flight, impulse and passages |

## Result

- Same layout, text and brain effects. The same six chunks light up for the same question (the mock search
  is the prototype's BM25 over the same chunks).
- Intended differences (plan F01-T12, "Decisions"):
  - the answer panel shows the matching passages with title, link and citation line instead of the
    extractive mock answer;
  - the steps panel lines come from the stream events (`retrieve · 6 de 127 trechos · 455 ms`);
  - the rotating placeholder shows the domain's example questions (`examples.yaml`);
  - the GitHub repository list below the hero is static (no request to api.github.com: single origin).
- The idle sway differs between shots only because it depends on the clock.
