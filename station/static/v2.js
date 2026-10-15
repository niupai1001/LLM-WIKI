(() => {
  const library = document.querySelector('meta[name="library-id"]')?.content;
  const request = async (url, data) => {
    const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-Library-ID':library || 'legacy'}, body:JSON.stringify(data || {})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || '处理失败');
    return result;
  };
  const operate = (element, fn) => element?.addEventListener('click', async () => {
    element.disabled = true;
    try { const result = await fn(); const output = document.getElementById('library-result'); if(output) output.textContent=JSON.stringify(result,null,2); else location.reload(); }
    catch(error) { const output = document.getElementById('library-result'); if(output) output.textContent=error.message; else alert(error.message); }
    finally {element.disabled=false;}
  });
  document.querySelectorAll('[data-new-job]').forEach(el=>operate(el,()=>request('/api/jobs',{kind:el.dataset.newJob,payload:{embeddings:true}})));
  document.querySelectorAll('[data-job-action]').forEach(el=>operate(el,()=>request(`/api/jobs/${el.dataset.jobId}/${el.dataset.jobAction}`,el.dataset.targetSlug ? {target_slug:el.dataset.targetSlug} : {})));
  document.querySelectorAll('[data-library-action]').forEach(el=>operate(el,()=>request(`/api/libraries/${el.dataset.library}/${el.dataset.libraryAction}`)));
  operate(document.getElementById('create-candidate'),()=>request('/api/libraries',{library_id:'candidate-v2'}));
  operate(document.getElementById('compare-libraries'),async()=>{const r=await fetch('/api/libraries/compare?library_id=candidate-v2');const v=await r.json();if(!r.ok)throw new Error(v.detail);return v;});
  document.getElementById('query-form')?.addEventListener('submit',async event=>{
    event.preventDefault(); const button=event.target.querySelector('button');button.disabled=true;
    const output=document.getElementById('query-result');output.textContent='正在查找原文证据…';
    try{
      const result=await request('/api/query',{question:event.target.question.value});
      output.replaceChildren();const answer=document.createElement('div');answer.className='prose';if(result.answer_html)answer.innerHTML=result.answer_html;else answer.textContent=result.reason || '没有足够证据回答。';output.append(answer);
      for(const citation of result.citations || []){const p=document.createElement('p'),a=document.createElement('a');a.href=citation.url;a.textContent=`${citation.id} · ${citation.locator}`;p.append(a);const q=document.createElement('blockquote');q.textContent=citation.quote;output.append(p,q);}
      for(const warning of result.warnings || []){const p=document.createElement('p');p.className='muted';p.textContent=warning;output.append(p);}
    }catch(error){output.textContent=error.message;}finally{button.disabled=false;}
  });
  if (document.querySelector('[data-job-action="cancel"]')) setTimeout(()=>location.reload(),5000);
})();
