/**
 * CF Pages Function — Waitlist endpoint
 *
 * SETUP (one-time in Cloudflare dashboard):
 * 1. Go to Workers & Pages → quini-bzs → Settings → Functions
 * 2. Under "KV namespace bindings", click "Add binding"
 * 3. Variable name: WAITLIST_KV
 * 4. KV namespace: create one called "quini-waitlist" in KV section first
 * 5. Save and redeploy
 *
 * Endpoints:
 *   GET  /api/subscribe  → { count: N }   (public counter)
 *   POST /api/subscribe  → { ok: true }   (save lead)
 *
 * POST body (JSON): { email, phone?, name?, website? }
 * website is honeypot — bots fill it, humans don't
 */

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

  if (body.website) {
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: cors });
  }

  const email = (body.email || '').trim().toLowerCase().slice(0, 254);
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
    return new Response(JSON.stringify({ error: 'invalid email' }), { status: 400, headers: cors });
  }

  // Rate limit: 1 submission per IP per 5 minutes
  const ip = request.headers.get('CF-Connecting-IP') || 'anon';
  const rlKey = `rl:${ip}`;
  if (await env.WAITLIST_KV.get(rlKey)) {
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: cors }); // silent pass
  }

  // Idempotent: update phone/name if email already exists
  const leadKey = `lead:${email}`;
  const existing = await env.WAITLIST_KV.get(leadKey);
  if (!existing) {
    const lead = {
      email,
      phone: (body.phone || '').trim(),
      name: (body.name || '').trim(),
      ts: new Date().toISOString(),
      lang: body.lang || 'es',
    };
    await env.WAITLIST_KV.put(leadKey, JSON.stringify(lead));
    // Increment counter
    const prev = parseInt(await env.WAITLIST_KV.get('meta:count') || '0', 10);
    await env.WAITLIST_KV.put('meta:count', String(prev + 1));
  } else if (body.phone || body.name) {
    // Update step-2 data
    const lead = JSON.parse(existing);
    if (body.phone) lead.phone = (body.phone || '').trim();
    if (body.name) lead.name = (body.name || '').trim();
    await env.WAITLIST_KV.put(leadKey, JSON.stringify(lead));
  }

  // Rate-limit this IP for 5 min
  await env.WAITLIST_KV.put(rlKey, '1', { expirationTtl: 300 });

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
