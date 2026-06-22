# Quini — Predicciones Mundial 2026

Motor de predicción de fútbol basado en Dixon-Coles con análisis de valor sobre cuotas de mercado.

**Live:** [quini-bzs.pages.dev](https://quini-bzs.pages.dev)

## Qué hace

- **Picks con ventaja** — compara probabilidades del modelo vs mercado. Si el modelo ve valor que las casas no ven, marca BET.
- **Quiniela optimizada** — predicciones de marcador que maximizan puntos esperados en formato de quiniela (3 pts ganador + 1 pt goles exactos).
- **Resultados en vivo** — actualización automática cada 15 min durante partidos.
- **Track record honesto** — todas las apuestas visibles, W/L, ROI real.

## Stack

| Capa | Tech |
|------|------|
| Modelo | Dixon-Coles MLE, 20k+ partidos internacionales, time-decay |
| De-vig | Shin method para extraer probabilidades reales de cuotas |
| Datos | The Odds API (cuotas + scores en vivo) |
| Frontend | HTML estático + Tailwind CDN (SPA vanilla JS) |
| Deploy | Cloudflare Pages (auto-deploy desde `docs/`) |
| Cron | GitHub Actions `*/15` — fetch odds, run model, commit JSON |

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

### Fase 0: Modelo + Backtest ✅
- [x] Dixon-Coles MLE con time-decay + tournament weighting
- [x] Shin de-vig para probabilidades reales
- [x] Cache de fit (pickle) — no re-fitea en cada predict
- [x] Backtest con hit rate y ROI por mercado
- [x] Validación de resultados

### Fase 1: Generación automática ✅
- [x] `api/generate.py` — script que genera predictions.json
- [x] Smart cron exit — skip si no hay partidos en ventana de 2h
- [x] Preservación de fixtures completados (API solo devuelve 3 días)
- [x] Dedup de fixtures (odds API vs scores API)
- [x] Acumulación de historial de apuestas

### Fase 2: Frontend ✅
- [x] SPA estática con Tailwind (docs/index.html)
- [x] Tabs: Picks, Resultados, Historial, Quiniela
- [x] Tabla de mercados con Modelo/Implícita/Edge/Cuotas
- [x] Badge confianza ALTA/MEDIA/BAJA
- [x] Marcadores más probables con highlight del top
- [x] Dark mode
- [x] i18n español/inglés con detección de navegador
- [x] Nombres de equipos traducidos (50+ selecciones)
- [x] Password gate para Quiniela (SHA-256)
- [x] Tooltips con iconos de ayuda circulados

### Fase 3: Automatización WC2026 ✅
- [x] GitHub Actions cron cada 15 min
- [x] The Odds API para odds + scores
- [x] Cloudflare Pages auto-deploy desde docs/
- [x] Track record: 31 apuestas, 21W-10L, 67.7%, ROI +16.5%

### TODO — Mejoras pendientes

#### Modelo
- [ ] Re-fit con resultados del WC (fit.pkl estático desde Jun 19)
- [ ] Calibración: reliability diagram, Platt/isotónica si sesgado

#### Frontend
- [ ] Heatmap de probabilidad de marcadores (matriz 6x6)
- [ ] ROI acumulado como gráfico de línea
- [ ] Win rate desglosado por mercado (1X2, O/U, BTTS)
- [ ] Drawdown y streak actual
- [ ] PWA (offline, add to homescreen)

#### Backend
- [ ] Re-fit automático por jornada (GitHub Action post-resultados)
- [ ] Alertas pre-partido (Telegram/email)
- [ ] API REST para consumo externo

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
