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
  try {
    const result=await work(); toast(result.message);
    if(result.url) {
      const target = new URL(result.url, location.href);
      const replace = target.origin === location.origin && target.pathname === location.pathname;
      rememberSettingsContext(); workspaceNavigation.remember(target.href, replace);
      if (replace) window.location.replace(target.href); else window.location.assign(target.href);
    }
  }
  catch(error) {toast(error.message);}
  finally {button.disabled=false; button.textContent=old;}
}
document.querySelectorAll('[data-compile]').forEach(button=>button.addEventListener('click',()=>action(button,()=>post('/api/compile',{category:button.dataset.categoryFrom ? document.getElementById(button.dataset.categoryFrom).value : ''}),'LLM 正在综合证据…')));
document.querySelectorAll('[data-source-compile]').forEach(button=>button.addEventListener('click',()=>action(button,()=>post('/api/compile',{source_id:button.dataset.sourceCompile}),'LLM 正在编写…')));
for(const name of ['reject']) document.querySelectorAll(`[data-${name}]`).forEach(button=>button.addEventListener('click',()=>action(button,()=>post(`/api/drafts/${button.dataset[name]}/${name}`))));
document.querySelectorAll('[data-source-category]').forEach(select=>select.addEventListener('change',async()=>{select.disabled=true;try{const result=await post(`/api/sources/${select.dataset.sourceCategory}/category`,{category:select.value});toast(result.message);window.location.reload();}catch(error){toast(error.message);}finally{select.disabled=false;}}));
document.querySelectorAll('[data-rewrite]').forEach(button => button.addEventListener('click', () => action(button, () => post(`/api/pages/${encodeURIComponent(button.dataset.rewrite)}/rewrite`), '正在改写表达…')));
document.querySelectorAll('[data-approve-form]').forEach(form => form.addEventListener('submit', event => {
  event.preventDefault();
  const payload = {fact_invariance_confirmed: form.elements.fact_invariance_confirmed?.checked === true,
    fact_review_note: form.elements.fact_review_note?.value || ''};
  action(form.querySelector('button[type="submit"]'), () => post(`/api/drafts/${form.dataset.approveForm}/approve`, payload));
}));
const urlForm=document.querySelector('#import-url-form');
if(urlForm) urlForm.addEventListener('submit',event=>{event.preventDefault();action(urlForm.querySelector('button[type="submit"], button:not([type])'),()=>post('/api/import-url',Object.fromEntries(new FormData(urlForm))),'正在获取原文…');});
const fileForm=document.querySelector('#import-file-form');
if(fileForm) fileForm.addEventListener('submit',event=>{event.preventDefault();const file=fileForm.elements.file.files[0];if(!file)return;if(file.size>15*1024*1024){toast('文件超过 15 MB');return;}action(fileForm.querySelector('button[type="submit"], button:not([type])'),async()=>{const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file);});return post('/api/import-file',{file_name:file.name,content_base64:content,title:fileForm.elements.title.value,category:fileForm.elements.category.value,kind:fileForm.elements.kind.value});},'正在归档原件…');});

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

// Keep navigation inside this workspace and retain the preceding page on reload.
const workspaceNavigation = (() => {
  const key = 'wiki-pending-navigation';
  const internal = value => {
    try { const url = new URL(value, location.href); return url.origin === location.origin ? url : null; }
    catch (error) { return null; }
  };
  const read = key => { try { return JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (error) { return null; } };
  const write = (key, value) => { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (error) { /* Storage is optional. */ } };
  const pending = read(key);
  const existing = history.state?.wikiNavigation;
  let previous = existing?.previous || '';
  if (pending && Date.now() - pending.time < 60000 && internal(pending.to)?.href === location.href) {
    previous = pending.previous;
    try { sessionStorage.removeItem(key); } catch (error) { /* Storage is optional. */ }
  } else if (!existing) {
    const referrer = internal(document.referrer);
    if (referrer && referrer.pathname !== location.pathname) previous = referrer.href;
  }
  if (!internal(previous) || previous === location.href) previous = '';
  const state = {...(history.state || {}), wikiNavigation: {previous}};
  history.replaceState(state, '', location.href);
  const remember = (url, replace = false) => {
    const target = internal(url);
    if (target) write(key, {to:target.href, previous:replace ? previous : location.href, time:Date.now()});
  };
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return;
    const target = internal(link.href);
    if (target && target.pathname !== location.pathname) remember(target.href);
    else if (target && target.search === location.search && target.hash && !link.classList.contains('skip-link')) {
      // Section links keep the page's return destination instead of adding a
      // string of fragment-only steps to the Back button.
      event.preventDefault(); history.replaceState(history.state, '', target.href); revealHashTarget();
    }
  });
  const path = document.querySelector('.topbar-path');
  if (path && location.pathname !== '/') {
    const fallback = location.pathname.startsWith('/source/') ? '/sources' : location.pathname.startsWith('/page/') ? '/' : location.pathname.startsWith('/review') ? '/sources' : '/';
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'icon-button workspace-back'; button.setAttribute('aria-label', '返回上一页'); button.title = '返回上一页';
    button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6M8 12h12"/></svg>';
    button.addEventListener('click', () => {
      if (previous && history.length > 1) history.back();
      else location.assign(fallback);
    });
    path.prepend(button);
  }
  return {remember, read, write};
})();
window.wikiRememberNavigation = (url, replace = false) => workspaceNavigation.remember(url, replace);

