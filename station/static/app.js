'use strict';
const themeToggle = document.querySelector('#theme-toggle');
const syncThemeLabel = () => {
  if (themeToggle) themeToggle.setAttribute('aria-label', document.documentElement.dataset.theme === 'dark' ? '切换浅色模式' : '切换深色模式');
};
syncThemeLabel();
themeToggle?.addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('wiki-theme', theme); } catch (error) { /* Optional preference storage. */ }
  syncThemeLabel();
});
const toast = message => { const el = document.querySelector('#toast'); el.textContent = message; el.classList.add('show'); clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => el.classList.remove('show'), 6500); };
async function post(url, data = {}) {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '操作失败，请重试');
  return result;
}
async function action(button, work, pending='处理中…') {
  const old = button.textContent; button.disabled=true; button.textContent=pending;
  try { const result=await work(); toast(result.message); if(result.url) window.location.href=result.url; }
  catch(error) {toast(error.message);}
  finally {button.disabled=false; button.textContent=old;}
}
document.querySelectorAll('[data-compile]').forEach(button=>button.addEventListener('click',()=>action(button,()=>post('/api/compile',{category:button.dataset.categoryFrom ? document.getElementById(button.dataset.categoryFrom).value : ''}),'LLM 正在综合证据…')));
document.querySelectorAll('[data-source-compile]').forEach(button=>button.addEventListener('click',()=>action(button,()=>post('/api/compile',{source_id:button.dataset.sourceCompile}),'LLM 正在编写…')));
for(const name of ['approve','reject']) document.querySelectorAll(`[data-${name}]`).forEach(button=>button.addEventListener('click',()=>action(button,()=>post(`/api/drafts/${button.dataset[name]}/${name}`))));
document.querySelectorAll('[data-source-category]').forEach(select=>select.addEventListener('change',async()=>{select.disabled=true;try{const result=await post(`/api/sources/${select.dataset.sourceCategory}/category`,{category:select.value});toast(result.message);window.location.reload();}catch(error){toast(error.message);}finally{select.disabled=false;}}));
const urlForm=document.querySelector('#import-url-form');
if(urlForm) urlForm.addEventListener('submit',event=>{event.preventDefault();action(urlForm.querySelector('button'),()=>post('/api/import-url',Object.fromEntries(new FormData(urlForm))),'正在获取原文…');});
const fileForm=document.querySelector('#import-file-form');
if(fileForm) fileForm.addEventListener('submit',event=>{event.preventDefault();const file=fileForm.elements.file.files[0];if(!file)return;if(file.size>15*1024*1024){toast('文件超过 15 MB');return;}action(fileForm.querySelector('button'),async()=>{const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file);});return post('/api/import-file',{file_name:file.name,content_base64:content,title:fileForm.elements.title.value,category:fileForm.elements.category.value,kind:fileForm.elements.kind.value});},'正在归档原件…');});

document.querySelectorAll('[data-refresh]').forEach(button=>button.addEventListener('click',()=>action(button,()=>post(`/api/sources/${button.dataset.refresh}/refresh`),'正在重新获取原文…')));
document.querySelectorAll('[data-fetch-pdf]').forEach(button=>button.addEventListener('click',()=>action(button,()=>post(`/api/sources/${button.dataset.fetchPdf}/pdf`),'正在获取 PDF…')));

const settingsWorkspace = document.querySelector('[data-settings-version]');
if (settingsWorkspace) {
  const version = settingsWorkspace.dataset.settingsVersion;
  const keywords = value => String(value || '').split(/[,，\n\r]+/).map(word => word.trim()).filter(Boolean);
  document.querySelectorAll('[data-settings-form]').forEach(form => form.addEventListener('submit', event => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form));
    payload.version = version;
    payload.enabled = form.elements.enabled.type === 'hidden' || form.elements.enabled.checked;
    const kind = form.dataset.settingsForm;
    if (kind === 'feed') {
      for (const field of ['include_keywords', 'require_keywords', 'exclude_keywords']) payload[field] = keywords(payload[field]);
      payload.max_age_days = payload.max_age_days ? Number(payload.max_age_days) : null;
    }
    action(form.querySelector('button[type="submit"], button:not([type])'), () => post(`/api/settings/${kind === 'topic' ? 'topics' : 'feeds'}`, payload), '正在保存…');
  }));
  document.querySelectorAll('[data-settings-delete]').forEach(button => button.addEventListener('click', () => {
    const kind = button.dataset.settingsDelete;
    const name = button.dataset.itemName;
    const message = kind === 'topic' ? `删除主题“${name}”？有资料、知识或订阅引用时会保留。` : `删除订阅“${name}”？后续停止采集，已归档的资料和知识保留。`;
    if (!window.confirm(message)) return;
    action(button, () => post(`/api/settings/${kind === 'topic' ? 'topics' : 'feeds'}/${encodeURIComponent(button.dataset.itemId)}/delete`, {version}), '正在删除…');
  }));
}
