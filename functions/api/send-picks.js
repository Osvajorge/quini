/**
 * CF Pages Function — Send today's BET picks to all waitlist subscribers
 *
 * Called from GitHub Actions after predictions update.
 * Protected by PICKS_SECRET env var.
 *
 * POST /api/send-picks
 * Headers: Authorization: Bearer <PICKS_SECRET>
 *
 * SETUP:
 * 1. Pages → kini-ar3 → Settings → Environment variables
 *    RESEND_API_KEY, PICKS_SECRET
 * 2. KV binding WAITLIST_KV already configured
 */

import { picksEmail, sendEmail } from './_email_templates.js';

const PREDICTIONS_URL = 'https://kini.bet/data/predictions.json';
const BATCH_SIZE = 10;   // send N emails, then yield (avoids rate limits)
const BATCH_DELAY = 500; // ms between batches

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

export async function onRequestPost({ request, env }) {
  // Auth check
  const auth = request.headers.get('Authorization') || '';
  const secret = env.PICKS_SECRET;
  if (!secret || auth !== `Bearer ${secret}`) {
    return new Response(JSON.stringify({ error: 'unauthorized' }), { status: 401 });
  }

  if (!env.RESEND_API_KEY) {
    return new Response(JSON.stringify({ error: 'RESEND_API_KEY not configured' }), { status: 500 });
  }

  // Load today's picks from predictions.json
  let todayPicks = [];
  const today = new Date().toISOString().slice(0, 10);
  try {
    const r = await fetch(PREDICTIONS_URL, { cf: { cacheTtl: 0 } });
    const data = await r.json();
    const fixtures = data.fixtures || [];

    // Picks for today (or tomorrow) that have BET signals
    const relevant = fixtures.filter(f => {
      const fDate = (f.commence_time || '').slice(0, 10);
      return fDate === today && !f.completed;
    });

    for (const f of relevant) {
      const bets = f.bets && f.bets.length ? f.bets : (f.best_bet ? [f.best_bet] : []);
      for (const b of bets) {
        const desc = b.description_es || b.market || '—';
        todayPicks.push({
          match: `${f.home} vs ${f.away}`,
          desc,
          edge: b.edge,
          odds: b.best_odds || b.odds,
          confidence_band: b.confidence_band || b.confidence || '—',
          kelly_pct: b.kelly_pct || null,
        });
      }
    }
  } catch (e) {
    console.error('[send-picks] predictions fetch failed:', e);
  }

  // Guard: only send once per UTC day (workflow runs every 5 min)
  const sentKey = `picks_sent:${today}`;
  const alreadySent = await env.WAITLIST_KV.get(sentKey);
  if (alreadySent && !request.headers.get('X-Force-Send')) {
    return Response.json({ ok: true, skipped: true, reason: 'already sent today', date: today });
  }

  // Collect all leads from KV
  const leads = [];
  let cursor;
  do {
    const result = await env.WAITLIST_KV.list({ prefix: 'lead:', cursor, limit: 1000 });
    for (const key of result.keys) {
      // Parse email from key name "lead:email@example.com"
      const email = key.name.replace(/^lead:/, '');
      const raw = await env.WAITLIST_KV.get(key.name);
      if (raw) {
        try {
          const lead = JSON.parse(raw);
          leads.push({ email, lang: lead.lang || 'es', name: lead.name || '' });
        } catch {
          leads.push({ email, lang: 'es', name: '' });
        }
      }
    }
    cursor = result.list_complete ? undefined : result.cursor;
  } while (cursor);

  if (!leads.length) {
    return Response.json({ ok: true, sent: 0, picks: todayPicks.length, message: 'no leads' });
  }

  // Send in batches
  let sent = 0, failed = 0;
  for (let i = 0; i < leads.length; i += BATCH_SIZE) {
    const batch = leads.slice(i, i + BATCH_SIZE);
    await Promise.allSettled(batch.map(async lead => {
      const { subject, html, text } = picksEmail({
        picks: todayPicks,
        date: today,
        lang: lead.lang,
      });
      // Replace unsubscribe placeholder
      const personalHtml = html.replace('{{EMAIL}}', encodeURIComponent(lead.email));

      try {
        await sendEmail(env.RESEND_API_KEY, {
          to: lead.email,
          from: 'Kini Picks <picks@kini.bet>',
          reply_to: 'hola@kini.bet',
          subject,
          html: personalHtml,
          text,
          headers: {
            'List-Unsubscribe': `<https://kini.bet/unsubscribe?email=${encodeURIComponent(lead.email)}>`,
            'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
          },
        });
        sent++;
      } catch (e) {
        console.error(`[send-picks] failed ${lead.email}:`, e?.message);
        failed++;
      }
    }));

    if (i + BATCH_SIZE < leads.length) await sleep(BATCH_DELAY);
  }

  // Mark as sent for today (expire after 26h to handle timezone drift)
  await env.WAITLIST_KV.put(sentKey, new Date().toISOString(), { expirationTtl: 93600 });

  return Response.json({
    ok: true,
    sent,
    failed,
    picks: todayPicks.length,
    leads: leads.length,
    date: today,
  });
}

export async function onRequestOptions() {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    },
  });
}
