/* Mesmo formato do hash_password do app.py:
   pbkdf2_sha256$600000$<sal hex>$<hash hex>, com o sal usado como texto (UTF-8). */
(function(){
  const $=s=>document.querySelector(s), msg=(t,ok)=>{$('#msg').textContent=t;$('#msg').className='msg '+(ok?'ok':'err');};
  const hex=buf=>[...new Uint8Array(buf)].map(b=>b.toString(16).padStart(2,'0')).join('');
  async function hashPassword(password){
    const salt=hex(crypto.getRandomValues(new Uint8Array(16))), enc=new TextEncoder();
    const key=await crypto.subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveBits']);
    const bits=await crypto.subtle.deriveBits({name:'PBKDF2',hash:'SHA-256',salt:enc.encode(salt),iterations:600000},key,256);
    return `pbkdf2_sha256$600000$${salt}$${hex(bits)}`;
  }
  window.rfHashPassword=hashPassword;
  $('#f').onsubmit=async ev=>{
    ev.preventDefault();
    const p1=$('#p1').value, p2=$('#p2').value;
    if(p1.length<12) return msg('Use pelo menos 12 caracteres.',false);
    if(p1!==p2) return msg('As duas senhas não são iguais.',false);
    $('#go').disabled=true; msg('Gerando...',true);
    try{ $('#out').value=await hashPassword(p1); $('#p1').value=''; $('#p2').value=''; msg('Pronto. Copie o hash abaixo.',true); }
    catch(e){ msg('Seu navegador não conseguiu gerar o hash.',false); }
    finally{ $('#go').disabled=false; }
  };
  $('#copy').onclick=async()=>{
    if(!$('#out').value) return;
    try{ await navigator.clipboard.writeText($('#out').value); msg('Hash copiado.',true); }
    catch{ $('#out').select(); msg('Selecione e copie (Ctrl+C).',false); }
  };
})();
