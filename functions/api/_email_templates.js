/** Shared HTML email templates + Resend sender for Kini */

export async function sendEmail(apiKey, { to, from, reply_to, subject, html, text, headers }) {
  const res = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ to, from, reply_to, subject, html, text, headers }),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Resend ${res.status}: ${err}`);
  }
  return res.json();
}

export function welcomeEmail({ name, lang = 'es' }) {
  const es = lang !== 'en';
  const displayName = name ? ` ${name}` : '';

  const subject = es
    ? '✅ Estás en la lista — Kini · Mundial 2026'
    : '✅ You\'re on the list — Kini · World Cup 2026';

  const html = `<!DOCTYPE html>
<html lang="${lang}">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#050a14;font-family:'Inter',Arial,sans-serif;color:#f1f5f9;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#050a14;padding:32px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">

        <!-- Header -->
        <tr><td style="padding-bottom:24px;">
          <table cellpadding="0" cellspacing="0">
            <tr>
              <td style="width:36px;height:36px;background:linear-gradient(135deg,#10b981,#0891b2);border-radius:10px;text-align:center;vertical-align:middle;">
                <span style="color:#fff;font-weight:700;font-size:18px;">K</span>
              </td>
              <td style="padding-left:10px;font-size:18px;font-weight:700;color:#f1f5f9;">Kini</td>
            </tr>
          </table>
        </td></tr>

        <!-- Hero -->
        <tr><td style="background:#0e1929;border:1px solid #1f2a3d;border-radius:16px;padding:32px;">
          <p style="margin:0 0 8px;font-size:28px;">✅</p>
          <h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#f1f5f9;">
            ${es ? `¡Reservado${displayName}!` : `You're in${displayName}!`}
          </h1>
          <p style="margin:0 0 24px;font-size:15px;color:#64748b;line-height:1.6;">
            ${es
              ? 'Tu lugar en la lista de acceso anticipado de <strong style="color:#f1f5f9;">Kini</strong> está confirmado. Te avisaremos cuando abramos — sin spam, prometido.'
              : 'Your spot on the <strong style="color:#f1f5f9;">Kini</strong> early access list is confirmed. We\'ll let you know when we open — no spam, promised.'}
          </p>

          <!-- Stats teaser -->
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
            <tr>
              <td width="50%" style="padding-right:6px;">
                <div style="background:#1a2638;border:1px solid #1f2a3d;border-radius:10px;padding:16px;text-align:center;">
                  <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#64748b;margin-bottom:4px;">${es ? 'Acierto' : 'Win rate'}</div>
                  <div style="font-size:22px;font-weight:700;color:#34d399;">55.3%</div>
                </div>
              </td>
              <td width="50%" style="padding-left:6px;">
                <div style="background:#1a2638;border:1px solid #1f2a3d;border-radius:10px;padding:16px;text-align:center;">
                  <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#64748b;margin-bottom:4px;">CLV</div>
                  <div style="font-size:22px;font-weight:700;color:#34d399;">+21.2%</div>
                </div>
              </td>
            </tr>
          </table>

          <!-- CTA -->
          <div style="text-align:center;">
            <a href="https://kini.bet/app" style="display:inline-block;background:linear-gradient(135deg,#10b981,#0891b2);color:#fff;text-decoration:none;font-weight:700;font-size:15px;padding:14px 32px;border-radius:12px;">
              ${es ? 'Ver picks de hoy →' : 'See today\'s picks →'}
            </a>
          </div>
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:20px 0;text-align:center;">
          <p style="margin:0;font-size:12px;color:#64748b;">
            ${es ? 'Recibirás picks cuando abramos acceso.' : 'You\'ll get picks when we open access.'}
            <br>
            <a href="https://kini.bet" style="color:#10b981;text-decoration:none;">kini.bet</a>
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>`;

  const text = es
    ? `¡Reservado${displayName}! Tu lugar en Kini está confirmado.\n\nAcierto: 55.3% · CLV: +21.2%\n\nVer picks: https://kini.bet/app\n\nkini.bet`
    : `You're in${displayName}! Your Kini spot is confirmed.\n\nWin rate: 55.3% · CLV: +21.2%\n\nSee picks: https://kini.bet/app\n\nkini.bet`;

  return { subject, html, text };
}


