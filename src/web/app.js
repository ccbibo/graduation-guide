'use strict';
const $ = s => document.querySelector(s);
const make = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
// current is the displayed, completed result; pending is a separate in-flight request.
const state = { current: null, pending: null, busy: false, source: null, scope: null, controller: null, activeChat: null, turns: [], chats: [], scrollPositions: {}, drafts: {}, ready: false, navSeq: 0, sourceSeq: 0 };
const slotLabels = {degree_level:'學位別',dept:'系所',enrollment_year:'入學學年度',target_academic_term:'目標學期'};
const statusLabels = {A:'可引用原文',B:'適用性待核實',C:'無法確認答案',reference_only:'僅供原文對照'};

async function api(path, body, signal) {
  const opts = body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  if(signal)opts.signal=signal;
  let response;
  try { response = await fetch(path, opts); }
  catch(e) {
    if(signal?.aborted || e?.name==='AbortError')throw e;
    if(e instanceof TypeError)throw {code:'service_unavailable',message:'無法連上本機查詢服務，請確認服務仍在執行後重試。'};
    throw e;
  }
  let data;
  try { data = await response.json(); } catch (_) { throw {code:'service_unavailable',message:'無法讀取本機服務，請確認服務仍在執行。'}; }
  if (!response.ok || data.ok === false) throw data.error || {code:'request_failed',message:'查詢暫時無法完成。'};
  return data;
}
function errorMessage(e) { return e && typeof e.message === 'string' ? e.message : '本機服務無法完成操作，請稍後重試。'; }
function showError(e) {
  const box = $('#request-error'); box.replaceChildren(make('span','',errorMessage(e)), make('span','error-code',e.code || 'request_failed')); box.hidden=false;
}
function showView(which) {
  document.body.dataset.view=which;
  setEditorialStage(which==='thread'?'answer':'ask');
  ['welcome','thread','scope-view','route-error'].forEach(id => { $('#'+id).hidden = id !== which; });
}
function setBusy(value) {
  document.body.classList.toggle('is-querying',value);
  state.busy=value; $('#question').disabled=value;
  $('#send').disabled=value || !$('#question').value.trim() || Array.from($('#question').value).length>200;
  $('#send').textContent=value ? '…' : '↑'; $('#send').setAttribute('aria-label',value ? '查詢中' : '送出問題');
  $('#new-query').disabled=value;
  document.querySelectorAll('.chat-list .chat-entry').forEach(e=>e.setAttribute('aria-disabled',String(value || e.dataset.expired==='true')));
  document.querySelectorAll('.clarify-box button,.clarify-box input').forEach(e => e.disabled=value);
}
function inputChanged() {
  const n = Array.from($('#question').value).length; $('#input-count').textContent=`${n} / 200`;
  $('#send').disabled=state.busy || n>200 || !$('#question').value.trim();
  $('#input-count').classList.toggle('too-long',n>200);
  $('#question').style.height='auto'; $('#question').style.height=Math.min($('#question').scrollHeight,160)+'px';
}
function route(path, replace=false) {
  if (location.pathname+location.search !== path) history[replace?'replaceState':'pushState']({},'',path);
}
function nearBottom(){const el=$('#conversation');return el.scrollHeight-el.clientHeight-el.scrollTop<100;}
function scrollToBottom(){const el=$('#conversation');el.scrollTop=el.scrollHeight;}
function revealLatestAnswer(){
  const el=$('#conversation'),turn=$('#thread').lastElementChild;
  if(turn)turn.classList.add('latest-reveal');
  if(turn)el.scrollTop+=turn.getBoundingClientRect().top-el.getBoundingClientRect().top-16;
}
function updateChats(chats){
  state.chats=chats;const area=$('#chat-list');area.replaceChildren();
  for(const chat of chats){
    const b=make('a','chat-entry',chat.title);b.href='/chat/'+chat.conversation_id;b.setAttribute('aria-disabled',String(state.busy || chat.expired));
    b.classList.toggle('active',chat.conversation_id===state.activeChat);b.setAttribute('aria-current',chat.conversation_id===state.activeChat?'page':'false');
    b.dataset.expired=String(chat.expired);b.title=chat.expired?'聊天已逾時':chat.title+'（⌘ 點擊可另開聊天）';
    b.addEventListener('click',e=>{if(state.busy || chat.expired){e.preventDefault();return;}if(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)return;e.preventDefault();switchChat(chat.conversation_id);});area.append(b);
  }
}
function useSnapshot(snapshot,autoScroll=false){
  state.activeChat=snapshot.conversation_id;state.turns=snapshot.turns || [];state.current=state.turns.at(-1) || null;
  renderHistory(autoScroll);const mem=$('#memory-status');const entries=Object.entries(snapshot.remembered_conditions || {});
  mem.textContent=entries.length?'這個聊天記住：'+entries.map(([k,v])=>`${slotLabels[k]} ${typeof v==='object'?JSON.stringify(v):v}`).join(' · '):'這個聊天的條件獨立記憶';
  mem.hidden=false;$('#session-title').hidden=true;
}
async function switchChat(id){
  if(state.busy || id===state.activeChat)return;
  const nav=++state.navSeq;setBusy(true);
  if(state.activeChat){state.scrollPositions[state.activeChat]=$('#conversation').scrollTop;state.drafts[state.activeChat]=$('#question').value;}
  try{const r=await api('/api/conversation?conversation_id='+encodeURIComponent(id));if(nav!==state.navSeq)return;closeSources(false);useSnapshot(r.conversation);route('/chat/'+id);updateChats(state.chats);$('#question').value=state.drafts[id] || '';inputChanged();$('#conversation').scrollTop=state.scrollPositions[id] ?? 0;$('#request-error').hidden=true;}catch(e){showError(e);}finally{setBusy(false);}
}
async function newQuery(){
  if(state.busy)return;
  if(state.activeChat && !state.turns.length){$('#question').focus();return;}
  const empty=state.chats.find(c=>!c.expired && c.turn_count===0);
  if(empty)return switchChat(empty.conversation_id);
  const nav=++state.navSeq;setBusy(true);
  if(state.activeChat)state.drafts[state.activeChat]=$('#question').value;
  try{const r=await api('/api/conversations',{});if(nav!==state.navSeq)return;if(state.activeChat)state.scrollPositions[state.activeChat]=$('#conversation').scrollTop;
    closeSources(false);state.pending=null;useSnapshot(r.conversation);updateChats(r.conversations);$('#question').value='';$('#request-error').hidden=true;route('/chat/'+state.activeChat);inputChanged();$('#question').focus();
  }catch(e){showError(e);}finally{setBusy(false);}
}
function sourceLocation(s) {
  if (s.page_kind==='html_snapshot' || s.page_pdf===null) return `HTML 快照 · 字元位置 ${s.char_offset ?? '未標示'}`;
  return `PDF 第 ${s.page_pdf} 頁${s.page_end && s.page_end!==s.page_pdf ? `–${s.page_end} 頁` : ''}`;
}
function sourceDate(s) {
  const d=s.document_date;
  return d && d!=='unknown' ? d : '未標示';
}
function sourceButton(s,index) {
  const b=make('button','citation'); b.type='button';
  const content=make('span',''); content.append(make('span','cite-title',s.title), make('span','cite-meta',`${s.source_id} · ${s.section || '原文片段'} · ${sourceLocation(s)}`));
  const quote=Array.from(s.quote_anchor || ''); content.append(make('span','cite-quote','「'+quote.slice(0,80).join('')+(quote.length>80?'…':'')+'」'));
  b.append(make('span','cite-index',String(index+1)),content,make('span','cite-arrow','↗'));
  b.addEventListener('click',()=>openSource(s)); return b;
}
function conditionChips(result) {
  const row=make('div','condition-row');
  Object.entries(result.conditions || {}).forEach(([k,v])=>row.append(make('span','condition-chip',`${slotLabels[k]}：${typeof v==='object' ? JSON.stringify(v) : v}`)));
  result.clarify_skipped.forEach(s=>row.append(make('span','condition-chip',`${slotLabels[s]}：已略過`)));
  row.append(make('span','condition-chip','文件適用年度：尚未核實'));
  return row;
}
function clarification(result) {
  const qs=result.clarify_questions || []; if (!qs.length || state.current?.question_id!==result.question_id) return null;
  const bound={conversation_id:state.activeChat,question_id:result.question_id,query_text:result.query_text,conditions:structuredClone(result.conditions || {}),skipped:[...(result.clarify_skipped || [])]};
  const box=make('form','clarify-box'); box.append(make('h3','','補充必要條件'),make('p','','只問會影響這次查詢的條件；不確定可以略過。'));
  const answers={};
  qs.forEach(q=>{
    const group=make('div','clarify-field'), label=make('label','',q.question); group.append(label);
    if(q.slot==='degree_level'){
      const choices=make('div','choice-row');
      [['碩士','碩士班'],['博士','博士班'],['__skip','不確定，略過']].forEach(([value,txt])=>{
        const b=make('button','choice',txt); b.type='button'; b.setAttribute('aria-pressed','false');
        b.addEventListener('click',()=>{answers[q.slot]=value; choices.querySelectorAll('button').forEach(x=>{x.classList.toggle('selected',x===b);x.setAttribute('aria-pressed',String(x===b));});}); choices.append(b);
      }); group.append(choices);
    }else{
      const input=make('input',''); input.id=`clarify-${q.slot}`; label.htmlFor=input.id;
      input.placeholder=q.slot==='dept'?'輸入系所名稱，例如：工科系（目前系所樣本）':q.slot==='target_academic_term'?'目標學年度與學期，例如：115-2':'入學學年度，例如：115';
      input.maxLength=q.slot==='dept'?80:q.slot==='target_academic_term'?5:3; input.addEventListener('input',()=>{answers[q.slot]=input.value; skip.classList.remove('selected');skip.setAttribute('aria-pressed','false');});
      const skip=make('button','choice','不確定，略過'); skip.type='button'; skip.setAttribute('aria-pressed','false');
      skip.addEventListener('click',()=>{answers[q.slot]='__skip';input.value='';skip.classList.add('selected');skip.setAttribute('aria-pressed','true');});
      group.append(input,skip);
    }
    const why={degree_level:'候選文件有學位別條件，可補充後縮小核對範圍。',dept:'系所要求可能不同；本批系所來源僅有工科系樣本。',enrollment_year:'入學學年度可作查核線索，不能單憑它確認適用版本。',target_academic_term:'請區分入學年度與這次要辦理的學年度／學期。'};group.append(make('div','why',why[q.slot] || '這項條件有助於核對本次文件。'));box.append(group);
  });
  const localError=make('p','notice'); localError.setAttribute('role','alert');
  const actions=make('div','clarify-actions'),go=make('button','primary-action','補充後重新查詢'),skipAll=make('button','secondary-action','全部略過，查看原文');go.type='submit';skipAll.type='button';actions.append(go,skipAll);box.append(actions,localError);
  async function submitAnswers(allSkip=false){
    if(state.busy || state.activeChat!==bound.conversation_id || state.current?.question_id!==bound.question_id){localError.textContent='這則補問已失效，請從目前查詢繼續。';return;}
    const cond=structuredClone(bound.conditions), skips=new Set(bound.skipped);
    for(const q of qs){
      const v=allSkip?'__skip':answers[q.slot];
      if(v==='__skip'){delete cond[q.slot];skips.add(q.slot);}
      else if(v!==undefined && String(v).trim()) {
        if(q.slot==='target_academic_term'){const m=String(v).trim().match(/^([1-9][0-9]{0,2})(?:[-－]([12]))?$/);if(!m){localError.textContent='目標學期請填例如115-2，或只填明確的學年度115。';return;}cond[q.slot]={year:Number(m[1]),semester:m[2]?Number(m[2]):null};}
        else cond[q.slot]=q.slot==='enrollment_year'?Number(v):String(v).trim();skips.delete(q.slot);}
      else {localError.textContent='請填答每個條件，或選擇不確定／略過。';return;}
    }
    await query(bound.query_text,{conditions:cond,clarify_skipped:[...skips],bound_result_id:bound.question_id});
  }
  box.addEventListener('submit',e=>{e.preventDefault();submitAnswers();});skipAll.addEventListener('click',()=>submitAnswers(true));return box;
}
function evidenceRefs(evidence,result) {
  const refs=make('span','evidence-refs'),seen=new Set();
  for(const e of evidence || []){
    if(seen.has(e.chunk_id))continue;seen.add(e.chunk_id);
    const index=result.sources.findIndex(s=>s.chunk_id===e.chunk_id);if(index<0)continue;
    const b=make('button','inline-cite',`[${index+1}]`);b.type='button';
    b.title=`核對來源 ${result.sources[index].source_id} 的原文`;b.setAttribute('aria-label',b.title);
    b.addEventListener('click',()=>openSource(result.sources[index]));refs.append(b);
  }
  return refs;
}
function flowItem(claim,result,tag='li') {
  const item=make(tag,'flow-item'),title=make('div','flow-item-title');
  title.append(make('strong','',claim.title),evidenceRefs(claim.evidence,result));
  item.append(title,make('p','',claim.text));return item;
}
function importantExcerpt(result,flow){
  const primary=(flow.summary || {}).evidence || [];
  const extra=[...(flow.local_sequence || []),...(flow.items || [])].flatMap(c=>c.evidence || []);
  const candidates=[...primary,...extra].filter(e=>typeof e.quote==='string' && result.sources.some(s=>s.chunk_id===e.chunk_id));
  // Preserve a complete supported span. Long excerpts remain behind the source link.
  const selected=candidates.find(e=>Array.from(e.quote).length<=180);
  if(!selected)return null;
  const box=make('section','important-excerpt');
  const title=make('div','excerpt-title','文件重點');title.append(evidenceRefs([selected],result));
  box.append(title,make('blockquote','',selected.quote));return box;
}
function renderTurn(result) {
  const thread=make('section','chat-turn');thread.dataset.questionId=result.question_id;
  thread.append(make('div','user-message',result.query_text));
  if(result.response_kind && !['normal','in_scope_no_evidence'].includes(result.response_kind)){
    const answer=make('article','answer friendly-answer');answer.append(make('h2','',result.title),make('p','summary',result.message));
    const choices=make('div','suggestion-row');for(const suggestion of result.suggestions || []){const b=make('button','suggestion',suggestion);b.type='button';b.addEventListener('click',()=>{$('#question').value=suggestion;inputChanged();$('#question').focus();});choices.append(b);}answer.append(choices);thread.append(answer);return thread;
  }
  const head=make('div','answer-head');head.append(make('span','answer-avatar','畢'),make('span','','你要畢業了嗎'),make('span','badge',statusLabels[result.decision]));thread.append(head);
  const flow=result.flow || {},answer=make('article','answer');
  if(result.context?.note)answer.append(make('p','context-note',result.context.note));
  if(!result.sources.length)answer.append(make('h2','','還需要一點資料才能確認'));
  const summary=make('p','summary',(flow.summary || {}).text || result.summary);summary.append(evidenceRefs((flow.summary || {}).evidence,result));answer.append(summary);
  if(result.reason_codes.includes('slot_condition_conflict'))answer.append(make('div','status-banner','你提供的條件彼此不一致，請先更正；以下僅整理文件內容。'));
  const clarify=clarification(result);
  if(Object.keys(result.conditions || {}).length || result.clarify_skipped.length)answer.append(conditionChips(result));
  if((flow.local_sequence || []).length){
    answer.append(make('h3','flow-heading','可以這樣準備'),make('p','flow-scope','以下順序來自這份碩士學位考試申請文件，個人適用版本仍須確認。'));
    const list=make('ol','flow-sequence');flow.local_sequence.forEach(c=>list.append(flowItem(c,result)));answer.append(list);
  }else if((flow.items || []).length){
    const heading={copies:'依收件單位準備',format:'辦理方式與條件分支',delay:'分版本與繳件階段看',exam:'文件已明示的申請項目'}[flow.kind] || '文件明示的辦理項目';
    answer.append(make('h3','flow-heading',heading));const list=make('ul','flow-items');flow.items.forEach(c=>list.append(flowItem(c,result)));answer.append(list);
  }
  if(clarify){if(result.sources.length){const details=make('details','personal-conditions');details.append(make('summary','','補充個人條件，重新核對適用性'),clarify);answer.append(details);}else answer.append(clarify);}
  const excerpt=importantExcerpt(result,flow);if(excerpt)answer.append(excerpt);
  const notes=make('div','flow-limits');
  if(flow.limitation)notes.append(make('p','',flow.limitation));
  for(const claim of flow.uncertainties || []){const p=make('p','',claim.text);p.append(evidenceRefs(claim.evidence,result));notes.append(p);}
  if(notes.children.length)answer.append(notes);
  if(result.sources.length){
    const details=make('details','source-disclosure'),label=make('summary','',`查看完整來源與原文（${result.sources.length}）`);const toggleText=make('span','','展開查看');label.append(toggleText);details.addEventListener('toggle',()=>{toggleText.textContent=details.open?'收合原文':'展開查看';});details.append(label);
    const list=make('div','citation-list');result.sources.forEach((src,i)=>list.append(sourceButton(src,i)));details.append(list);answer.append(details);
  }
  const scope=make('button','scope-link','查看服務範圍與限制');scope.type='button';scope.addEventListener('click',()=>showScope());answer.append(scope);thread.append(answer);
  return thread;
}
function renderHistory(autoScroll=false){
  const el=$('#conversation'),top=el.scrollTop;const thread=$('#thread');thread.replaceChildren();
  if(!state.turns.length){showView('welcome');$('#source-count').textContent='0';return;}
  showView('thread');for(const turn of state.turns)thread.append(renderTurn(turn));
  $('#source-count').textContent=String(state.current?.sources?.length || 0);
  if(autoScroll)scrollToBottom();else el.scrollTop=top;
}
function renderResult(){renderHistory(false);}
function renderLoading(queryText){
  renderHistory(false);showView('thread');const thread=$('#thread');const pending=make('section','pending-turn');pending.append(make('div','user-message',queryText));thread.append(pending);
  const l=make('div','loading-box'),txt=make('p','loading-text');txt.append(make('span','loading-dot'),make('span','','正在檢索已收錄的文件…'));l.append(txt,make('div','loading-line'),make('div','loading-line short'));pending.append(l);
  const cancel=make('button','secondary-action','停止等待');cancel.type='button';cancel.hidden=true;
  cancel.addEventListener('click',()=>state.controller?.abort('user_cancelled'));
  const note=make('p','notice','停止等待會中斷頁面等候；本機查詢可能仍在完成。');note.hidden=true;l.append(cancel,note);
  return setTimeout(()=>{const e=l.querySelector('.loading-text span:last-child');if(e){e.textContent='仍在查詢本機文件，請稍候…';cancel.hidden=false;note.hidden=false;}},8000);
}
async function query(text,options={}){
  if(state.busy || !state.activeChat)return;const clean=text.trim();if(!clean || Array.from(text).length>200)return;
  if(options.bound_result_id && state.current?.question_id!==options.bound_result_id){showError({code:'clarification_expired',message:'這則補問已失效，請從目前聊天最後一則繼續。'});return;}
  const chatId=state.activeChat,atBottom=nearBottom();
  const pending={query_text:text,conditions:structuredClone(options.conditions || {}),clarify_skipped:[...(options.clarify_skipped || [])],bound_result_id:options.bound_result_id || null};
  state.pending=pending;$('#request-error').hidden=true;setBusy(true);state.controller=new AbortController();const controller=state.controller;
  const timer=renderLoading(text);if(atBottom)scrollToBottom();const deadline=setTimeout(()=>controller.abort('request_timeout'),40000);
  try{
    const body={conversation_id:chatId,query_text:text,conditions:pending.conditions,clarify_skipped:pending.clarify_skipped};if(pending.bound_result_id)body.parent_question_id=pending.bound_result_id;
    const r=await api('/api/query',body,controller.signal);
    if(controller.signal.aborted || state.activeChat!==chatId)throw {code:'query_cancelled',message:'已停止等待；這則回覆未寫入其他聊天。'};
    // Only a response actually received by this page is committed to memory.
    document.querySelectorAll('.pending-turn button').forEach(b=>b.hidden=true);
    pending.commit_started=true;const ack=await api('/api/conversation/commit',{conversation_id:chatId,question_id:r.question_id,commit_token:r.commit_token},controller.signal);
    if(state.activeChat!==chatId)return;
    useSnapshot(ack.conversation,false);updateChats(ack.conversations);state.drafts[chatId]='';$('#question').value='';inputChanged();route('/chat/'+chatId);
    if(atBottom)revealLatestAnswer();
  }catch(e){
    renderHistory(false);$('#question').value=text;inputChanged();
    if(pending.commit_started)e={code:'commit_status_unknown',message:'回覆保留狀態尚未確認，請重新整理這個聊天查看；原問題已保留。'};
    else if(controller.signal.aborted)e={code:controller.signal.reason==='request_timeout'?'request_timeout':'query_cancelled',message:controller.signal.reason==='request_timeout'?'等待已逾時，問題已保留。未確認收到的回覆不會加入聊天記憶。':'已停止等待，問題已保留。未確認收到的回覆不會加入聊天記憶。'};
    showError(e);
  }finally{
    clearTimeout(timer);clearTimeout(deadline);state.pending=null;state.controller=null;setBusy(false);
    if(state.activeChat===chatId && (document.activeElement===document.body || document.activeElement===document.documentElement))$('#question').focus({preventScroll:true});
  }
}
async function openSource(s,push=true){
  const sourceSeq=++state.sourceSeq,chatId=state.activeChat;
  setEditorialStage('source');
  $('#source-panel').hidden=false;$('.app-shell').classList.add('sources-open');$('#sources-toggle').setAttribute('aria-expanded','true');
  const area=$('#source-content');area.replaceChildren(make('p','notice','正在讀取權威原文…'));
  try{
    const result=await api(`/api/source/${encodeURIComponent(s.source_id)}?chunk_id=${encodeURIComponent(s.chunk_id)}&conversation_id=${encodeURIComponent(state.activeChat)}`);const src=result.source;if(sourceSeq!==state.sourceSeq || chatId!==state.activeChat)return;state.source=src;
    area.replaceChildren(make('span','source-id',`${src.source_id} · ${src.source_type || '文件'}`),make('h3','source-card-title',src.title),make('span','badge','適用性尚未核實'));
    const meta=make('div','source-meta');
    [["權責單位",src.unit || '未標示'],["文件日期",sourceDate(src)],["日期說明",src.date_kind || '未標示'],["取得日期",src.observed_at || '未標示'],["定位",sourceLocation(src)],["適用範圍",src.scope.join('、') || '尚未核實']].forEach(([label,value])=>{const p=make('p','');p.append(make('strong','',label+'　'),document.createTextNode(value));meta.append(p);});area.append(meta);
    area.append(make('p','source-label','原文片段 · 保留來源文字'),make('blockquote','source-text',src.text));
    if(src.url){const a=make('a','source-link','開啟官方原文 ↗');a.href=src.url;a.target='_blank';a.rel='noopener noreferrer';area.append(a,make('p','source-footnote','外部原文將在新分頁開啟。來源官方性與文件對個人的適用性，是不同的判斷。'));}
    if(src.limit_note)area.append(make('p','source-footnote',src.limit_note));
    if(push)route(`/chat/${state.activeChat}/source/${src.source_id}?chunk_id=${encodeURIComponent(src.chunk_id)}`);
    $('#close-sources').focus();
  }catch(e){area.replaceChildren(make('p','notice',errorMessage(e)),make('span','error-code',e.code || 'source_failed'));}
}
function closeSources(back=true){
  setEditorialStage(state.turns.length?'answer':'ask');
  state.sourceSeq++;
  $('#source-panel').hidden=true;$('.app-shell').classList.remove('sources-open');$('#sources-toggle').setAttribute('aria-expanded','false');state.source=null;
  if(back && location.pathname.includes('/source/'))route('/chat/'+state.activeChat);
  $('#sources-toggle').focus();
}
async function showScope(push=true){
  showView('scope-view');closeSources(false);const area=$('#scope-view');area.replaceChildren(make('h1','','服務範圍與限制'));
  if(push)route('/scope');
  try{
    const r=state.scope || await api('/api/scope');state.scope=r;area.append(make('p','',r.coverage));
    const section=make('section','scope-section');section.append(make('h2','','使用前，先知道這些限制'));const ul=make('ul','');r.limitations.forEach(x=>ul.append(make('li','',x)));section.append(ul);area.append(section);
    const sourceSection=make('section','scope-section');sourceSection.append(make('h2','',`目前收錄 ${r.source_count} 份來源 · ${r.chunk_count} 個片段`));const sources=make('div','scope-sources');r.source_titles.forEach(s=>{const p=make('p','');p.append(make('b','',s.source_id),document.createTextNode(s.title));sources.append(p);});sourceSection.append(sources);area.append(sourceSection,make('p','notice',`資料取得截止：${r.data_observed_at}。文件取得日期不代表現行適用日期。`));
    const back=make('button','secondary-action',state.current?'回到目前查詢':'開始提問');back.addEventListener('click',()=>{if(state.current){renderResult(state.current);route('/chat/'+state.activeChat);}else newQuery();});area.append(back);
  }catch(e){area.append(make('p','notice',errorMessage(e)));}
}
async function handleRoute(){
  if(!state.ready)return;
  const path=location.pathname,routeSearch=location.search;if(path==='/scope')return showScope(false);
  const match=path.match(/^\/chat\/(chat-[a-f0-9]+)(?:\/source\/([^/?]+))?/);
  if(match){
    if(match[1]!==state.activeChat)await switchChat(match[1]);
    if(match[2] && state.activeChat===match[1]){
      route(path+routeSearch,true);
      return openSource({source_id:match[2],chunk_id:new URLSearchParams(routeSearch).get('chunk_id') || ''},false);
    }
    closeSources(false);return renderHistory(false);
  }
  if(path==='/' || path==='/app'){
    if(state.activeChat){renderHistory(false);route('/chat/'+state.activeChat,true);return;}
    const recent=state.chats.filter(c=>!c.expired).at(-1);
    return recent?switchChat(recent.conversation_id):newQuery();
  }
  showView('route-error');const e=$('#route-error');e.replaceChildren(make('h1','','這個查詢網址屬於舊版'),make('p','','請從聊天列表選擇本次保留的對話，或新增聊天。'));
}
async function initialize(){
  try{const r=await api('/api/conversations');updateChats(r.conversations);state.ready=true;await handleRoute();}catch(e){showError(e);}
}
$('#question').addEventListener('input',inputChanged);
$('#question').addEventListener('keydown',e=>{if(e.key==='Enter' && !e.shiftKey && !e.isComposing){e.preventDefault();if(!$('#send').disabled)query($('#question').value);}});
$('#query-form').addEventListener('submit',e=>{e.preventDefault();query($('#question').value);});
$('#new-query').addEventListener('click',newQuery);$('#scope-button').addEventListener('click',()=>showScope());$('#close-sources').addEventListener('click',()=>closeSources());
$('#sources-toggle').addEventListener('click',()=>{if(!$('#source-panel').hidden)return closeSources();if(state.current?.sources.length)return openSource(state.current.sources[0]);$('#source-panel').hidden=false;$('.app-shell').classList.add('sources-open');$('#sources-toggle').setAttribute('aria-expanded','true');});
document.querySelectorAll('[data-example]').forEach(b=>b.addEventListener('click',()=>{$('#question').value=b.dataset.example;inputChanged();$('#question').focus();}));
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();newQuery();}if(e.key==='Escape'&&!$('#source-panel').hidden)closeSources();});
window.addEventListener('popstate',handleRoute);
api('/api/scope').then(r=>{state.scope=r;$('#data-date').textContent=`資料取得截止 ${r.data_observed_at}`;}).catch(()=>{});
initialize();inputChanged();

// Editorial stages reflect displayed UI, never claim unobserved verification progress.
function setEditorialStage(stage){
  document.querySelectorAll('[data-stage]').forEach(item=>{if(item.dataset.stage===stage)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');});
}
const folio=document.querySelector('.folio-preview');
const folioCopy={copies:'一本論文，送到對的地方。',cover:'讓封面，符合那一份規定。',print:'每一頁，都有它的分寸。',delay:'需要多一點時間，也先問清楚。'};
document.querySelectorAll('[data-topic]').forEach(button=>{
  const reveal=()=>{if(!folio)return;folio.dataset.topic=button.dataset.topic;folio.querySelector('.folio-caption').textContent=folioCopy[button.dataset.topic];};
  button.addEventListener('pointerenter',reveal);button.addEventListener('focus',reveal);
  const reset=()=>{if(!folio)return;delete folio.dataset.topic;folio.querySelector('.folio-caption').textContent='把規定翻開，把疑問放下。';};
  button.addEventListener('pointerleave',()=>{if(document.activeElement!==button)reset();});
  button.addEventListener('blur',()=>{if(!button.matches(':hover'))reset();});
});