const settingsContextKey = 'wiki-settings-context';
const settingsDetailKey = details => {
  const form = details.querySelector('[data-settings-form]') || details.closest('[data-settings-form]');
  if (!form) return '';
  const identity = `${form.dataset.settingsForm}:${form.elements.id?.value || 'new'}`;
  return details.classList.contains('settings-advanced') ? `${identity}:advanced` : identity;
};
function rememberSettingsContext() {
  if (!settingsWorkspace) return;
  workspaceNavigation.write(settingsContextKey, {time:Date.now(), scrollY:window.scrollY,
    open:Array.from(settingsWorkspace.querySelectorAll('details[open]')).map(settingsDetailKey).filter(Boolean)});
}
if (settingsWorkspace) {
  const context = workspaceNavigation.read(settingsContextKey);
  if (context && Date.now() - context.time < 10 * 60 * 1000) {
    const opened = new Set(context.open);
    for (const details of settingsWorkspace.querySelectorAll('details')) if (opened.has(settingsDetailKey(details))) details.open = true;
    requestAnimationFrame(() => window.scrollTo(0, context.scrollY || 0));
    try { sessionStorage.removeItem(settingsContextKey); } catch (error) { /* Storage is optional. */ }
  }
}

// A hash may point into a collapsed provenance record or review form.
function revealHashTarget() {
  if (!location.hash) return;
  let id;
  try { id = decodeURIComponent(location.hash.slice(1)); } catch (error) { return; }
  const target = document.getElementById(id);
  if (!target) return;
  for (let parent = target.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
  requestAnimationFrame(() => target.scrollIntoView({block:'start'}));
}
window.addEventListener('hashchange', revealHashTarget);
window.addEventListener('pageshow', event => {
  if (!event.persisted && location.hash) requestAnimationFrame(revealHashTarget);
});
revealHashTarget();

// Native selects remain the form's data source and the no-JavaScript fallback.
// The enhanced listbox is bounded by the viewport, rather than the OS menu width.
(() => {
  let opened = null;
  let sequence = 0;
  const enhance = select => {
    if (select.multiple || select.size > 1 || select.dataset.enhancedSelect) return;
    select.dataset.enhancedSelect = 'true';
    const wrapper = document.createElement('span'); wrapper.className = 'select-control';
    select.before(wrapper); wrapper.append(select);
    const trigger = document.createElement('button');
    trigger.type = 'button'; trigger.className = 'select-trigger'; trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false');
    const label = select.getAttribute('aria-label') || select.closest('label')?.childNodes[0]?.textContent?.trim() || ({category:'主题分类',status:'原文状态',kind:'资料类型'}[select.name]) || '选择';
    const value = document.createElement('span'); value.className = 'select-value';
    const arrow = document.createElement('span'); arrow.className = 'select-arrow'; arrow.setAttribute('aria-hidden', 'true'); arrow.textContent = '⌄';
    trigger.append(value, arrow); wrapper.append(trigger);
    const menu = document.createElement('div'); menu.className = 'select-menu'; menu.hidden = true;
    menu.id = `wiki-select-${++sequence}`; menu.setAttribute('role', 'listbox'); menu.setAttribute('aria-label', label);
    trigger.setAttribute('aria-controls', menu.id); document.body.append(menu);
    let active = select.selectedIndex;
    let options = [];
    let query = ''; let typedAt = 0;
    const update = () => {
      value.textContent = select.selectedOptions[0]?.textContent || '请选择';
      trigger.setAttribute('aria-label', `${label}：${value.textContent}`);
      trigger.disabled = select.disabled;
      if (select.disabled && opened?.trigger === trigger) close();
      for (const option of options) option.element.setAttribute('aria-selected', String(option.index === select.selectedIndex));
    };
    const position = () => {
      const rect = trigger.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) {close(); return;}
      const width = Math.min(Math.max(rect.width, 180), window.innerWidth - 24);
      const below = window.innerHeight - rect.bottom - 12;
      const above = rect.top - 12;
      const upwards = below < Math.min(menu.scrollHeight, 240) && above > below;
      const height = Math.max(60, Math.min(320, upwards ? above - 6 : below - 6));
      menu.style.width = `${width}px`; menu.style.maxHeight = `${height}px`;
      menu.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
      menu.style.top = `${Math.max(12, Math.min(upwards ? rect.top - Math.min(menu.scrollHeight, height) - 6 : rect.bottom + 6, window.innerHeight - Math.min(menu.scrollHeight, height) - 12))}px`;
    };
    const focusOption = index => {
      if (!options.length) return;
      active = Math.max(0, Math.min(index, options.length - 1));
      const option = options[active];
      options.forEach(item => item.element.classList.toggle('is-active', item.index === active));
      trigger.setAttribute('aria-activedescendant', option.element.id);
      option.element.scrollIntoView({block:'nearest'});
    };
    const close = () => {
      menu.hidden = true; trigger.setAttribute('aria-expanded','false'); trigger.removeAttribute('aria-activedescendant');
      if (opened?.trigger === trigger) opened = null;
    };
    const choose = index => {
      const option = select.options[index];
      if (!option || option.disabled || option.parentElement.disabled) return;
      select.selectedIndex = index; update(); close(); trigger.focus({preventScroll:true});
      select.dispatchEvent(new Event('input', {bubbles:true})); select.dispatchEvent(new Event('change', {bubbles:true}));
    };
    const open = () => {
      if (select.disabled) return;
      if (opened) opened.close();
      menu.replaceChildren(); options = Array.from(select.options).map((option,index) => {
        const element = document.createElement('div'); element.className = 'select-option';
        element.id = `${menu.id}-option-${index}`; element.setAttribute('role','option');
        element.textContent = option.textContent; element.setAttribute('aria-selected',String(index === select.selectedIndex));
        const disabled = option.disabled || option.parentElement.disabled;
        if (disabled) element.setAttribute('aria-disabled','true');
        element.addEventListener('click', () => choose(index));
        menu.append(element); return {index,element,disabled};
      });
      menu.hidden = false; opened = {trigger,menu,close,position}; trigger.setAttribute('aria-expanded','true');
      position(); focusOption(select.selectedIndex >= 0 ? select.selectedIndex : 0);
    };
    const step = direction => {
      let next = active + direction;
      while (next >= 0 && next < options.length && options[next].disabled) next += direction;
      if (next >= 0 && next < options.length) focusOption(next);
    };
    trigger.addEventListener('click', () => opened?.trigger === trigger ? close() : open());
    trigger.addEventListener('keydown', event => {
      if (['ArrowDown','ArrowUp','Home','End','Enter',' ','Escape'].includes(event.key)) {
        event.preventDefault();
        if (event.key === 'Escape') {close(); return;}
        if (event.key === 'Enter' || event.key === ' ') {if (opened?.trigger === trigger) choose(active); else open(); return;}
        const wasOpen = opened?.trigger === trigger;
        if (!wasOpen) open();
        if (event.key === 'Home') {focusOption(0); if (options[active]?.disabled) step(1);}
        else if (event.key === 'End') {focusOption(options.length - 1); if (options[active]?.disabled) step(-1);}
        else if (wasOpen) step(event.key === 'ArrowDown' ? 1 : -1);
      } else if (event.key === 'Tab') close();
      else if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault(); if (opened?.trigger !== trigger) open();
        query = Date.now() - typedAt > 800 ? event.key : query + event.key; typedAt = Date.now();
        const match = options.find(item => !item.disabled && item.element.textContent.trim().toLocaleLowerCase().startsWith(query.toLocaleLowerCase()));
        if (match) focusOption(match.index);
      }
    });
    select.addEventListener('change', update);
    select.addEventListener('invalid', event => {event.preventDefault(); trigger.focus(); open();});
    select.addEventListener('focus', () => trigger.focus());
    select.form?.addEventListener('reset', () => setTimeout(update, 0));
    new MutationObserver(update).observe(select, {attributes:true,childList:true,subtree:true,characterData:true});
    select.classList.add('select-native'); select.tabIndex = -1; select.setAttribute('aria-hidden','true'); update();
  };
  document.querySelectorAll('select').forEach(enhance);
  document.addEventListener('pointerdown', event => {if (opened && !opened.menu.contains(event.target) && !opened.trigger.contains(event.target)) opened.close();});
  document.addEventListener('keydown', event => {if (event.key === 'Escape') opened?.close();});
  window.addEventListener('resize', () => opened?.position());
  document.addEventListener('scroll', event => {if (opened && !opened.menu.contains(event.target)) opened.position();}, true);
  document.addEventListener('toggle', event => {if (opened && event.target.tagName === 'DETAILS' && !event.target.open && event.target.contains(opened.trigger)) opened.close();}, true);
})();
