(async () => {
  const out = [];
  for (let off = 0; off < 1250; off += 50) {
    const r = await fetch('/api/user/agent/conversation/list', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ limit: 50, offset: off }),
    });
    const j = await r.json();
    await fetch('http://127.0.0.1:8766/list/' + off / 50, {
      mode: 'no-cors',
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify(j),
    });
    out.push(j.conversations ? j.conversations.length : 0);
    await new Promise((s) => setTimeout(s, 400));
  }
  return 'pages=' + out.length + ' counts=' + JSON.stringify(out);
})()