export function picksEmail({ picks, date, lang = 'es' }) {
  const es = lang !== 'en';
  const dateStr = new Date(date).toLocaleDateString(es ? 'es-ES' : 'en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
  });

  const subject = picks.length
    ? `🏆 Kini · ${picks.length} pick${picks.length > 1 ? 's' : ''} ${es ? 'de hoy' : 'today'} — ${dateStr}`
    : `Kini · ${es ? 'Sin picks hoy' : 'No picks today'} — ${dateStr}`;

  const confColor = { ALTA: '#34d399', MEDIA: '#f59e0b', BAJA: '#94a3b8' };

  const pickRows = picks.map(p => {
    const cc = confColor[p.confidence_band] || '#94a3b8';
    return `
      <tr>
        <td style="padding:12px 0;border-bottom:1px solid #1f2a3d;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
            <div style="flex:1;">
              <div style="font-size:13px;color:#64748b;margin-bottom:4px;">${p.match}</div>
              <div style="font-size:15px;font-weight:600;color:#f1f5f9;">${p.desc}</div>
            </div>
            <div style="text-align:right;white-space:nowrap;">
              <span style="display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:6px;background:${cc}22;color:${cc};margin-bottom:4px;">${p.confidence_band}</span><br>
              <span style="font-size:14px;font-weight:700;color:#10b981;">+${p.edge}% edge</span>
              <span style="font-size:13px;color:#64748b;margin-left:8px;">@ ${Number(p.odds).toFixed(2)}</span>
            </div>
          </div>
          ${p.kelly_pct ? `<div style="margin-top:4px;font-size:12px;color:#64748b;">${es ? 'Stake sugerido' : 'Suggested stake'}: <strong style="color:#f1f5f9;">${p.kelly_pct}% banca</strong></div>` : ''}
        </td>
      </tr>`;
  }).join('');

  const noPicksMsg = es
    ? '<p style="color:#64748b;font-size:15px;">Hoy no hay apuestas con edge suficiente. El modelo prefiere no apostar que forzar señales débiles.</p>'
    : '<p style="color:#64748b;font-size:15px;">No bets with enough edge today. The model prefers passing over forcing weak signals.</p>';

  const html = `<!DOCTYPE html>
<html lang="${lang}">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#050a14;font-family:'Inter',Arial,sans-serif;color:#f1f5f9;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#050a14;padding:32px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">

        <!-- Header -->
        <tr><td style="padding-bottom:20px;">
          <table cellpadding="0" cellspacing="0" width="100%"><tr>
            <td>
              <table cellpadding="0" cellspacing="0"><tr>
                <td style="width:32px;height:32px;background:linear-gradient(135deg,#10b981,#0891b2);border-radius:8px;text-align:center;vertical-align:middle;">
                  <span style="color:#fff;font-weight:700;font-size:16px;">K</span>
                </td>
                <td style="padding-left:8px;font-size:16px;font-weight:700;color:#f1f5f9;">Kini</td>
              </tr></table>
            </td>
            <td style="text-align:right;font-size:12px;color:#64748b;">${dateStr}</td>
          </tr></table>
        </td></tr>

        <!-- Body -->
        <tr><td style="background:#0e1929;border:1px solid #1f2a3d;border-radius:16px;padding:24px;">
          <h2 style="margin:0 0 4px;font-size:18px;font-weight:700;">
            ${picks.length ? (es ? `${picks.length} pick${picks.length > 1 ? 's' : ''} de hoy` : `${picks.length} pick${picks.length > 1 ? 's' : ''} today`) : (es ? 'Sin picks hoy' : 'No picks today')}
          </h2>
          <p style="margin:0 0 20px;font-size:13px;color:#64748b;">
            ${es ? 'Modelo Dixon-Coles + Elo · Solo señales con edge real' : 'Dixon-Coles + Elo model · Only genuine edge signals'}
          </p>

          ${picks.length ? `<table width="100%" cellpadding="0" cellspacing="0">${pickRows}</table>` : noPicksMsg}

          <div style="margin-top:20px;text-align:center;">
            <a href="https://kini.bet/app" style="display:inline-block;background:linear-gradient(135deg,#10b981,#0891b2);color:#fff;text-decoration:none;font-weight:700;font-size:14px;padding:12px 28px;border-radius:10px;">
              ${es ? 'Ver en la app →' : 'Open in app →'}
            </a>
          </div>
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:16px 0;text-align:center;">
          <p style="margin:0;font-size:11px;color:#64748b;line-height:1.8;">
            <a href="https://kini.bet" style="color:#10b981;text-decoration:none;">kini.bet</a>
            ${es ? ' · Apuestas deportivas con modelo estadístico' : ' · Sports betting with statistical model'}
            <br>
            <a href="https://kini.bet/unsubscribe?email={{EMAIL}}" style="color:#475569;text-decoration:underline;">
              ${es ? 'Cancelar suscripción' : 'Unsubscribe'}
            </a>
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>`;

  const text = picks.length
    ? `Kini · Picks ${es ? 'de hoy' : 'today'} — ${dateStr}\n\n${picks.map(p => `${p.match}\n${p.desc}\n+${p.edge}% edge @ ${Number(p.odds).toFixed(2)} · ${p.confidence_band}\n`).join('\n')}\nVer en la app: https://kini.bet/app\n\nkini.bet`
    : `Kini · ${es ? 'Sin picks hoy' : 'No picks today'} — ${dateStr}\n\nVer en la app: https://kini.bet/app`;

  return { subject, html, text };
}
