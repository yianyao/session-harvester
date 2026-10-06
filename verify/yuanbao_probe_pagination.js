(async () => {
  const variants = [
    ['top-level offset', { limit: 50, offset: 50 }],
    ['pageNo', { pageNo: 2, pageSize: 50 }],
    ['page', { page: 2, pageSize: 50 }],
    ['pagination.pageNo', { pagination: { pageNo: 2, pageSize: 50 } }],
    ['lastId-blank+offset', { offset: 50, limit: 50, cursor: '', dataTimeTag: '' }],
  ];
  const res = [];
  for (const [name, body] of variants) {
    try {
      const r = await fetch('/api/user/agent/conversation/list', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body),
      });
      const j = await r.json();
      const pg = j.pagination || {};
      res.push(name + ' -> offset=' + pg.offset + ' n=' + (j.conversations ? j.conversations.length : -1) + ' firstId=' + (j.conversations && j.conversations[0] ? j.conversations[0].id : '?'));
    } catch (e) {
      res.push(name + ' -> ERR ' + e.message);
    }
    await new Promise((s) => setTimeout(s, 300));
  }
  return JSON.stringify(res, null, 1);
})()
