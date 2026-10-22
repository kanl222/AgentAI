import { colorizeCode } from './highlighter.js';
// Model/source text stays text. Syntax spans come only from the local highlighter.
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
export function sourceName(row) {
  if (row.filename) return row.filename;
  const citation=row.citation || '';
  const document=citation.match(/^(?:document|attachment):[^/]+\/(.*?)(?:@[\da-f-]{36})?(?:\?|#|$)/i);
  return document?.[1] || citation.split('#')[0] || 'Источник';
}
export function sourcePage(row) { const page=row.citation?.match(/[?&]page=(\d+)/)?.[1]; return page?'стр. '+page:row.citation?.match(/#L(\d+)(?:-L(\d+))?/)?.[0].replace('#','') || ''; }
export function renderMarkdown(container, text, sources, actions) {
  const inline=(parent,text)=>{
    const tokens=/(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\)|(?:document|attachment):[^\s<>"'`\]\)]+|\[S\d+\])/g;
    let cursor=0;
    for (const match of text.matchAll(tokens)) {
      parent.append(document.createTextNode(text.slice(cursor,match.index))); const token=match[0];
      if (token.startsWith('`')) parent.append(el('code',token.slice(1,-1)));
      else if (token.startsWith('**')) parent.append(el('strong',token.slice(2,-2)));
      else {
        const link=token.match(/^\[([^\]]+)\]\((.+)\)$/); const uri=link?.[2] || token.replace(/[.,;]+$/,'');
        const index=sources.findIndex(s=>s.citation===uri || '['+s.ref+']'===uri);
        if (index>=0) { const b=el('button','['+(index+1)+']'); b.type='button'; b.className='citation'; b.title=sourceName(sources[index]); b.setAttribute('aria-label','Открыть источник '+(index+1)); b.onclick=()=>actions.openSource(sources[index]); parent.append(b); }
        else if (link && sources.some(s=>s.citation===link[2]) && /^https:\/\//.test(link[2])) { const a=el('a',link[1]); a.href=link[2]; a.target='_blank'; a.rel='noopener noreferrer'; parent.append(a); }
        else parent.append(document.createTextNode(token));
      }
      cursor=match.index+token.length;
    }
    parent.append(document.createTextNode(text.slice(cursor)));
  };
  const cleaned=String(text || '').replace(/\r/g,'').split(/\n\nSources:\s*\n/)[0];
  const lines=cleaned.split('\n'); let i=0;
  const isTableRule=line=>/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line || '');
  const cells=line=>line.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(s=>s.trim());
  while (i<lines.length) {
    const line=lines[i]; if (!line.trim()) { i++; continue; }
    if (/^\s*```/.test(line)) {
      const language=line.trim().slice(3).trim(); const code=[]; i++; while(i<lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]); if (i<lines.length) i++;
      const box=el('div'); box.className='code-block'; const header=el('div'); header.className='code-header'; header.append(el('span',language || 'Код'));
      const b=el('button'); b.type='button'; b.append(actions.icon('copy'),el('span','Копировать')); b.onclick=()=>actions.copy(code.join('\n'),b); header.append(b);
      const pre=el('pre'); const content=el('code'); colorizeCode(content,code.join('\n'),language); pre.append(content); box.append(header,pre); container.append(box); continue;
    }
    if (line.includes('|') && isTableRule(lines[i+1])) {
      const scroller=el('div'); scroller.className='table-scroll'; const table=el('table'),thead=el('thead'),header=el('tr');
      for (const text of cells(line)) { const th=el('th'); th.scope='col'; inline(th,text); header.append(th); } thead.append(header); table.append(thead); i+=2;
      const body=el('tbody'); while(i<lines.length && lines[i].includes('|') && lines[i].trim()) { const tr=el('tr'); for(const text of cells(lines[i++])) {const td=el('td');inline(td,text);tr.append(td);}body.append(tr); }table.append(body);scroller.append(table);container.append(scroller);continue;
    }
    const heading=line.match(/^\s*(#{1,6})\s+(.+)$/);
    if (heading) { const h=el('h'+Math.min(heading[1].length+1,4)); inline(h,heading[2]); container.append(h);i++;continue; }
    const item=line.match(/^\s*(?:([-*])|\d+[.)])\s+(.+)$/);
    if (item) { const list=el(item[1]?'ul':'ol'); while(i<lines.length) {const m=lines[i].match(/^\s*(?:([-*])|\d+[.)])\s+(.+)$/);if(!m || Boolean(m[1])!==Boolean(item[1]))break;const li=el('li');inline(li,m[2]);list.append(li);i++;}container.append(list);continue; }
    if (/^\s*>/.test(line)) { const quote=el('blockquote'); while(i<lines.length && /^\s*>/.test(lines[i])) {inline(quote,lines[i++].replace(/^\s*>\s?/,''));quote.append(el('br'));}container.append(quote);continue; }
    const p=el('p'); inline(p,line); i++;
    while(i<lines.length && lines[i].trim() && !/^\s*(?:```|#{1,6}\s|[-*]\s|\d+[.)]\s|>)/.test(lines[i]) && !(lines[i].includes('|') && isTableRule(lines[i+1]))) {p.append(el('br'));inline(p,lines[i++]);}container.append(p);
  }
}
