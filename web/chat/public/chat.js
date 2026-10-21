import { renderMarkdown, sourceName, sourcePage } from './chat-render.js';

const $ = id => document.getElementById(id);
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const paths = {
  plus:'M12 5v14M5 12h14', search:'m21 21-4.3-4.3M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  folder:'M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z',
  'arrow-up-right':'M7 17 17 7M7 7h10v10', 'arrow-up':'M12 19V5m-6 6 6-6 6 6',
  user:'M20 21v-2a5 5 0 0 0-5-5H9a5 5 0 0 0-5 5v2M16 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  settings:'m9 3-1 3-3 1-2 3 2 2-1 3 3 2 1 3h4l1-3 3-1 2-3-2-2 1-3-3-2-1-3ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  menu:'M4 6h16M4 12h16M4 18h16', close:'m6 6 12 12M6 18 18 6',
  trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4M18 4h4',
  sliders:'M4 7h5m4 0h7M4 17h10m4 0h2M9 4v6M14 14v6', compare:'M8 3v18M16 3v18M5 7h6M13 17h6',
  code:'m8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18',
  book:'M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15',
  message:'M21 11a9 9 0 0 1-9 9H4l-2 2V11a9 9 0 0 1 19 0Z',
  file:'M14 2H5v20h14V7Zm0 0v5h5M8 12h8M8 16h6',
  copy:'M8 8h13v13H8ZM16 8V3H3v13h5', check:'m5 12 4 4L20 5',
};
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})) svg.setAttribute(key,value);
  const path = document.createElementNS(svg.namespaceURI,'path'); path.setAttribute('d',paths[name] || paths.file); svg.append(path); return svg;
}
document.querySelectorAll('[data-icon]').forEach(n => n.replaceChildren(icon(n.dataset.icon)));
const state = {token:'',epoch:0,view:0,identity:null,conversations:[],pending:new Map(),cache:new Map(),submitting:false,timer:null,documents:[],panel:0};
let previousFocus = null;
const mobileSidebar=matchMedia('(max-width:700px)');
function announce(text) { $('announcer').textContent = text; }
function notice(text) { $('notice-text').textContent=text; $('notice').hidden=!text; if (text) announce(text); }
function connectedStatus(text, kind='') { $('connection-status').className='connection-status '+kind; $('connection-status').lastElementChild.textContent=text; }
function displayError(error) { if (error.name !== 'AbortError') notice(error.message || 'Не удалось выполнить запрос. Повторите попытку.'); }
async function api(path, options={}) {
  const epoch=state.epoch;
  let response;
  try { response=await fetch('/api'+path,{...options,cache:'no-store',headers:{Authorization:'Bearer '+state.token,'Content-Type':'application/json',...options.headers}}); }
  catch { throw new Error('Нет соединения с сервером. Проверьте подключение и повторите запрос.'); }
  if (epoch !== state.epoch) throw new DOMException('Подключение изменилось','AbortError');
  if (!response.ok) {
    const messages={401:'Ключ не принят. Проверьте его и подключитесь заново.',403:'У этого ключа нет доступа к операции.',404:'Диалог, документ или задание больше не доступны.',409:'Запрос уже выполняется. Отменить можно только запрос в очереди.',413:'Запрос слишком большой.',422:'Проверьте данные запроса.',429:'Очередь занята. Подождите и повторите отправку.',502:'Нет соединения с AgentAI. Проверьте сервер и повторите запрос.',504:'AgentAI не ответил вовремя. Повторите запрос.',503:'Сервис сейчас недоступен. Проверьте AgentAI и загруженные модели, затем повторите.'};
    const error=new Error(messages[response.status] || 'Не удалось выполнить запрос (HTTP '+response.status+').');
    error.status=response.status; throw error;
  }
  return response;
}
async function json(path, body, method='POST') { const epoch=state.epoch; const data=await (await api(path,{method,body:JSON.stringify(body)})).json(); if (epoch!==state.epoch) throw new DOMException('Подключение изменилось','AbortError'); return data; }
function isCurrent(epoch, view) { return epoch === state.epoch && view === state.view; }
function updateSend() { $('send').disabled=!$('question').value.trim() || state.submitting || state.pending.has(state.identity); }
function resizeInput() { const q=$('question'); q.style.height='auto'; q.style.height=Math.min(q.scrollHeight,170)+'px'; $('character-count').hidden=q.value.length<10000; $('character-count').textContent=q.value.length.toLocaleString('ru')+' / 12 000'; updateSend(); }
function scrollBottom() { $('timeline').scrollTop=$('timeline').scrollHeight; }
function syncSidebar() {
  const app=document.querySelector('.app');
  if (!mobileSidebar.matches) app.classList.remove('sidebar-open');
  const open=mobileSidebar.matches && app.classList.contains('sidebar-open');
  $('sidebar').inert=mobileSidebar.matches && !open;
  $('sidebar-backdrop').hidden=!open;
  $('mobile-menu').setAttribute('aria-expanded',String(open));
}
function closeSidebar(restoreFocus=false) { const wasOpen=document.querySelector('.app').classList.contains('sidebar-open'); document.querySelector('.app').classList.remove('sidebar-open'); syncSidebar(); if (restoreFocus && wasOpen && mobileSidebar.matches) $('mobile-menu').focus(); }
mobileSidebar.addEventListener('change',syncSidebar);
function setTitle(title='Новый диалог') { $('chat-title').textContent=title; document.title=title==='Новый диалог'?'AgentAI — чат':title+' — AgentAI'; $('delete-chat').hidden=!state.identity; }
function openConnection() { $('connection-error').hidden=true; if (!$('connection-dialog').open) $('connection-dialog').showModal(); }
function disconnect() {
  state.epoch++; state.view++; state.panel++; clearTimeout(state.timer); state.token=''; state.identity=null; state.conversations=[]; state.pending.clear(); state.cache.clear(); state.documents=[]; state.submitting=false;
  $('messages').replaceChildren(); $('welcome').hidden=false; $('question').value=''; $('history-search').value=''; $('access-key').value=''; $('account-name').textContent='Подключение'; $('account-detail').textContent='Личный ключ доступа'; $('disconnect').hidden=true;
  closePanel(); setTitle(); renderHistory(); connectedStatus('Не подключено'); notice(''); resizeInput();
}
function renderHistory() {
  const query=$('history-search').value.trim().toLocaleLowerCase('ru');
  const rows=state.conversations.filter(c=>c.title.toLocaleLowerCase('ru').includes(query));
  $('history').replaceChildren(); $('history-count').textContent=state.conversations.length || '';
  if (!rows.length) { $('history').append(el('p',!state.token?'Подключитесь, чтобы увидеть историю.':query?'Диалоги не найдены.':'Здесь появятся ваши диалоги.','history-empty')); return; }
  for (const c of rows) {
    const b=el('button',undefined,'history-item'+(state.identity===c.id?' active':'')); b.type='button'; b.dataset.conversationId=c.id; b.append(icon('message'),el('span',c.title)); b.title=c.title;
    if (state.identity===c.id) b.setAttribute('aria-current','page');
    b.onclick=()=>selectConversation(c.id).catch(displayError); $('history').append(b);
  }
}
async function loadConversations() { const epoch=state.epoch; const data=await (await api('/conversations')).json(); if (epoch!==state.epoch) return; state.conversations=data.conversations; renderHistory(); const current=state.conversations.find(c=>c.id===state.identity); if (current && !state.pending.has(state.identity)) setTitle(current.title); }
function newChat() {
  if (state.submitting) return;
  state.view++; state.identity=null; $('messages').replaceChildren(); $('welcome').hidden=false; $('question').value=''; notice(''); setTitle(); renderHistory(); closeSidebar(); resizeInput(); $('question').focus();
}
async function selectConversation(identity) {
  if (state.submitting) return;
  const epoch=state.epoch, view=++state.view; state.identity=identity; closeSidebar(); renderHistory(); setTitle(state.conversations.find(c=>c.id===identity)?.title); notice('');
  $('welcome').hidden=true; $('messages').replaceChildren(el('p','Загружаем историю…','panel-note')); updateSend();
  try {
    const data=await (await api('/conversations/'+identity)).json(); if (!isCurrent(epoch,view)) return;
    $('messages').replaceChildren();
    for (const turn of data.turns) { userMessage(turn.question); const cached=state.cache.get(identity+'\n'+turn.question+'\n'+turn.answer); assistantMessage(cached || {answer:turn.answer,sources:turn.sources}); }
    if (!data.turns.length && !state.pending.has(identity)) $('messages').append(el('p','Диалог пока пуст. Задайте первый вопрос.','panel-note'));
    appendPending(); scrollBottom();
  } catch (error) { if (isCurrent(epoch,view)) { $('messages').replaceChildren(el('p',error.message,'failed-message')); displayError(error); } }
}
function userMessage(text) { const box=el('article',undefined,'message user'); box.setAttribute('aria-label','Вы'); box.append(el('div',text,'user-bubble')); $('messages').append(box); }
function heading() { const h=el('div',undefined,'assistant-heading'); h.append(el('span','a','brand-symbol'),el('span','AgentAI')); return h; }
function button(text, fn, cls='') { const b=el('button',text,cls); b.type='button'; b.onclick=()=>Promise.resolve().then(fn).catch(displayError); return b; }
async function copy(text, b) {
  try { await navigator.clipboard.writeText(text); b.replaceChildren(icon('check'),el('span','Скопировано')); announce('Скопировано'); setTimeout(()=>{ if (b.isConnected) b.replaceChildren(icon('copy'),el('span','Копировать')); },1800); }
  catch { notice('Не удалось скопировать. Выделите текст и скопируйте вручную.'); }
}
function assistantMessage(result) {
  const box=el('article',undefined,'message assistant'); box.setAttribute('aria-label','Ответ AgentAI'); box.append(heading());
  const sources=result.used_sources?.length?result.used_sources:result.sources || [];
  const body=el('div',undefined,'answer-body'); renderMarkdown(body,result.answer,sources,{openSource,copy,icon}); box.append(body);
  const warnings=[...new Set(result.warnings || [])];
  if (result.validation?.passed===false && !warnings.length) warnings.push('Ответ не прошёл автоматическую проверку. Сверьте утверждения с источниками.');
  if (warnings.length) box.append(el('p',warnings.join('\n'),'answer-warning'));
  if (sources.length) {
    const title=el('div',undefined,'sources-heading'); title.append(icon('book'),el('span','Источники · '+sources.length)); box.append(title);
    const list=el('div',undefined,'source-list');
    for (const row of sources) { const b=button('',()=>openSource(row),'source-chip'); b.append(icon('file'),el('span',sourceName(row)),el('small',sourcePage(row))); b.title=sourceName(row)+' '+sourcePage(row); list.append(b); }
    box.append(list);
  }
  const actions=el('div',undefined,'message-actions'); const b=button('',()=>copy(result.answer,b)); b.append(icon('copy'),el('span','Копировать')); actions.append(b); box.append(actions); $('messages').append(box); return box;
}
function appendPending() {
  $('pending-message')?.remove(); const job=state.pending.get(state.identity); updateSend(); if (!job) return;
  const box=el('article',undefined,'message assistant'); box.id='pending-message'; box.append(heading());
  const line=el('div',undefined,'pending'); line.append(el('span',undefined,'pending-indicator'),el('span',job.state==='queued'?'Запрос в очереди…':'Готовит ответ…')); box.append(line);
  box.append(el('p','Можно перейти в другой диалог — запрос продолжит выполняться.','pending-detail'));
  if (job.state==='queued') box.append(button('Отменить запрос',async()=>{ await api('/jobs/'+job.id,{method:'DELETE'}); await pollJobs(); },'retry-button'));
  $('messages').append(box);
}
function failedMessage(text, question) {
  const box=el('article',undefined,'message assistant'); box.append(heading(),el('p',text,'failed-message'));
  if (question) box.append(button('Вернуть вопрос в поле ввода',()=>{ $('question').value=question; resizeInput(); $('question').focus(); },'retry-button'));
  $('messages').append(box); announce(text);
}
async function sendQuestion(event) {
  event?.preventDefault(); const question=$('question').value.trim(); if (!question || state.submitting || state.pending.has(state.identity)) return;
  if (!state.token) { openConnection(); return; }
  const epoch=state.epoch; state.submitting=true; updateSend(); notice('');
  try {
    if (!state.identity) { const conversation=await json('/conversations',{}); state.identity=conversation.id; state.view++; }
    const identity=state.identity; const job=await json('/jobs/chat',{question,conversation_id:identity});
    if (epoch!==state.epoch) return;
    state.pending.set(identity,{...job,conversation_id:identity,question});
    $('welcome').hidden=true; $('messages').querySelector('.panel-note')?.remove(); userMessage(question); appendPending(); $('question').value=''; resizeInput(); scrollBottom(); setTitle(question.slice(0,80));
    await loadConversations(); schedulePoll(400); announce('Вопрос отправлен.');
  } catch (error) { displayError(error); }
  finally { if (epoch===state.epoch) { state.submitting=false; updateSend(); } }
}
function schedulePoll(delay=1600) { clearTimeout(state.timer); if (state.token && state.pending.size) state.timer=setTimeout(()=>pollJobs().catch(error=>{ displayError(error); schedulePoll(5000); }),delay); }
async function pollJobs() {
  const epoch=state.epoch;
  for (const [identity,tracked] of [...state.pending]) {
    let job;
    try { job=await (await api('/jobs/'+tracked.id)).json(); }
    catch (error) { if (error.name==='AbortError') return; if ([401,403,404].includes(error.status)) { state.pending.delete(identity); if (identity===state.identity) { $('pending-message')?.remove(); failedMessage(error.message,tracked.question); } continue; } throw error; }
    if (epoch!==state.epoch) return;
    if (['queued','running'].includes(job.state)) { const changed=tracked.state!==job.state; state.pending.set(identity,{...tracked,...job}); if (changed && identity===state.identity) appendPending(); continue; }
    state.pending.delete(identity);
    if (job.state==='succeeded' && job.result) {
      const result=job.result; if (tracked.question) state.cache.set(identity+'\n'+tracked.question+'\n'+result.answer,result);
      if (identity===state.identity) {
        await selectConversation(identity);
        announce('Ответ готов.');
      }
      await loadConversations();
    } else if (identity===state.identity) { $('pending-message')?.remove(); failedMessage(job.state==='cancelled'?'Запрос отменён.':job.error || 'Не удалось получить ответ. Попробуйте ещё раз.',tracked.question); }
  }
  updateSend(); schedulePoll();
}
async function connect(event) {
  event.preventDefault(); const token=$('access-key').value.trim(); if (!token) return;
  const draft=$('question').value; disconnect(); $('question').value=draft; resizeInput(); state.token=token; const epoch=state.epoch; $('connect-button').disabled=true; $('connect-button').textContent='Подключаемся…'; $('connection-error').hidden=true;
  try {
    const user=await (await api('/session')).json();
    if (epoch!==state.epoch) return;
    $('access-key').value=''; $('account-name').textContent=user.name; $('account-detail').textContent='Подключено к AgentAI'; $('disconnect').hidden=false; connectedStatus('Подключено','connected');
    await loadConversations(); const jobs=await (await api('/jobs')).json();
    for (const job of jobs.jobs) if (job.kind==='chat' && job.conversation_id && ['queued','running'].includes(job.state)) state.pending.set(job.conversation_id,job);
    schedulePoll(); $('connection-dialog').close(); closeSidebar(); $('question').focus();
    try { await api('/ready'); connectedStatus('Готов к работе','connected'); }
    catch (error) { if (error.name!=='AbortError') { connectedStatus('Сервисы недоступны','warning'); notice('Подключение установлено, но модель или поиск недоступны. Проверьте LM Studio и сервисы.'); } }
  } catch (error) { if (epoch===state.epoch) { state.token=''; state.conversations=[]; renderHistory(); connectedStatus('Не подключено'); $('connection-error').textContent=error.message; $('connection-error').hidden=false; } }
  finally { $('connect-button').disabled=false; $('connect-button').textContent='Подключиться'; updateSend(); }
}
function openPanel(title) { previousFocus=document.activeElement; state.panel++; $('panel-title').textContent=title; $('panel-body').replaceChildren(); $('detail-panel').hidden=false; $('close-panel').focus(); return state.panel; }
function closePanel() { state.panel++; $('detail-panel').hidden=true; $('panel-body').replaceChildren(); if (previousFocus?.isConnected) { if (previousFocus.closest('[inert]')) $('mobile-menu').focus(); else previousFocus.focus(); } }
async function downloadSource(row) {
  const epoch=state.epoch;
  let path=row.download_url;
  if (!path) { const version=row.version || row.citation?.match(/@([0-9a-f-]{36})(?:\?|#|$)/i)?.[1]; if (version) path='/documents/'+version+'/original'; }
  if (!path || !/^\/documents\/[0-9a-f-]{36}\/original$/i.test(path)) throw Error('Оригинал этого источника недоступен для скачивания.');
  const response=await api(path); const blob=await response.blob(); if (epoch!==state.epoch) return; const url=URL.createObjectURL(blob); const a=el('a'); a.href=url; a.download=sourceName(row); document.body.append(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function openSource(row) {
  const panel=openPanel('Источник'); const epoch=state.epoch; const body=$('panel-body');
  body.append(el('h3',sourceName(row)),el('p',[row.source,sourcePage(row)].filter(Boolean).join(' · '),'source-location'));
  if (row.citation?.startsWith('document:')) body.append(button('Скачать оригинал',()=>downloadSource(row),'secondary-button'));
  const excerpt=el('div',row.text || 'Загружаем фрагмент…','source-excerpt'); body.append(excerpt);
  if (row.validity==='obsolete') body.append(el('p','Документ отмечен как устаревший.','answer-warning'));
  if (row.note) body.append(el('p',row.note,'panel-note'));
  if (row.text) return;
  try { const data=await (await api('/sources/'+encodeURIComponent(row.id)+'?offset=0&limit=1')).json(); if (panel===state.panel && epoch===state.epoch) excerpt.textContent=data.chunks.map(c=>c.text).join('\n\n'); }
  catch (error) { if (panel===state.panel && epoch===state.epoch) excerpt.textContent=error.message; }
}
async function openDocuments() {
  if (!state.token) { openConnection(); return; }
  closeSidebar(true);
  const panel=openPanel('Документы'); const body=$('panel-body'); body.append(el('p','Загружаем документы…','panel-note'));
  try {
    const data=await (await api('/documents?limit=200')).json(); if (panel!==state.panel) return; state.documents=data.versions; body.replaceChildren();
    const search=el('input'); search.type='search'; search.placeholder='Найти документ'; search.setAttribute('aria-label','Найти документ');
    const list=el('div'); const link=el('a','Загрузка и управление документами'); const consoleURL=new URL('/library',location.origin); consoleURL.port='8000'; link.href=consoleURL.href; link.target='_blank'; link.rel='noopener'; body.append(search,link,list);
    function render() {
      list.replaceChildren(); const docs=state.documents.filter(d=>d.filename.toLocaleLowerCase('ru').includes(search.value.toLocaleLowerCase('ru')));
      if (!docs.length) list.append(el('p','Документы не найдены.','panel-note'));
      for (const d of docs) { const row=el('div',undefined,'document-row'); row.append(el('strong',d.filename),el('p',d.source+' · '+({indexed:'В поиске',pending:'Индексируется',failed:'Не проиндексирован',superseded:'Предыдущая версия',removed:'Убран из поиска'}[d.state] || d.state))); row.append(button('Скачать',()=>downloadSource({...d,citation:'document:'+d.source+'/'+d.filename+'@'+d.version}))); list.append(row); }
      if (data.versions.length===200) list.append(el('p','Показаны последние 200 версий. Остальные доступны в библиотеке.','panel-note'));
    }
    search.oninput=render; render();
  } catch (error) { if (panel===state.panel) body.replaceChildren(el('p',error.message,'failed-message')); }
}
$('connection-form').addEventListener('submit',connect);
$('open-connection').onclick=openConnection;
$('close-connection').onclick=()=>{ $('access-key').value=''; $('connection-dialog').close(); };
$('connection-dialog').addEventListener('close',()=>{ $('access-key').value=''; });
$('disconnect').onclick=()=>{ disconnect(); $('connection-dialog').close(); };
$('new-chat').onclick=newChat;
$('history-search').oninput=renderHistory;
$('question').oninput=resizeInput;
$('question').onkeydown=event=>{ if (event.key==='Enter' && !event.shiftKey && !event.isComposing && matchMedia('(pointer:fine)').matches) { event.preventDefault(); $('chat-form').requestSubmit(); } };
$('chat-form').addEventListener('submit',sendQuestion);
document.querySelectorAll('[data-prompt]').forEach(b=>{ b.onclick=()=>{ $('question').value=b.dataset.prompt; resizeInput(); $('question').focus(); }; });
$('mobile-menu').onclick=()=>{ document.querySelector('.app').classList.add('sidebar-open'); syncSidebar(); $('new-chat').focus(); };
$('sidebar-backdrop').onclick=()=>closeSidebar(true);
$('open-documents').onclick=openDocuments;
$('close-panel').onclick=closePanel;
$('dismiss-notice').onclick=()=>notice('');
$('delete-chat').onclick=()=>{ if (state.pending.has(state.identity)) { notice('Дождитесь ответа или отмените запрос в очереди, затем удалите диалог.'); return; } $('delete-dialog').showModal(); };
$('delete-dialog').addEventListener('close',async()=>{ if ($('delete-dialog').returnValue!=='delete' || !state.identity) return; try { await api('/conversations/'+state.identity,{method:'DELETE'}); newChat(); await loadConversations(); } catch(error) { displayError(error); } });
document.addEventListener('keydown',event=>{ if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase()==='k') { event.preventDefault(); newChat(); } if (event.key==='Escape') { closeSidebar(true); if (!$('detail-panel').hidden) closePanel(); } });
syncSidebar();
resizeInput();
