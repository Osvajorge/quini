export async function onRequest(context) {
  const url = new URL(context.request.url);
  if (url.hostname !== 'kini.bet' && url.hostname !== 'www.kini.bet') {
    return Response.redirect(`https://kini.bet${url.pathname}${url.search}`, 301);
  }
  return context.next();
}
