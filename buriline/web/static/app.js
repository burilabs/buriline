async function ask(ev){
  ev.preventDefault();
  const form=ev.target, q=form.question.value.trim(); if(!q) return;
  const chat=document.getElementById('chat');
  chat.insertAdjacentHTML('beforeend',`<div class="msg q">${esc(q)}</div>`);
  const wait=document.createElement('div'); wait.className='msg a'; wait.textContent='Analysiere …'; chat.appendChild(wait);
  form.question.value=''; window.scrollTo(0,document.body.scrollHeight);
  try{
    const r=await fetch('/api/ask',{method:'POST',body:new FormData(form)});
    const j=await r.json();
    if(!j.ok){wait.textContent=j.error||'Fehler';return}
    wait.innerHTML=esc(j.answer)+`<div class="meta">Modell: ${esc(j.provider)}:${esc(j.model)}${j.note?' · '+esc(j.note):''} · Audit ${esc(j.audit_hash.slice(0,12))}</div>`;
  }catch(e){wait.textContent='Verbindung fehlgeschlagen: '+e}
  window.scrollTo(0,document.body.scrollHeight);
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function chip(el){const f=document.getElementById('askform'); f.question.value=el.textContent; f.question.focus();}
