// Runs before CSS so a saved dark theme never flashes a light background.
(() => {
  const key='agentai-theme';
  const system=window.matchMedia('(prefers-color-scheme: dark)');
  const valid=value=>value==='dark' || value==='light';
  let preference=null;
  try { const saved=localStorage.getItem(key); if (valid(saved)) preference=saved; } catch {}
  function apply() {
    const dark=(preference || (system.matches?'dark':'light'))==='dark';
    document.documentElement.dataset.theme=dark?'dark':'light';
    const button=document.getElementById('toggle-theme');
    if (button) { button.setAttribute('aria-pressed',String(dark)); button.title=dark?'Включить светлую тему':'Включить тёмную тему'; }
  }
  apply();
  document.addEventListener('DOMContentLoaded',()=>{
    apply();
    document.getElementById('toggle-theme').addEventListener('click',()=>{
      preference=document.documentElement.dataset.theme==='dark'?'light':'dark';
      try { localStorage.setItem(key,preference); } catch {}
      apply();
    });
  },{once:true});
  system.addEventListener('change',()=>{if (!preference) apply();});
  window.addEventListener('storage',event=>{ if (event.key===key || event.key===null) {preference=valid(event.newValue)?event.newValue:null;apply();} });
})();
