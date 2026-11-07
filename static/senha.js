// Botão de olho em todo campo de senha: mostra/esconde o que foi digitado.
(function(){
  const EYE='<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF='<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.9 17.9A10.4 10.4 0 0 1 12 19C5.5 19 2 12 2 12a18.6 18.6 0 0 1 5.1-5.9M9.9 5.2A9.6 9.6 0 0 1 12 5c6.5 0 10 7 10 7a18.8 18.8 0 0 1-2.2 3.2M14.1 14.1a3 3 0 1 1-4.2-4.2"/><path d="M2 2l20 20"/></svg>';
  const css=document.createElement('style');
  css.textContent='.pw-wrap{position:relative;display:block}.pw-wrap>input{padding-right:46px!important}'
    +'.pw-eye{position:absolute;right:6px;top:50%;transform:translateY(-50%);width:36px;height:36px;display:flex;align-items:center;justify-content:center;'
    +'border:0!important;background:transparent!important;box-shadow:none!important;padding:0!important;margin:0!important;color:#8a8a92;border-radius:8px;cursor:pointer}'
    +'.pw-eye:hover,.pw-eye[aria-pressed="true"]{color:#9b59b6}.pw-eye:focus-visible{outline:2px solid #9b59b6}';
  document.head.appendChild(css);
  function add(input){
    if(input.dataset.pwEye) return; input.dataset.pwEye='1';
    const st=getComputedStyle(input), wrap=document.createElement('span');
    wrap.className='pw-wrap';
    // A margem passa pro invólucro, senão o olho não fica no meio do campo.
    wrap.style.margin=st.margin; input.style.margin='0';
    input.parentNode.insertBefore(wrap,input); wrap.appendChild(input);
    const btn=document.createElement('button');
    btn.type='button'; btn.className='pw-eye'; btn.innerHTML=EYE;
    btn.setAttribute('aria-label','Mostrar senha'); btn.setAttribute('aria-pressed','false');
    btn.addEventListener('mousedown',e=>e.preventDefault()); // não tira o foco do campo
    btn.addEventListener('click',e=>{
      e.preventDefault(); e.stopPropagation();
      const show=input.type==='password';
      input.type=show?'text':'password';
      btn.innerHTML=show?EYE_OFF:EYE;
      btn.setAttribute('aria-label',show?'Esconder senha':'Mostrar senha');
      btn.setAttribute('aria-pressed',String(show));
    });
    wrap.appendChild(btn);
  }
  // Ao fechar uma janela, as senhas voltam a ficar escondidas.
  document.addEventListener('close',e=>{
    e.target.querySelectorAll&&e.target.querySelectorAll('.pw-eye[aria-pressed="true"]').forEach(b=>b.click());
  },true);
  function run(){ document.querySelectorAll('input[type=password]').forEach(add); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',run); else run();
})();
