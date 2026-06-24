# Quini — Predicciones Mundial 2026

Motor de predicción de fútbol — **Bivariate-Poisson + Elo ensemble** con análisis de valor sobre cuotas de mercado, Kelly Criterion staking, y tracking de Closing Line Value (CLV).

**Live:** [quini-bzs.pages.dev](https://quini-bzs.pages.dev) · **Landing:** [/landing.html](https://quini-bzs.pages.dev/landing.html)

## Qué hace

- **Picks con ventaja** — probabilidades del modelo vs mercado. BET solo cuando edge ≥ umbral por mercado (1X2 12% / O/U 8%, calibrado por backtest).
- **Per-market thresholds** — el modelo es fuerte en O/U (+59% ROI histórico) y débil en 1X2 (-41% con threshold 7%). Cada mercado tiene su propio umbral.
- **Kelly Criterion stakes** — quarter-Kelly capped al 5% por apuesta. Maximiza crecimiento a largo plazo sin sobreapostar.
- **Bankroll simulator** — replay del historial con 3 estrategias (Kelly/Flat/Value), filtro por mercado.
- **CLV tracking** — guarda snapshot de odds por cron tick, calcula closing-line-value al cerrar el partido. Métrica gold-standard de edge real.
- **Quiniela optimizada** — predicciones de marcador que maximizan puntos esperados.
- **Resultados en vivo** — heatmap de probabilidades restantes condicional al score actual.
- **Track record honesto** — todas las apuestas visibles, W/L, ROI, CLV promedio, % que vencen al cierre.

## Stack

| Capa | Tech |
|------|------|
| Modelo principal | Bivariate-Poisson (penaltyblog) sobre 180k+ partidos con time-decay (half-life 180d) |
| Modelo regularizador | Elo K=20-60 por tournament, blend dinámico (más Elo si BP tiene poca data) |
| De-vig | Shin method sobre books sharp (Pinnacle/Betfair priority) |
| Stake sizing | Quarter-Kelly capped 5% |
| Datos | The Odds API (cuotas + scores) + ESPN (live updates) |
| Frontend | HTML estático + Tailwind CDN (SPA vanilla JS) + PWA |
| Deploy | Cloudflare Pages (auto-deploy desde `docs/`) |
| Cron | GitHub Actions `*/5` — fetch odds, run model, snapshot odds, regen OG, commit |
| Tooling | `tools/tune_threshold.py` (backtest tuner) · `tools/generate_og_card.py` (PIL) |

## Arquitectura

```
GitHub Actions (cron */15)
    │
    ▼
api/generate.py ── The Odds API (odds + scores)
    │                    │
    ▼                    ▼
model/ (Dixon-Coles)   data/fit.pkl (MLE pre-fit)
    │
    ▼
docs/data/predictions.json + history.json
    │
    ▼
Cloudflare Pages (static site)
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add ODDS_API_KEY

# Fit the model (~90s)
python -m model.dixon_coles

# Generate predictions
python api/generate.py

# Serve locally
python -m http.server 8000 -d docs
```

## Estado del proyecto

### Fase 0-3 ✅ (Base)
- Dixon-Coles → Bivariate-Poisson MLE con time-decay 180d
- Shin de-vig sobre sharp books
- Cron */5 min, smart skip si no hay partidos en ventana 2h
- SPA con tabs: Picks · Grupos · Resultados · Historial · Quiniela
- PWA (manifest + service worker)
- i18n ES/EN

### Fase 4 ✅ (Model intelligence)
- **Ensemble Elo + BivariatePoisson** con blend dinámico por sample size
- **Per-market thresholds** (1X2 12% / O/U 8% / BTTS 10%) — calibrado por backtest
- **MIN_MODEL_PROB 30%** — bloquea bets a longshots (Scotland 22% vs Brazil ya no)
- **EV_THRESHOLD 4%** — filtro adicional sobre edge
- **Quarter-Kelly stakes** capped 5%

### Fase 5 ✅ (Validation infra)
- **CLV tracking**: snapshot de odds por cron, calcula closing-line-value al cerrar
- **Backtest tuner** (`tools/tune_threshold.py`): ROI por edge bucket + por mercado, recomienda thresholds
- **Bankroll simulator**: replay histórico con 3 estrategias + filtro por mercado

### Fase 6 ✅ (Marketing & growth)
- **Landing page** (`/landing.html`) con stats live + email capture
- **OG card auto-gen** (`tools/generate_og_card.py`) con stats reales
- **Affiliate links** con sort LatAm-first (Caliente · Betcris · Codere)
- **Email capture banner** sticky (Formspree-ready)
- **Modo presentación** para screengrab/video TikTok
- **Share card** mobile-first 1080×1350 con probs + top scores + BETs
- **"Why this pick?" disclosure** — transparencia total

### TODO — Mejoras siguientes
- [ ] Re-fit automático por jornada con resultados nuevos
- [ ] Telegram bot / canal premium con Stripe Payment Link
- [ ] Auto-tweet workflow para BETs de high-edge
- [ ] Tournament bracket viz para KO stages
- [ ] Player stats (StatsBomb WC2022) — radar hexagonal por jugador
- [ ] Mercados secundarios (córners, tarjetas) post-WC

#### Estadísticas de jugadores (feature grande)
- [ ] Radar hexagonal estilo FIFA por jugador: 6 ejes (goles, asistencias, % tiro a puerta, duelos ganados, tarjetas, distancia)
- [ ] Foto + equipo + país por jugador
- [ ] Navegación por país → jugadores de esa selección
- [ ] Top 5 predicciones por equipo (ej: equipos con mayor % tiro a puerta histórico basado en StatsBomb WC2022)
- [ ] Fuente de datos: StatsBomb open data (WC2022 gratis vía `statsbombpy`) + ESPN API para 2026 en curso

#### Scope futuro (post-WC)
- [ ] Ligas de clubes (football-data.co.uk / FBref)
- [ ] Mercados secundarios (córners, tarjetas)
- [ ] Kelly fraccional como toggle
- [ ] Múltiples deportes
- [ ] Bootstrap varianza posterior → confianza v2

## Cómo funciona el edge

```
edge = probabilidad_modelo - probabilidad_mercado
```

Si el modelo dice 60% y el mercado dice 50%, hay +10% de edge. Umbral mínimo: 7% para señal BET.

## License

Predicciones para análisis y entretenimiento. No es asesoría financiera.
