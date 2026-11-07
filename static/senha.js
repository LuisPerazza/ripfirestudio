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

// Mesma regra do password_rules.py (o servidor é quem decide; aqui só avisa
// enquanto a pessoa digita). Se mudar uma, mude a outra.
const RF_PWD=(()=>{
  const LEET={'4':'a','@':'a','3':'e','1':'i','!':'i','0':'o','$':'s','5':'s','7':'t','+':'t','9':'g','8':'b'};
  const WORDS=["abc", "abcd", "abril", "acesso", "admin", "administrador", "agosto", "amor", "amore", "ana", "anjo", "asdf", "baseball", "batman", "bemvindo", "botafogo", "brasil", "brazil", "camisa", "camiseta", "casa", "changeme", "cliente", "computador", "corinthians", "cristo", "cruzeiro", "default", "deus", "deusefiel", "dezembro", "dragon", "dtf", "empresa", "entrar", "equipe", "escritorio", "estampa", "estudio", "familia", "felicidade", "fevereiro", "flamengo", "fluminense", "football", "forte", "funcionario", "futebol", "gabriel", "gatinha", "gatinho", "gremio", "hello", "iloveyou", "impressao", "impressora", "inter", "internet", "janeiro", "jesus", "joao", "joinville", "jose", "julho", "junho", "letmein", "linda", "lindo", "login", "loja", "love", "lucas", "maio", "marco", "maria", "master", "matrix", "mengao", "meu", "minha", "minhasenha", "monkey", "mudar", "naruto", "nova", "novembro", "oi", "ola", "outubro", "palmeiras", "pass", "passwd", "password", "pedro", "personalizado", "pokemon", "prensa", "princesa", "producao", "qwerty", "rafael", "ripfire", "root", "santos", "saopaulo", "secret", "segredo", "segura", "seguranca", "senha", "servidor", "setembro", "shadow", "sistema", "starwars", "studio", "sucesso", "sunshine", "superman", "teamo", "test", "teste", "trabalho", "trocar", "trustno", "user", "usuario", "vasco", "vida", "vitoria", "welcome", "zxcv"];
  const ROWS=["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm", "1qaz2wsx3edc4rfv", "qazwsxedc"];
  const LINES=(()=>{const l=['abcdefghijklmnopqrstuvwxyz','0123456789',...ROWS];return [...l,...l.map(x=>[...x].reverse().join(''))];})();
  const plain=t=>String(t||'').normalize('NFKD').replace(/[^\x00-\x7f]/g,'').toLowerCase();
  const leet=t=>[...t].map(c=>LEET[c]||c).join('');
  function runs(s,min){
    const spans=[]; let i=0;
    while(i<s.length){
      let best=1;
      for(const line of LINES){ const pos=line.indexOf(s[i]); if(pos<0) continue;
        let n=1; while(i+n<s.length&&pos+n<line.length&&s[i+n]===line[pos+n]) n++; best=Math.max(best,n); }
      let n=1; while(i+n<s.length&&s[i+n]===s[i]) n++; best=Math.max(best,n);
      if(best>=min){ spans.push([i,i+best]); i+=best; } else i++;
    }
    return spans;
  }
  const repeated=s=>{for(let k=1;k<=s.length/2;k++){ if(s.length%k===0&&s.slice(0,k).repeat(s.length/k)===s) return true; } return false;};
  function cutRuns(s){
    return s.split(/\s+/).filter(Boolean).map(part=>{ let out='',last=0; for(const [a,b] of runs(part,3)){ out+=part.slice(last,a); last=b; } out+=part.slice(last); return out; })
      .filter(p=>!(p.length>=2&&repeated(p))).join(' ');
  }
  function strip(low,words){
    let s=low.replace(/(19|20)\d\d/g,' ').replace(/\d{1,2}[/.\-]\d{1,2}([/.\-]\d{2,4})?/g,' ').replace(/[^a-z0-9@$!+]/g,' ');
    s=leet(cutRuns(s));
    for(const w of [...new Set(words.filter(w=>w.length>=3))].sort((a,b)=>b.length-a.length)) s=s.split(w).join(' ');
    s=s.replace(/[^a-z0-9]/g,' ');
    return cutRuns(s).replace(/ /g,'');
  }
  return function problem(pw,personal=[],strong=false){
    if(!/^[A-Za-z0-9!@#$%]+$/.test(String(pw||''))) return 'Use só letras (sem acento), números e os símbolos ! @ # $ % (sem espaço, _ ou -).';
    const low=plain(pw), norm=leet(low);
    if(new Set(low).size<(strong?6:4)) return 'A senha repete muitos caracteres. Misture letras e números diferentes.';
    if(repeated(low)||repeated(norm)) return 'A senha é um pedaço repetido (tipo 1212 ou abcabc). Escolha outra.';
    const mine=[]; for(const item of personal) for(const t of leet(plain(String(item||'').split('@')[0])).split(/[^a-z0-9]+/)) if(t.length>=3) mine.push(t);
    if(mine.some(w=>norm.includes(w))) return 'A senha não pode ter o seu nome, usuário ou e-mail.';
    const rest=strip(low,WORDS.concat(mine));
    if(rest.length<(strong?6:4)||new Set(rest).size<(strong?4:3))
      return 'Senha fácil de adivinhar: evite sequências (abc, 123, qwerty), datas, palavras como "senha" ou "admin" e trocas tipo s3nh4. Junte palavras que não combinam, com maiúsculas e um símbolo (ex.: GatoTijoloNuvem#7).';
    return null;
  };
})();
window.rfPasswordProblem=RF_PWD;
// Aviso ao vivo embaixo dos campos de senha nova: data-strength="staff" pede a
// regra da equipe (12+); data-personal lista os campos com nome/e-mail/usuário.
(function(){
  const css=document.createElement('style');
  css.textContent='.pw-hint{display:block;font-size:12px;line-height:1.4;margin:4px 2px 8px;min-height:0}.pw-hint.bad{color:#b3261e}.pw-hint.good{color:#1f8a4c}'
    +'html[data-theme="dark"] .pw-hint.bad{color:#ff8a80}html[data-theme="dark"] .pw-hint.good{color:#6fdc9a}';
  document.head.appendChild(css);
  function bind(input){
    if(input.dataset.pwHint) return; input.dataset.pwHint='1';
    const staff=input.dataset.strength==='staff', min=staff?12:8;
    const hint=document.createElement('small'); hint.className='pw-hint'; hint.setAttribute('aria-live','polite');
    (input.closest('.pw-wrap')||input).insertAdjacentElement('afterend',hint);
    const update=()=>{
      const v=input.value;
      if(!v){ hint.textContent=''; hint.className='pw-hint'; return; }
      const personal=(input.dataset.personal||'').split(',').filter(Boolean).map(sel=>{const el=document.querySelector(sel);return el?el.value:'';});
      const p=v.length<min?`Use pelo menos ${min} caracteres.`:RF_PWD(v,personal,staff);
      hint.textContent=p||'Senha boa.'; hint.className='pw-hint '+(p?'bad':'good');
    };
    input.addEventListener('input',update);
    input.form&&input.form.addEventListener('reset',()=>setTimeout(update));
  }
  function run(){ document.querySelectorAll('input[autocomplete="new-password"]:not([data-pw-confirm])').forEach(bind); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',run); else run();
})();
