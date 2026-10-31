/* Tema claro/escuro. Carrega no <head>, antes da página aparecer, para não
   piscar branco. A escolha fica só neste navegador (localStorage). */
(function(){
  var key='rf_theme', saved=null;
  try{ saved=localStorage.getItem(key); }catch(e){}
  var theme=saved==='dark'||saved==='light'?saved:'light';
  document.documentElement.setAttribute('data-theme',theme);
  window.rfTheme={
    get:function(){ return document.documentElement.getAttribute('data-theme')||'light'; },
    set:function(t){
      document.documentElement.setAttribute('data-theme',t);
      try{ localStorage.setItem(key,t); }catch(e){}
      document.dispatchEvent(new CustomEvent('rf-theme',{detail:t}));
    },
    toggle:function(){ this.set(this.get()==='dark'?'light':'dark'); }
  };
})();
