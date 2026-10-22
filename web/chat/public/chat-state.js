export function readView(scope, storage) {
  try {
    const value = JSON.parse(storage.getItem('agentai.view.' + scope) || '{}');
    const drafts = Object.fromEntries(Object.entries(value.drafts || {}).filter(([key, text]) =>
      key.length < 150 && typeof text === 'string').slice(-50).map(([key,text]) => [key,text.slice(0,12000)]));
    return { identity: typeof value.identity === 'string' ? value.identity : null, drafts,
      scroll: Number.isFinite(value.scroll) ? Math.max(0,value.scroll) : 0 };
  } catch { return { identity: null, drafts: {}, scroll: 0 }; }
}
export function writeView(scope, value, storage) {
  try { storage.setItem('agentai.view.' + scope, JSON.stringify(value)); } catch {}
}
export function historyGroups(rows, query = '', now = new Date()) {
  const groups = new Map(), unnamed = [];
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  for (const row of rows) {
    if (!String(row.title || '').toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru').trim())) continue;
    if (!row.title || row.title === 'Новый диалог') { unnamed.push(row); continue; }
    const date = new Date(row.updated_at);
    const label = date.toDateString() === now.toDateString() ? 'Сегодня' : date.toDateString() === yesterday.toDateString() ? 'Вчера' : 'Ранее';
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(row);
  }
  return { groups, unnamed };
}
