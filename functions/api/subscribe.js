/**
 * CF Pages Function — Waitlist endpoint
 *
 * SETUP (one-time in Cloudflare dashboard):
 * 1. Pages → kini-ar3 → Settings → Functions → KV namespace bindings
 *    Variable: WAITLIST_KV → namespace: quini-waitlist
 * 2. Pages → kini-ar3 → Settings → Environment variables
 *    RESEND_API_KEY = re_xxx...
 *
 * Endpoints:
 *   GET  /api/subscribe  → { count: N }
 *   POST /api/subscribe  → { ok: true }
 *
 * POST body: { email, phone?, name?, lang?, website? }
 * website = honeypot
 */

import { welcomeEmail, sendEmail } from './_email_templates.js';

export async function onRequestGet({ env }) {
  const count = parseInt(await env.WAITLIST_KV.get('meta:count') || '0', 10);
  return Response.json({ count }, {
    headers: { 'Access-Control-Allow-Origin': '*' },
  });
}

export async function onRequestPost({ request, env }) {
  const cors = { 'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json' };

  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: 'invalid json' }), { status: 400, headers: cors });
  }

  // Honeypot
  if (body.website) {
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: cors });
  }

  const email = (body.email || '').trim().toLowerCase().slice(0, 254);
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
    return new Response(JSON.stringify({ error: 'invalid email' }), { status: 400, headers: cors });
  }

  // Rate limit: 1 submission per IP per 5 min
  const ip = request.headers.get('CF-Connecting-IP') || 'anon';
  const rlKey = `rl:${ip}`;
  if (await env.WAITLIST_KV.get(rlKey)) {
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: cors });
  }

  const leadKey = `lead:${email}`;
  const existing = await env.WAITLIST_KV.get(leadKey);
  let isNew = false;

  if (!existing) {
    isNew = true;
    const lead = {
      email,
      phone: (body.phone || '').trim(),
      name: (body.name || '').trim(),
      ts: new Date().toISOString(),
      lang: body.lang || 'es',
    };
    await env.WAITLIST_KV.put(leadKey, JSON.stringify(lead));
    const prev = parseInt(await env.WAITLIST_KV.get('meta:count') || '0', 10);
    await env.WAITLIST_KV.put('meta:count', String(prev + 1));
  } else if (body.phone || body.name) {
    const lead = JSON.parse(existing);
    if (body.phone) {lead.phone = (body.phone || '').trim();}
    if (body.name) {lead.name = (body.name || '').trim();}
    await env.WAITLIST_KV.put(leadKey, JSON.stringify(lead));
  }

  await env.WAITLIST_KV.put(rlKey, '1', { expirationTtl: 300 });

  // Welcome email — only for new signups, best-effort
  if (isNew && env.RESEND_API_KEY) {
    const lang = body.lang || 'es';
    const name = (body.name || '').trim();
    const { subject, html, text } = welcomeEmail({ name, lang });
    try {
      await sendEmail(env.RESEND_API_KEY, {
        to: email,
        from: 'Kini <hola@kini.bet>',
        reply_to: 'hola@kini.bet',
        subject,
        html,
        text,
      });
    } catch (e) {
      console.error('[subscribe] welcome email failed:', e?.message);
    }
  }

  return new Response(JSON.stringify({ ok: true }), { status: 200, headers: cors });
}

export async function onRequestOptions() {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}
