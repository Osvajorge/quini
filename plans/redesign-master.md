# Kini Redesign Master Plan — WC 2026 Launch

> Plan de ejecución end-to-end para dejar `kini.bet` en shape comercial: rediseño Apple-Sports-claro, endurecimiento de seguridad, hardening del modelo con ideas de repos WC26 de referencia y cierre del roadmap del issue [Osvajorge/quini#1](https://github.com/Osvajorge/quini/issues/1).
>
> **Repo:** [Osvajorge/quini](https://github.com/Osvajorge/quini) · **Local:** `/Users/jorge.guardado/Documents/Projects/football-prediction-tool` · **Design source of truth:** `/Users/jorge.guardado/Downloads/design_handoff_kini_redesign 2/` (App + Landing `.dc.html` + `kini_data.json` + README).

---

## Resumen ejecutivo

**Contexto verificado (Phase 0 discovery):**

- Stack real: **static site** en `docs/` + **Cloudflare Pages Functions** en `functions/` (no hay `package.json`, no hay `wrangler.toml`, no hay build). Tailwind por CDN.
- SPA principal: [docs/app/index.html](docs/app/index.html) (3.915 líneas, todo inline). Landing: [docs/index.html](docs/index.html) (921 líneas).
- Backend model: Python (`api/`, `model/`, `backtest/`, `quiniela/`, `tools/`) — Bivariate-Poisson + Elo + Shin devig + quarter-Kelly + CLV. Ya es sólido; sólo hay que enriquecerlo con lo que trae la referencia.
- Cron: [.github/workflows/update-predictions.yml](.github/workflows/update-predictions.yml) (único workflow).
- Workers: `_middleware` (redirect a `kini.bet`), `/api/subscribe` (waitlist KV + Resend welcome), `/api/send-picks` (broadcast diario protegido por `PICKS_SECRET`).
- Auth/tiers: **no existen todavía**. La maqueta pide Free/Pro/Admin con gating de picks.
- Issues abiertos: **1** ([#1 Roadmap: Lanzamiento comercial](https://github.com/Osvajorge/quini/issues/1)) — checklists de producto/negocio/contenido/telegram para semana actual + post-mundial.
- **"Brackets mal cerrados": no reproducidos.** `node --check` pasa limpio en todos los `.js` funciones + `sw.js`, y ambos HTML tienen los `<script>` inline sin errores de sintaxis. Se deja Phase 1 igualmente porque el usuario los reportó — la primera tarea es **reproducir el fallo antes de tocar nada** (ver §Fase 1).

**Cambios que sí trae este plan:**

1. **Rediseño Apple-Sports-claro (hifi)** con Tailwind, Manrope + JetBrains Mono, tokens del handoff — Landing + App + nuevo Admin.
2. **Detalle de partido** data-driven (matriz 6×6, xG, stats reales, top scores) — la pieza estrella que hoy no existe.
3. **Auth D1 + JWT + 2FA admin** para desbloquear los tiers Free/Pro/Admin del handoff sin migrar el modelo a DB.
4. **Hardening de seguridad** end-to-end: CSP, sanitización, rate-limit multi-endpoint, secrets audit, audit logs de admin.
5. **Modelo v2**: Dixon-Coles correction, bipartite best-third qualifier, RPS + calibración expuestos, MC 50k para bracket real (ideas de [Hicruben/world-cup-2026-prediction-model](https://github.com/Hicruben/world-cup-2026-prediction-model)) y `wc26-mcp` como fuente de H2H + lesiones.
6. **Testing** Playwright + Vitest + pruebas de modelo + pentest checklist.
7. **Roadmap issue #1** cerrado o desglosado en issues descendientes que este plan enlaza uno-a-uno.

**Prioridades:**

| Fase | Bloque | Prioridad | Estimación |
|------|--------|-----------|------------|
| 0 | Auditoría + baseline | Crítico | 0.5 día |
| 1 | Bugfix sintaxis + lint | Alto | 0.5 día |
| 2.1 | Landing rediseño | Alto | 1.5 día |
| 2.2 | App rediseño (SPA) | **Crítico** | 4 días |
| 2.3 | Admin panel | Medio | 2 días |
| 3 | Auth + seguridad | **Crítico** | 3 días |
| 3.5 | Model v2 (hardening + ideas ref repos) | Alto | 2 días |
| 4 | Testing + QA | Alto | 2 días |
| 5 | Deploy + cierre issues | Alto | 0.5 día |

**Total estimado: ~16 días de trabajo focalizado** (ajustar según capacidad; el orden intra-fase está pensado para poder cortar en cualquier punto y quedar en verde).

---

## Fase 0 — Preparación y Auditoría

**Objetivo:** dejar el árbol en estado conocido, con dependencias del build declaradas y un baseline reproducible antes de tocar código.

### 0.1 Baseline del repo
- [ ] Rama de trabajo: `git checkout -b redesign/wc26-launch`
- [ ] Snapshot: `git tag pre-redesign-$(date +%Y%m%d)` (rollback point).
- [ ] Verificar `.env` **no** está trackeado (`git ls-files .env` debe salir vacío). Ya está en [.gitignore](.gitignore), verificar.
- [ ] Congelar el modelo actual: `cp data/fit.pkl data/fit.pkl.backup-$(date +%Y%m%d)`.

### 0.2 Declarar el build que hoy no existe
- [ ] Crear [package.json](package.json) mínimo (para tooling de front, no cambia el runtime estático):
  ```json
  {
    "name": "kini",
    "private": true,
    "type": "module",
    "scripts": {
      "lint": "eslint .",
      "format": "prettier --write .",
      "test:e2e": "playwright test",
      "test:unit": "vitest run",
      "typecheck": "tsc --noEmit -p ."
    }
  }
  ```
- [ ] Crear [wrangler.toml](wrangler.toml) para tener parity local con las Pages Functions:
  ```toml
  name = "kini"
  compatibility_date = "2026-01-01"
  pages_build_output_dir = "docs"
  ```
  (Sólo para `wrangler pages dev`; el deploy real sigue en el dashboard de Cloudflare.)
- [ ] Crear [.eslintrc.cjs](.eslintrc.cjs) + [.prettierrc.json](.prettierrc.json) con reglas estrictas (`no-unused-vars: error`, `curly: all`, `eqeqeq: always`, `no-implicit-globals: error`, `no-eval: error`, `no-implied-eval: error`).
- [ ] Instalar dev deps: `npm i -D eslint prettier @playwright/test vitest typescript`
- [ ] Correr una pasada limpia: `npm run lint` — anotar el count actual de warnings/errors como baseline; **no** arreglar aquí, sólo medir.

### 0.3 Auditoría documentada de GitHub Issues
- [ ] Leer [#1 Roadmap](https://github.com/Osvajorge/quini/issues/1) completo y desglosarlo en sub-issues por bloque:
  - `ui/landing` → email capture widget, affiliate links, landing basica.
  - `ui/app` → modo presentación, affiliate links en BET cards.
  - `content` → templates capcut (fuera de scope técnico; sólo anotar).
  - `telegram` → canal free + Stripe (post-lanzamiento, ver Fase 5).
  - `post-mundial` → radar jugadores, bankroll simulator (ya existe según README), Kelly por señal, ligas de clubes.
- [ ] Crear los sub-issues en GitHub con label `plan-master:redesign` y referenciar `#1` como parent (`Ref: #1`).
- [ ] Correr `gh issue list --state closed --limit 100 --repo Osvajorge/quini` — no hay ninguno todavía; documentar en el PR final los que este plan cierra.

### 0.4 Documentation Discovery — patrones a copiar
Consultar en cada fase, no derivar de memoria:

| Necesidad | Fuente exacta a leer |
|-----------|----------------------|
| Design tokens (colores, radios, sombras, tipografía) | [design_handoff/README.md](/Users/jorge.guardado/Downloads/design_handoff_kini_redesign%202/README.md) §Design Tokens |
| Estado + navegación SPA | [Kini App.dc.html:437-597](/Users/jorge.guardado/Downloads/design_handoff_kini_redesign%202/Kini%20App.dc.html) (clase `Component`, funciones `enrich`, `buildDetail`, `renderVals`) |
| Cálculo matriz 6×6 | Mismo archivo, líneas 506–520 |
| Form data del modelo | [design_handoff/README.md](/Users/jorge.guardado/Downloads/design_handoff_kini_redesign%202/README.md) §Data — forma esperada y fetch |
| API existente CF Pages Functions | [functions/api/subscribe.js](functions/api/subscribe.js), [functions/api/send-picks.js](functions/api/send-picks.js) |
| Model API interna | [api/generate.py](api/generate.py) + [model/predict.py](model/predict.py) + [model/bivariate_poisson.py](model/bivariate_poisson.py) |
| Idea Dixon-Coles + MC 50k + bipartite | [Hicruben/world-cup-2026-prediction-model](https://github.com/Hicruben/world-cup-2026-prediction-model) — leer README + `src/` completo antes de reimplementar |
| Fuente de datos H2H, lesiones, venues, group standings | [jordanlyall/wc26-mcp](https://github.com/jordanlyall/wc26-mcp) (18 tools, ships local, sin API externa) |
| UX bracket + pool + private leagues | [oyvhov/world-cup-pool](https://github.com/oyvhov/world-cup-pool) (SvelteKit + PocketBase — solo ideas de UX, no adoptar stack) |
| Reglas WCAG 2.1 AA | [w3.org/WAI/WCAG21/quickref](https://www.w3.org/WAI/WCAG21/quickref/?versions=2.1&levels=aa) |

**Anti-pattern guards para el resto del plan:**
- ❌ NO copiar `support.js` del bundle de handoff. Es runtime del prototipo, no producción.
- ❌ NO migrar `predictions.json`/`projections.json`/`history.json` a base de datos. Siguen siendo estáticos regenerados por el cron.
- ❌ NO reescribir el modelo desde cero. Ya funciona (Bivariate-Poisson + Elo). Sólo añadir capas.
- ❌ NO introducir React/Next para el frontend. Vanilla JS + Tailwind alcanza; a lo sumo Astro (opcional Fase 2.2 §Astro migration).

### Verificación Fase 0
- [ ] `npm run lint` corre (aunque falle) — infraestructura OK.
- [ ] `wrangler pages dev docs` sirve la landing localmente.
- [ ] `python api/generate.py` corre sin error contra `.env` local.
- [ ] Sub-issues creados en GitHub y linkeados al `#1`.

---

## Fase 1 — Corrección de bugs estructurales

**Objetivo:** cero errores de compilación/parse en JS + Python, y una barrera automatizada para que no vuelvan.

### 1.1 Reproducir los "brackets mal cerrados"
El discovery **no encontró errores de sintaxis** con `node --check` en:
- [docs/app/index.html](docs/app/index.html) (2 script blocks, 0 errores)
- [docs/index.html](docs/index.html) (2 script blocks — el "error" del `JSON-LD` es falso positivo, `new Function()` no parsea JSON-LD)
- [docs/sw.js](docs/sw.js), [functions/_middleware.js](functions/_middleware.js), [functions/api/*.js](functions/api/)

- [ ] Antes de tocar nada: correr en la consola del navegador con Devtools abierto sobre `kini.bet/app` y `kini.bet/`, mirar la pestaña Console — si hay `Uncaught SyntaxError` con ruta+línea, apuntarlo aquí y arreglar puntualmente.
- [ ] Correr `python -m py_compile $(find . -name "*.py" -not -path "./.venv/*")` — reportar cualquier `.py` que falle.
- [ ] Si no aparece ningún error de sintaxis real, cerrar esta sub-tarea documentándolo en el PR ("no bracket bugs reproduced; formatter + lint added as guardrail").

### 1.2 Guardrails permanentes
- [ ] Instalar hook local: `.githooks/pre-commit` con `node --check` sobre todo `.js` staged y `python -m py_compile` sobre todo `.py` staged.
- [ ] Añadir a `.github/workflows/`:
  ```yaml
  # .github/workflows/lint.yml
  name: lint
  on: [pull_request, push]
  jobs:
    js:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-node@v4
          with: { node-version: '20' }
        - run: npm ci
        - run: npm run lint
    py:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5
          with: { python-version: '3.11' }
        - run: pip install ruff
        - run: ruff check .
  ```
- [ ] Añadir `ruff` a [requirements.txt](requirements.txt) (dev) con config en `pyproject.toml`.

### 1.3 Curación puntual conocida
- [ ] [docs/app/index.html:3389](docs/app/index.html) contiene `// Replace with your Formspree endpoint: https://formspree.io/f/XXXXXXXX` — reemplazar por lectura desde `/api/subscribe` (ya existe) o borrar si no se usa.
- [ ] [docs/index.html:99-102](docs/index.html) — `min-height` inline para prevenir CLS: OK, no tocar.
- [ ] Auditar el segundo script block de `docs/app/index.html` en busca de `console.log` que hayan quedado (grep `console.log` y decidir cuáles quedarse).

### Verificación Fase 1
- [ ] `npm run lint` → 0 errores.
- [ ] `ruff check .` → 0 errores.
- [ ] `node --check` sobre cada `.js` extraído del HTML — 0 errores.
- [ ] Push a rama activa lanza el workflow `lint` y pasa verde.

---

## Fase 2 — Rediseño UI/UX (end-to-end)

**Objetivo:** implementar el look Apple-Sports-claro del handoff, pixel-close, en Landing + App + Admin. Todo con Tailwind + tokens declarados; mobile-first; light/dark; WCAG 2.1 AA.

### 2.0 Design system compartido
Fuente única de tokens — [docs/assets/tokens.css](docs/assets/tokens.css) (nuevo). Copiar de `design_handoff/README.md §Design Tokens`:

- [ ] Crear [docs/assets/tokens.css](docs/assets/tokens.css) con `:root` claro + `:root[data-theme="dark"]` (declarar equivalentes oscuros):
  ```css
  :root {
    --bg-app: #f5f5f7;
    --bg-body: #ececed;
    --surface: #ffffff;
    --border-subtle: #e2e2e5;
    --border-divider: #f5f5f7;
    --text-primary: #1d1d1f;
    --text-secondary: #515154;
    --text-muted: #8a8a8e;
    --placeholder: #a8a29a;
    --accent: #12b981;
    --accent-text: #0e9f6e;
    --accent-soft: #e7f7f0;
    --accent-border: #b8ecd5;
    --ui-black: #111;
    --live: #e0245e;
    --info: #2a6fdb;
    --info-soft: #eef4ff;
    --amber-text: #c2530a;
    --amber-soft: #fff4e5;
    --tier-pro: #12b981;
    --tier-admin: #7c5cff;
    --draw-bar: #c9c9ce;
    --radius-card: 16px;
    --radius-large: 20px;
    --radius-chip: 9px;
    --radius-pill: 999px;
    --shadow-card: 0 10px 30px -22px rgba(0,0,0,.25);
    --shadow-elevated: 0 24px 60px -30px rgba(0,0,0,.35);
    --content-app: 760px;
    --content-landing: 1080px;
    --nav-height: 64px;
  }
  ```
- [ ] Extender la config de Tailwind (existe inline en [docs/app/index.html:29-35](docs/app/index.html)) para exponer `theme.extend.colors` mapeados a los tokens (`accent`, `surface`, etc.) — usar `colors: { accent: 'var(--accent)' }` para dejarlo consistente.
- [ ] Cargar `<link rel="stylesheet" href="/assets/tokens.css">` desde ambos HTML.
- [ ] Import Manrope 400/500/600/700/800 + JetBrains Mono 400/500/700 (ya está parcialmente en el app, unificar en el landing).

### 2.1 Landing Page

**Archivo destino:** [docs/index.html](docs/index.html) (reescritura del look, misma URL; la funcionalidad de waitlist debe preservarse).

**Referencia:** [Kini Landing.dc.html](/Users/jorge.guardado/Downloads/design_handoff_kini_redesign%202/Kini%20Landing.dc.html) completo (175 líneas de HTML/CSS inline).

- [ ] Hero split (texto izquierda + hero pick card derecha) — copiar layout de `Kini Landing.dc.html:37-69`.
- [ ] "Cómo funciona" 3 columnas (01/02/03) — copiar de `Kini Landing.dc.html:71-80`.
- [ ] Sección "Rendimiento" con banda oscura + métricas honestas (ROI +59% O/U, edge min 8%, 20.5k partidos) — usar `history.json` real via `fetch()` para actualizar en runtime (no hard-code).
- [ ] Bloque waitlist (email → phone opcional → nombre) — **reutilizar la lógica actual** de [docs/index.html:159-200](docs/index.html) contra `/api/subscribe`. Sólo cambiar los estilos.
- [ ] Footer con links a `/juego-responsable` (nueva página; ver §2.2 view 9).
- [ ] Toggle ES/EN — usar el mecanismo existente en [docs/app/index.html:422-427](docs/app/index.html) (`localStorage 'quini_lang'`), extraer a `docs/assets/i18n.js` compartido.
- [ ] Micro-interacciones: `transition: transform 0.2s ease` en botones, `hover:scale-[1.02]`, focus visible con `outline: 2px solid var(--accent); outline-offset: 2px`.
- [ ] **Sin `<script src="cdn.tailwindcss.com">` en producción** si el sitio va a crecer — dejar CDN para el MVP pero anotar como deuda para Fase 5 (compilar Tailwind a un CSS estático de <30KB via `tailwindcss -o docs/assets/tailwind.css`).
- [ ] Accesibilidad:
  - [ ] Contraste `#8a8a8e` sobre `#ffffff` = 3.5:1 → **falla AA para texto normal**. Restringir su uso a texto ≥14px semibold (contraste AA large text = 3:1) o subir a `#6e6e73` (4.55:1).
  - [ ] `aria-label` en los toggles ES/EN y modo oscuro.
  - [ ] `alt` real en escudos de países (nombre del equipo).
  - [ ] Skip-link "Ir al contenido" al inicio del body.

### 2.2 App Principal — SPA

**Archivo destino:** [docs/app/index.html](docs/app/index.html) — reescritura profunda de la sección `<body>` + del script inline.

**Referencia canónica:** [Kini App.dc.html:437-597](/Users/jorge.guardado/Downloads/design_handoff_kini_redesign%202/Kini%20App.dc.html).

Portar screen-by-screen. **Cada screen es un sub-checkpoint independiente que puede mergearse solo.**

#### 2.2.1 Chrome (nav + shell)
- [ ] Nav sticky con `backdrop-filter:blur(14px)` y `background:rgba(245,245,247,.82)` — ver `Kini App.dc.html:25-34` (viene del Landing pero el App usa el mismo patrón).
- [ ] Logo `kini` + punto verde.
- [ ] Tabs: **Partidos · Picks · Ranking · Bracket · Historial** (ojo: hoy hay "Jugadores" y "Quiniela" — mover Quiniela a Historial como sección, Jugadores a Fase post-lanzamiento).
- [ ] Toggle ES/EN + toggle tema (mantener el existente) + "Entrar" (abre modal auth §2.2.8).
- [ ] Estado activo con `border-bottom:2px solid #111` + `color:#111; font-weight:800`.
- [ ] Scroll-to-top al cambiar de screen (ya existe patrón en [docs/app/index.html](docs/app/index.html), verificar).

#### 2.2.2 Partidos (view por defecto)
- [ ] Segmented control **En vivo / Próximos / Resultados** (pill activa blanca + shadow) — copiar `segStyle` de `Kini App.dc.html:477`.
- [ ] Default: `seg='prox'`.
- [ ] Persistir `seg` en `localStorage` (no en el mockup, pero UX-correct).
- [ ] **Regla clave** para En vivo: filtrar sólo `is_live === true`, nunca completed. Bug documentado en handoff.
- [ ] Cada tarjeta de fixture (Próximos):
  - Col hora fija 52px (`HH:MM` UTC + fecha corta).
  - Col equipos flex:1, min-width:0, 2 filas home/away con escudo 24px + nombre elipsis + prob 1X2 a la derecha.
  - Mini-barra 1X2 apilada (home `#111` / draw `#d6d3cd` / away `#0e9f6e`) + label "X {draw}%".
  - Chip ancho fijo **104px** para evitar shift.
  - Chevron `›`, tarjeta clickable → Detalle.
- [ ] Estado vacío honesto "Ningún partido en vivo ahora" (no fixtures pasados).

#### 2.2.3 Detalle de partido (LA PIEZA ESTRELLA — no existe hoy)
- [ ] Copiar el algoritmo de `buildDetail(f)` de `Kini App.dc.html:502-553` — reimplementar en vanilla JS sin `Component`.
- [ ] Header: escudos 56px + nombres + xG (`f.xg_home`/`f.xg_away`).
- [ ] Marcador central: `VS` si no completed, `ah – aa` si completed. Etiqueta "Final · penales" si `penalty_winner`.
- [ ] Tarjeta pick de valor (verde con `background:#eafaf3; border:1px solid #b8ecd5`) — kicker "PICK DE VALOR", edge `+X%`, `betLabel`, razón generada, `confidence_band`, `best_odds`+`best_book`. Si `best_bet:null` → tarjeta gris "Sin apuesta" con explicación.
- [ ] Barras 1X2 modelo (26px) vs mercado (16px, opacidad .55).
- [ ] **Matriz 6×6** con gradiente `rgba(14,159,110, 0.06 + 0.92*(prob/maxProb))`, texto blanco si alpha>0.5. Ring negro en la celda del resultado real si `completed`.
- [ ] "Marcadores más probables" — top 7 de `top_scores` como barras horizontales.
- [ ] Stats reales (posesión con barra + tiros/SoT/córners/faltas/amarillas) — leer de `match_stats.json` (ya existe [docs/data/match_stats.json](docs/data/match_stats.json)).
- [ ] Botón volver al origen: `backTo === 'picks' ? '← Picks' : '← Partidos'` — guardar el origen al abrir el detalle.

#### 2.2.4 Picks
- [ ] Header + subtítulo.
- [ ] Lista = `upcoming.filter(f => f.best_bet && f.best_bet.pick==='BET')`.
- [ ] Cada tarjeta: equipos+escudos, edge, `betLabel`, `modelo% vs mercado%`.
- [ ] Click → Detalle con `backTo='picks'`.
- [ ] **Gating por tier:** Free ve `slice(0,2)` + bloque bloqueado `filter: blur(6px)` + overlay + CTA "Desbloquear con Pro" → abre modal auth.
- [ ] Pro/Admin ve todo.

#### 2.2.5 Ranking
- [ ] Tabla desde `projections.json`, orden `p_win` desc, filtrar `p_win>0`.
- [ ] Columnas: #, Selección (escudo+nombre), Título (barra + `p_win`%), Semis `p_sf`%, Final `p_f`%.
- [ ] Barra de "título" — `min(100, round(t.p_win * 7))`% (mismo cálculo del mockup).

#### 2.2.6 Bracket
- [ ] Columnas horizontales scrollables: R32 → Cuartos → Semifinal → Final.
- [ ] Partidos completados avanzan al ganador real (fallback: `p_win` mayor).
- [ ] Pendientes muestran prob del modelo.
- [ ] **Ojo:** los emparejamientos exactos de R32 no vienen en los datos hoy — cablearlos con el bracket oficial FIFA (el commit [23c7e65](https://github.com/Osvajorge/quini/commit/23c7e65) ya corrigió el orden, verificar que sigue vigente).

#### 2.2.7 Historial / Rendimiento
- [ ] Banda oscura con métricas honestas de backtest desde `history.json` (ROI, edge min, matches count, quarter-Kelly).
- [ ] Nota transparencia sobre CLV.
- [ ] Apuestas activas (pendientes) desde BET signals.
- [ ] **Regla de producto:** pérdidas en gris neutro, no rojo alarmante.

#### 2.2.8 Login (modal 3 tiers)
- [ ] Overlay oscuro `rgba(0,0,0,.5)` + tarjeta blanca centrada.
- [ ] Copy exacto del handoff (README §8):
  - **Free** — $0, 1–2 picks/día, marcadores live, historial público.
  - **Pro** — $99/mes (lanzamiento; antes $199) o $990/año (~$82/mes, 2 meses gratis); todos los picks + edge/CLV/why + alertas + bracket.
  - **Admin** — interno.
- [ ] La lógica de auth real va en Fase 3. Aquí sólo el UI + un hook `chooseTier(t)` que setee `localStorage.setItem('kini_tier', t)` mock para poder validar el gating antes de tener backend.

#### 2.2.9 Juega con responsabilidad
- [ ] Nueva página estática [docs/juego-responsable.html](docs/juego-responsable.html) con:
  - 18+.
  - "El pasado no predice el futuro."
  - "Apuesta solo lo que puedas perder."
  - Líneas de ayuda: MX CIJ (`800 911 2000`), ES FEJAR (`900 200 225`), Jugadores Anónimos.
- [ ] Link desde footer de landing + app.

#### 2.2.10 State management (reescribir el script inline)
- [ ] Reemplazar el bloque `<script>` grande de [docs/app/index.html](docs/app/index.html) por una máquina de estados mínima:
  ```js
  const state = {
    screen: 'partidos',
    seg: 'prox',
    selectedId: null,
    backTo: 'partidos',
    tier: localStorage.getItem('kini_tier') || null,
    authed: !!localStorage.getItem('kini_session'),
    data: null,
    showAuth: false,
  };
  function setState(patch) { Object.assign(state, patch); render(); if (patch.screen) window.scrollTo(0, 0); }
  ```
- [ ] `render()` recorre el DOM por sección y muestra/oculta con `hidden` en vez de reemplazar innerHTML — evita XSS por concatenación.
- [ ] **Toda inserción dinámica de texto de la API pasa por `textContent`**, nunca `innerHTML`. La única excepción son plantillas hardcoded en el propio JS (sin datos externos).
- [ ] Si algún gráfico requiere HTML (matriz 6×6), construirlo con `document.createElement` + `appendChild`, no template strings.

#### 2.2.11 Modo presentación + affiliate links (issue #1)
- [ ] Botón "Modo presentación" en nav (ya existe [docs/app/index.html:99](docs/app/index.html)) — verificar que abre fullscreen + oculta chrome.
- [ ] Affiliate links en cada tarjeta BET (Caliente / bet365 / Betcris) — usar la función `affiliateUrl` que ya existe en [docs/app/index.html:390](docs/app/index.html) y verificar priorización LatAm (README dice "Caliente · Betcris · Codere").
- [ ] `rel="nofollow sponsored noopener noreferrer"` **obligatorio** en los outbound.

#### 2.2.12 Astro migration (OPCIONAL, sólo si el HTML inline se vuelve inmanejable)
- [ ] Estimación: 3 días extra.
- [ ] Beneficio: componentes reutilizables, mejor DX, tree-shaking de Tailwind sin CDN.
- [ ] Decisión: **posponer**. Con la refactorización planteada arriba, el `docs/app/index.html` debe quedar en ~2500 líneas legibles.

### 2.3 Panel de administración

**Ruta destino:** `docs/admin/index.html` + Functions bajo `functions/admin/*`.

**Alcance:** métricas del sitio (waitlist count, envíos, tasa de apertura si Resend lo expone), métricas del modelo (ROI live, edge medio, CLV), gestión (forzar refit, resend picks, kill switch).

- [ ] [docs/admin/index.html](docs/admin/index.html) con mismo shell + tokens.
- [ ] Dashboard:
  - Card KPI: subscribers count, active picks, ROI 7d, ROI histórico, CLV medio, %WinRate.
  - Tabla últimas apuestas (top 20) con edge / stake / result / CLV.
  - Sección "Modelo" con último `generated_at`, samples entrenados, `EDGE_THRESHOLD`, próxima ejecución del cron.
- [ ] Acciones (protegidas 2FA — ver Fase 3):
  - `POST /admin/actions/resend-picks` → llama a `/api/send-picks` con header `X-Force-Send`.
  - `POST /admin/actions/refit` → dispara un `workflow_dispatch` del cron (via `gh` API).
  - `POST /admin/actions/kill-picks` → escribe `flags:picks_disabled=1` en KV; el frontend lo lee al arranque y oculta las tarjetas.
- [ ] Toda acción crítica genera fila en `audit_log` (D1, ver Fase 3).

### Verificación Fase 2
- [ ] Landing: Lighthouse desktop mobile ≥95 en Performance, ≥95 A11y, ≥95 Best Practices.
- [ ] App: mismo umbral.
- [ ] Playwright test: navegación Partidos → Detalle → Volver funciona.
- [ ] Playwright test: tab Picks muestra 2 cards + overlay bloqueado en tier Free (mock).
- [ ] Contraste manual (axe-devtools) — 0 violaciones.
- [ ] Matriz 6×6 renderiza con la celda del resultado real ringeada cuando `completed`.
- [ ] Modo dark: todos los tokens tienen equivalente, sin `color:#fff` hardcodeado.

---

## Fase 3 — Seguridad end-to-end

**Objetivo:** cero secrets en cliente, XSS blocked por contrato, CSRF cubierto, rate-limit en todos los POST, JWT rotables, 2FA admin, audit trail. Adherir OWASP Top 10 2021.

### 3.1 Frontend
- [ ] **Content Security Policy** — añadir en `_middleware.js`:
  ```js
  const csp = [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https://a.espncdn.com",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join('; ');
  ```
  aplicar sólo a HTML responses. **Meta a corto plazo:** eliminar `'unsafe-inline'` compilando Tailwind y moviendo el script inline a `docs/assets/app.js`.
- [ ] Headers de seguridad adicionales en el mismo middleware:
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()`
- [ ] Sanitización: **prohibido `innerHTML` con datos externos**. Añadir regla ESLint `no-restricted-syntax`:
  ```js
  { selector: "AssignmentExpression[left.property.name='innerHTML'][right.type!=='Literal']", message: "innerHTML with dynamic data disallowed; use textContent or DOMPurify." }
  ```
- [ ] CSRF: para todos los `POST /api/*` que hoy leen `body.email` sin token — añadir doble-submit cookie:
  - Servidor setea `csrf_token=<random>` HttpOnly=false, SameSite=Strict.
  - Frontend lee la cookie y la reenvía en `X-CSRF-Token`.
  - Server valida match.
  - Excepción: `/api/subscribe` es idempotente + honeypot; mantener sin token pero rate-limitear por IP + email (ya lo hace).
- [ ] Validación de forms en cliente **antes** del submit: usar HTMLConstraint (`required`, `pattern`, `maxlength`) + un chequeo JS con `URL()` / `RegExp` explícito. Nunca confiar en el submit.

### 3.2 Backend / Workers
- [ ] Schema validation con [zod](https://zod.dev/) (ligero, tree-shakable) en cada endpoint. Ejemplo `/api/subscribe`:
  ```js
  import { z } from 'zod';
  const Body = z.object({
    email: z.string().email().max(254),
    phone: z.string().max(20).optional(),
    name: z.string().max(60).optional(),
    lang: z.enum(['es', 'en']).default('es'),
    website: z.string().max(0).optional(), // honeypot must be empty
  });
  ```
- [ ] Rate limiting global via Cloudflare Turnstile + KV counters:
  - `/api/subscribe`: 1/5min por IP (ya existe) + 5/día por IP.
  - `/api/send-picks`: bearer secret + firewall a nivel Cloudflare que bloquee todo lo que no venga de GitHub Actions (whitelist IP ranges).
  - `/admin/*`: 20/min por sesión admin.
- [ ] Auth con JWT:
  - Endpoint `POST /api/auth/login` → email + password (bcrypt en D1) → devuelve access token (15min) + refresh token (7d, rotable).
  - Endpoint `POST /api/auth/refresh` → rota refresh, emite nuevo access.
  - Store: **Cloudflare D1** (SQLite) con esquema:
    ```sql
    CREATE TABLE users (
      id INTEGER PRIMARY KEY,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      tier TEXT NOT NULL CHECK(tier IN ('free','pro','admin')) DEFAULT 'free',
      totp_secret TEXT,
      created_at INTEGER NOT NULL,
      last_login_at INTEGER
    );
    CREATE TABLE refresh_tokens (
      jti TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL,
      expires_at INTEGER NOT NULL,
      revoked INTEGER NOT NULL DEFAULT 0,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts INTEGER NOT NULL,
      user_id INTEGER,
      ip TEXT,
      action TEXT NOT NULL,
      details_json TEXT
    );
    ```
  - JWT firmado con HS256, secret en `env.JWT_SECRET`, `iss=kini.bet`, `aud=kini-app`.
  - **Nunca** meter `password_hash` ni `totp_secret` en el token.
- [ ] Secrets audit:
  - [ ] Grep `.env.example` — sólo variables no-secretas. ✓
  - [ ] Grep del código por strings que empiecen con `re_`, `pk_`, `sk_`, `bkp_`, tokens de longitud >20 hardcodeados — reportar 0.
  - [ ] Validar en Cloudflare Dashboard: `RESEND_API_KEY`, `PICKS_SECRET`, `JWT_SECRET` (nuevo), `TOTP_ISSUER=kini.bet` — todos como Environment Variables encrypted.
- [ ] TLS: verificar en Cloudflare Dashboard → SSL/TLS → Overview: modo **Full (strict)**; Minimum TLS Version = **1.3**; Automatic HTTPS Rewrites = **On**.
- [ ] En reposo: passwords con `bcrypt.hash(pw, 12)`. TOTP secrets cifrados con AES-GCM usando `env.KMS_KEY` antes de meterlos en D1.

### 3.3 Admin panel — 2FA
- [ ] Endpoint `POST /api/auth/2fa/enroll` → genera TOTP secret, devuelve `otpauth://` URI para QR.
- [ ] Endpoint `POST /api/auth/2fa/verify` → valida código de 6 dígitos con ventana ±1.
- [ ] Todos los admin actions requieren cabecera `X-2FA-Code` válida (verificada contra el TOTP del user).
- [ ] Audit log automático via helper `logAdminAction(env, ctx, action, details)`:
  ```js
  await env.DB.prepare(
    'INSERT INTO audit_log (ts, user_id, ip, action, details_json) VALUES (?, ?, ?, ?, ?)'
  ).bind(Date.now(), ctx.user.id, ctx.ip, action, JSON.stringify(details)).run();
  ```
- [ ] UI del admin muestra las últimas 100 filas del audit log, con filtro por acción y user.

### 3.4 Anti-pattern guards
- ❌ Nada de `eval`, `new Function()` con string externo, `setTimeout("string")`.
- ❌ No usar `dangerouslySetInnerHTML` (no aplica — no React — pero el equivalente `innerHTML =` con datos remotos también está prohibido por ESLint rule §3.1).
- ❌ No exponer `.env` en el bundle (revisar en el `wrangler pages dev` que `document.body.innerHTML` no contiene `re_`).

### Verificación Fase 3
- [ ] `curl -I https://kini.bet/` devuelve CSP + HSTS + los otros headers.
- [ ] Playwright test: intento de XSS enviando `<script>alert(1)</script>` como `name` en waitlist → el nombre se guarda como texto plano, no ejecuta.
- [ ] Playwright test: intento de CSRF desde origin distinto → 403.
- [ ] Rate limit test: 10 POST rápidos a `/api/subscribe` → los últimos devuelven 429 o `ok:true` idempotente sin escritura.
- [ ] Login con JWT: sesión de 15min expira; refresh renueva; refresh revocado no funciona.
- [ ] 2FA: acción admin sin `X-2FA-Code` → 403.
- [ ] Audit log escribe fila por cada admin action.
- [ ] Escaneo con [gitleaks](https://github.com/gitleaks/gitleaks) → 0 secrets.
- [ ] `npm audit --audit-level=high` → 0 high vulns.

---

## Fase 3.5 — Model hardening (ideas de repos referencia)

**Objetivo:** aprovechar lo mejor de los repos referencia sin reescribir. Aditivo al modelo actual.

### Prioridades del [Hicruben model](https://github.com/Hicruben/world-cup-2026-prediction-model)
- [ ] **Dixon-Coles correction** para partidos low-scoring: hoy [model/dixon_coles.py](model/dixon_coles.py) existe pero [api/generate.py:15](api/generate.py) importa desde `bivariate_poisson`. Verificar cuál está activo en pipeline; si es el bivariate, añadir el término de corrección τ(x,y) para (0,0), (0,1), (1,0), (1,1).
- [ ] **Monte Carlo 50k trials** para el bracket:
  - Nuevo módulo [quiniela/mc_tournament.py](quiniela/mc_tournament.py) con `simulate(fixtures, n_trials=50_000)` que:
    1. Corre los partidos pendientes muestreando de la Bivariate-Poisson.
    2. Aplica el sistema de best-third (3ros de mejor puntaje entre 12 grupos).
    3. Avanza el bracket 32→16→8→4→2→1.
    4. Cuenta % de veces que cada equipo llega a cada ronda.
  - Persistir en `docs/data/projections.json` con `p_r32, p_qf, p_sf, p_f, p_win` — schema ya definido y consumido por el UI.
- [ ] **Bipartite matching best-third**: los 8 mejores 3ros clasifican a R32 con emparejamientos fijos por grupo. Buscar la tabla oficial FIFA para 2026 y hardcodearla como matriz. Alternativa: adoptar el `assignBestThirds()` de Hicruben (MIT, atribuir).
- [ ] **Ranked Probability Score (RPS)** como métrica pública:
  - Añadir `backtest/rps.py` con `def rps(probs, actual): ...` (Epstein 1969).
  - Publicar RPS histórico junto al ROI en la Landing + Admin.
  - Meta interna: `RPS < 0.20` (baseline 0.241 según Hicruben).
- [ ] **Expected Calibration Error (ECE)** — bining por decil de prob, comparar con freq observada. Publicar la reliability diagram en Admin.

### Datos de [wc26-mcp](https://github.com/jordanlyall/wc26-mcp)
- [ ] Evaluar clonar el repo local y usar sus JSONs de venues + H2H como features al modelo:
  - `home_advantage`: si el partido es en México/USA/Canadá vs otro, boost del team local.
  - `travel_distance_km`: puede degradar performance del visitante que viene de <3 días de descanso + >8000km.
  - `injury_index`: suma de importancia (goles + apariciones) de titulares lesionados por selección.
- [ ] Añadir features al fit del BP como coeficientes lineales sobre `attack_i` y `defense_i`.

### Verificación Fase 3.5
- [ ] `python -m backtest.rps` sobre `history.json` imprime RPS actual.
- [ ] `python -m quiniela.mc_tournament` corre 50k y actualiza `projections.json` con timing < 30s.
- [ ] `python -m backtest.calibration` genera un CSV con 10 bins, mostrado en Admin.
- [ ] Test: en fixtures con 0-0 histórico, la prob del modelo debe ser ≥5% (corrección DC aplicada). Antes: probablemente <3%.

---

## Fase 4 — Testing y QA

**Objetivo:** verificar el rediseño se comporta, el modelo no regresó, y hay pruebas de seguridad básicas antes del deploy.

### 4.1 Unit tests (Python)
- [ ] `tests/model/test_predict.py` — snapshot de outputs para 5 fixtures conocidos (fijar seed). Debe pasar tras Fase 3.5.
- [ ] `tests/model/test_devig.py` — comprobar que Shin devig sobre bookies conocidos suma ~1.
- [ ] `tests/model/test_mc_tournament.py` — 1000 sims, comprobar que `sum(p_win)` sobre todos los equipos = 1.0 ± 0.001.
- [ ] Correr con `pytest -q`. Añadir a workflow `lint.yml`.

### 4.2 Integration tests (Workers)
- [ ] Vitest con [`@cloudflare/vitest-pool-workers`](https://developers.cloudflare.com/workers/testing/vitest-integration/):
  - `tests/functions/subscribe.test.js`: valid, invalid email, honeypot, rate-limit, dup email idempotency.
  - `tests/functions/send-picks.test.js`: no auth → 401, wrong secret → 401, correct secret + no leads → skip, correct + leads → sends.
  - `tests/functions/auth.test.js` (Fase 3): register, login, refresh, revoke, 2FA verify/wrong code.

### 4.3 E2E tests (Playwright)
- [ ] `tests/e2e/landing.spec.ts`: hero visible, waitlist submit exitoso, stats live populan.
- [ ] `tests/e2e/app-partidos.spec.ts`: cargar `/app`, verificar tarjeta clickable → detalle → matriz visible → volver.
- [ ] `tests/e2e/app-picks-gating.spec.ts`: modo Free → 2 cards + overlay; login mock Pro → todas visibles.
- [ ] `tests/e2e/dark-mode.spec.ts`: toggle → data-theme=dark → tokens cambian.
- [ ] `tests/e2e/i18n.spec.ts`: toggle ES→EN cambia copy de al menos 10 strings.
- [ ] `tests/e2e/a11y.spec.ts`: [`@axe-core/playwright`](https://www.npmjs.com/package/@axe-core/playwright) sobre landing + app + admin → 0 críticos/serios.
- [ ] Correr en CI + subir screenshots de fallo como artefacto.

### 4.4 Penetration testing (checklist manual)
- [ ] XSS payload en cada input: `"><script>alert(1)</script>` — no ejecuta.
- [ ] SQL injection en login: `' OR 1=1 --` — D1 parametrizado, no vulnerable, verificar.
- [ ] JWT tamper: modificar payload y firmar con secret aleatorio → server 401.
- [ ] JWT sin firma (algo `none`): server 401.
- [ ] CSRF: form desde `evil.com` que hace POST a `/api/auth/login` → 403 por SameSite=Strict + CSRF token.
- [ ] Rate limit bypass: rotar `CF-Connecting-IP` (spoof) → sigue bloqueado por Cloudflare (usa la IP real del edge, no la header).
- [ ] Clickjacking: `X-Frame-Options: DENY` o `frame-ancestors 'none'` en CSP → verificado con iframe embed.
- [ ] Path traversal: `GET /api/../.env` → 404.
- [ ] SSRF: `/api/subscribe` con `phone: "http://169.254.169.254/latest/meta-data"` → validación zod rechaza.
- [ ] Correr [OWASP ZAP baseline scan](https://www.zaproxy.org/docs/docker/baseline-scan/) contra `staging.kini.bet` → 0 High.

### 4.5 Performance
- [ ] Lighthouse CI en el mismo workflow lint → si Perf<90 o A11y<95, falla.
- [ ] Bundle size: `docs/assets/*.js` + inline scripts total < 100KB gzipped.
- [ ] `docs/data/predictions.json` gzip ≤ 200KB.

### Verificación Fase 4
- [ ] `pytest`, `vitest`, `playwright test` → todos verdes en CI.
- [ ] Reporte de pentest manual documentado en `docs/security/pentest-<fecha>.md`.
- [ ] Lighthouse ≥95 en 4 categorías.

---

## Fase 5 — Deploy y cierre

### 5.1 Staging
- [ ] Branch `redesign/wc26-launch` → Cloudflare Pages preview deployment automático (ya está el link `<sha>.kini-ar3.pages.dev`).
- [ ] Compartir URL de staging para QA humana (Pedro / Dani si aplica al proyecto — este es proyecto personal, así que sólo test-driver personal).

### 5.2 Producción
- [ ] Merge a `main` → deploy automático a `kini.bet` vía Pages.
- [ ] Post-deploy smoke test:
  - [ ] `curl -s -o /dev/null -w "%{http_code}" https://kini.bet/` → 200.
  - [ ] `curl -s -I https://kini.bet/` incluye CSP + HSTS.
  - [ ] `curl -s -X POST https://kini.bet/api/subscribe -H "Content-Type: application/json" -d '{"email":"noop+deploytest@kini.bet"}'` → `{"ok":true}` (sin welcome email hacia esa dir; borrar el lead manualmente después).
  - [ ] Abrir `https://kini.bet/app` en Safari mobile + Chrome desktop, verificar Partidos/Picks/Bracket cargan.

### 5.3 Cierre issues
- [ ] Para cada checkbox del `#1` que este plan cubre:
  - Cerrar el sub-issue con "Fixed in PR #<n>".
  - Cerrar el `#1` cuando **todos** sus checkboxes marcados en el desglose (§0.3) estén en un PR merged.
- [ ] Sub-issues que quedan open (fuera de scope de este plan):
  - Content marketing (CapCut, TikTok/IG): responsabilidad del equipo de contenido.
  - Telegram premium con Stripe Payment Link: post-lanzamiento.
  - Post-Mundial: ligas de clubes, radar jugadores, mercados secundarios.

### 5.4 Comunicación post-deploy
- [ ] Nota en el README con badges: build, tests, lighthouse.
- [ ] Update de [docs/security/pentest-<fecha>.md](docs/security/) con hallazgos y fixes.
- [ ] Enviar broadcast waitlist con `/api/send-picks` diciendo "estamos en el aire" — copy antes de enviar (revisar con equipo si aplica).

### Verificación Fase 5
- [ ] `kini.bet` responde 200 y muestra la landing nueva.
- [ ] `kini.bet/app` muestra la SPA nueva con el detalle de partido funcionando sobre datos reales.
- [ ] `kini.bet/admin` (protegido) muestra las métricas y las acciones funcionan con 2FA.
- [ ] Issue `#1` cerrado o desglosado con al menos 5 sub-issues cerrados.

---

## Matriz de trazabilidad — Issue #1 ↔ Fase

| Item del issue #1 | Fase | Nota |
|-------------------|------|------|
| Email capture widget en app | 2.2.11 + reuse subscribe API | Ya existe en landing; portar al app SPA |
| Modo presentación (fullscreen) | 2.2.11 | Verificar existente en `docs/app/index.html:99` |
| Affiliate links en cada BET card | 2.2.11 | Función `affiliateUrl` ya existe, cablear con `rel="nofollow sponsored"` |
| Landing page básica (rama `landing`) | 2.1 | La rama actual `main` ya tiene landing; el rediseño es Fase 2.1 |
| Nombre definitivo (LaJugada/Pronos/Remate) | fuera de scope técnico | Ya definido: `kini` |
| Registrar dominio | ✓ hecho | `kini.bet` activo |
| Mailchimp | ✗ no aplica | Se usa Resend + KV waitlist propio (más control, menos vendor lock) |
| Afiliados Caliente/bet365/Betcris | fuera de scope técnico | Depende de acceso a esos programas |
| TikTok/Instagram | fuera de scope técnico | Contenido |
| Templates CapCut | fuera de scope técnico | Contenido |
| Community manager | fuera de scope técnico | Contratación |
| Telegram canal gratuito + premium con Stripe | Fase 5.4 (nota) | Post-lanzamiento inmediato |
| Bankroll simulator | ya existe | README §Fase 5 ✓ |
| Kelly Criterion por señal | 3.5 (posible) | Ya es quarter-Kelly; "por señal" requiere refactor menor de `model/predict.py` |
| Modelo ligas de clubes | fuera de scope | Post-Mundial |
| Dashboard usuarios (Supabase) | 3.2 | Se hace con **D1**, no Supabase (ya está el stack Cloudflare) |

---

## Anti-patterns a evitar (consolidado)

1. ❌ Copiar `support.js` del handoff — es runtime del prototipo.
2. ❌ Migrar predictions a base de datos — mantener JSON estático.
3. ❌ Reescribir el modelo desde cero — extender el existente.
4. ❌ Meter React/Next — vanilla JS + Tailwind alcanza.
5. ❌ `innerHTML` con datos externos — ESLint bloquea.
6. ❌ Secrets en el bundle — verificado en Fase 3.2.
7. ❌ Skips en el linter (`// eslint-disable`) sin justificación en comentario.
8. ❌ Force-push a `main`.
9. ❌ Deploy sin haber pasado Fase 4 verde.
10. ❌ Cerrar el issue #1 sin haber ejecutado la matriz de trazabilidad.

---

## Handoff instructions para ejecutar por fase

Cada fase es autocontenida. Para retomar en una sesión nueva:

```
# Sesión Fase X
Contexto:
- Repo: /Users/jorge.guardado/Documents/Projects/football-prediction-tool
- Design source: /Users/jorge.guardado/Downloads/design_handoff_kini_redesign 2/
- Plan master: plans/redesign-master.md
- Fase actual: X.Y (leer §Fase X del plan)

Ejecuta los checkboxes de la fase en orden. No abras nueva fase hasta que la verificación de la actual pase verde. Reporta al final con:
- Checkboxes marcados
- Comandos ejecutados
- Verificación (con evidencia)
- Sub-issues cerrados en GitHub
```

---

## Notas finales

- La estimación de 16 días asume una sola persona trabajando dedicada. Se puede paralelizar Landing (2.1) + Model v2 (3.5) sin conflicto (archivos disjuntos).
- Auth D1 (Fase 3) puede posponerse a **post-mundial** si el gating Pro/Admin no es urgente para el MVP comercial — en ese caso Fase 2.2.8 usa sólo `localStorage` mock y el revenue Pro se cobra manualmente vía Stripe Payment Link.
- El modelo v2 (Fase 3.5) es aditivo — se puede shippear con el modelo actual y activar RPS/MC/DC-correction en un release menor.
- **Si algo se rompe en producción:** `git revert` sobre `main` reverso el último merge; el tag `pre-redesign-<fecha>` es el rollback total.
