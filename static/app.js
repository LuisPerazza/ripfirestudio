(() => {
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let CONFIG, artworks=[], selected=null, dragging=null, adminKey=null, adminUser=null, adminRole=null, shirtMockups=[];
let shirtResize=null; // {piece,pointerId} - arraste do quadradinho de esticar (mesmo modelo da chapa de DTF)
let currentModel='regular', currentSide='FRONT';
let sidesElements={FRONT:[],BACK:[],SLEEVE_R:[],SLEEVE_L:[]};
let elements=sidesElements.FRONT; // sempre aponta pro array do lado ativo (frente, verso ou uma das mangas)
// Mangas: a área de impressão tem tamanho real fixo (o que aparece na tela
// é o tamanho que sai no DTF). Proporção 420x480, igual ao canvas, pra não
// distorcer a arte.
const SLEEVE_SIDES={SLEEVE_R:'Manga direita',SLEEVE_L:'Manga esquerda'};
const SLEEVE_PRINT_W_CM=10, SLEEVE_PRINT_H_CM=SLEEVE_PRINT_W_CM*480/420;
const isSleeve=side=>Object.prototype.hasOwnProperty.call(SLEEVE_SIDES,side);
// Área máxima de impressão (frente/verso) por tamanho de camiseta, em cm.
// Os valores reais ficam em size_print_limits no config.json; estes são só
// reserva caso o servidor ainda não mande o campo.
const DEFAULT_SIZE_LIMITS={P:{w:26,h:34},M:{w:28,h:36},G:{w:30,h:38},GG:{w:32,h:40},G1:{w:34,h:42},G2:{w:36,h:44},G3:{w:38,h:46},G4:{w:40,h:48}};
let currentSize=null; // tamanho clicado na grade (P, M, G...) pra mostrar o limite
const canvas=$("#designCanvas"), ctx=canvas.getContext("2d");
const money=v=>Number(v||0).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]));
const TAB_TITLES={home:'Início',editor:'Personalizar',digital:'Artes digitais',profile:'Minha conta',admin:'Admin'};
// Troca de tela (o site é uma página só: cada "aba" é uma <section class="tab">).
function tab(id){
  document.title=`${TAB_TITLES[id]||'Ripfire'} · Ripfire Studio`;
  if(id!=='admin'){
    countPage(id);
    track('page_view',{page_title:id,page_location:`${location.origin}/${id}`});
  }
  $$('.tab').forEach(x=>x.classList.toggle('active',x.id===id));
  $$('.nav-btn').forEach(x=>x.classList.toggle('active',x.dataset.tab===id));
  document.body.classList.toggle('is-home',id==='home');
  document.body.classList.toggle('is-editor',id==='editor');
  document.body.classList.toggle('is-digital',id==='digital');
  $('#navAccount').classList.toggle('current',id==='profile');
  if(id==='editor') positionPrintArea();
  if(id==='admin') enterAdmin();
  if(id==='profile'){ stat('#profileNotice','',true); loadProfile(); }
}

/* ============ Conta do cliente (login/cadastro) ============ */
let currentCustomer=null; // {id,email,full_name,phone,cep,address_number,document,premium_until,token}
function loadCustomerFromStorage(){
  try{ const raw=localStorage.getItem('ripfire_customer'); if(raw) currentCustomer=JSON.parse(raw); }catch{ currentCustomer=null; }
  // Login salvo antes da sessão existir (sem token): o servidor não reconhece, então pede pra entrar de novo.
  if(currentCustomer && !currentCustomer.token){ currentCustomer=null; saveCustomerToStorage(); }
}
function customerHeaders(){return {'X-Customer-Token': currentCustomer?.token||''}}
// Atualiza os dados da conta (o premium muda quando o pagamento é aprovado).
async function refreshCustomer(){
  if(!currentCustomer) return;
  try{
    const r=await fetch('/api/me',{headers:customerHeaders()});
    if(r.status===401){ currentCustomer=null; saveCustomerToStorage(); }
    else if(r.ok){ currentCustomer={...currentCustomer,...await r.json().catch(()=>({}))}; saveCustomerToStorage(); }
  }catch{}
  renderAccountBoxes();
}
function saveCustomerToStorage(){
  try{ if(currentCustomer) localStorage.setItem('ripfire_customer', JSON.stringify(currentCustomer)); else localStorage.removeItem('ripfire_customer'); }catch{}
}
function customerPayload(){
  const c=currentCustomer||{};
  return {name:c.full_name||'', email:c.email||'', whatsapp:c.phone||'', cep:c.cep||'', address_number:c.address_number||'', document:c.document||''};
}
function renderAccountBoxes(){
  const logged=!!currentCustomer;
  [['accountInfoShirt','openAuthShirt','logoutBtnShirt'],['accountInfoGang','openAuthGang','logoutBtnGang']].forEach(([infoId,openId,logoutId])=>{
    $('#'+infoId).textContent = logged ? `Logado como ${currentCustomer.full_name} (${currentCustomer.email})` : 'Você ainda não entrou com uma conta.';
    $('#'+openId).hidden = logged;
    $('#'+logoutId).hidden = !logged;
  });
  // Botão do menu: "Entrar" ou a inicial + primeiro nome de quem está logado.
  $('#navAvatar').hidden=!logged; $('#navAvatar').textContent=logged?customerInitial():'';
  $('#navAccountLabel').textContent=logged?(currentCustomer.full_name||'').trim().split(/\s+/)[0]||'Minha conta':'Entrar';
  $('#navAccount').classList.toggle('logged',logged);
  renderLibrary(); // selos e botões mudam com a conta
}
const customerInitial=()=>(currentCustomer?.full_name||currentCustomer?.email||'?').trim().charAt(0).toUpperCase();
/* ============ Contagem anônima de páginas ============ */
// Só "dia, página, +1" no servidor: sem cookie, sem IP gravado, sem
// identificador. Não depende de consentimento e conta todo mundo (o GA só
// conta quem aceita). keepalive: não se perde se a pessoa sair da página.
function countPage(page){
  try{ fetch('/api/metrics/hit',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify({page})}).catch(()=>{}); }catch{}
}
// Uma visita por aba aberta (o sessionStorage some quando fecha a aba; nada vai pro servidor além do +1).
try{ if(!sessionStorage.getItem('rf_visit')){ sessionStorage.setItem('rf_visit','1'); countPage('visit'); } }catch{}

/* ============ Cookies e consentimento ============ */
// Necessário (sempre ligado, não pede aceite): o login salvo no localStorage.
// Opcionais, cada um só carrega com permissão:
//   google    → script do "Entrar com Google" (cookies do Google)
//   analytics → Google Analytics 4 (GA4_MEASUREMENT_ID no .env)
//   marketing → Pixel da Meta, Facebook/Instagram (META_PIXEL_ID no .env)
//   Google Tag Manager (GTM_CONTAINER_ID) carrega com qualquer um dos dois e
//   recebe o Consent Mode v2 com o que foi permitido.
// Mudou o que o site usa? Suba CONSENT_VERSION para todo mundo ver o aviso de novo.
const CONSENT_KEY='ripfire_consent', CONSENT_VERSION=2;
const CONSENT_KINDS=['google','analytics','marketing'];
function getConsent(){
  try{ const c=JSON.parse(localStorage.getItem(CONSENT_KEY)||'null'); return c&&c.v===CONSENT_VERSION?c:null; }catch{ return null; }
}
const loaded={google:false,analytics:false,marketing:false}; // o que já rodou nesta página
let gtmGranted=null; // permissões com que o GTM foi carregado ({analytics,marketing})
function loadScript(src){const s=document.createElement('script');s.src=src;s.async=true;document.head.appendChild(s)}
// choice: {google,analytics,marketing}; true/false liga ou desliga todos.
function saveConsent(choice){
  const c=Object.fromEntries(CONSENT_KINDS.map(k=>[k,typeof choice==='object'?!!choice[k]:!!choice]));
  try{ localStorage.setItem(CONSENT_KEY,JSON.stringify({v:CONSENT_VERSION,...c,at:new Date().toISOString()})); }catch{}
  $('#cookieBanner').hidden=true;
  // Script de terceiro que já rodou só sai recarregando. O GA é desligado antes,
  // senão ele grava o cookie de novo enquanto a página fecha.
  const gtmLost=gtmGranted&&((gtmGranted.analytics&&!c.analytics)||(gtmGranted.marketing&&!c.marketing));
  if(gtmLost||CONSENT_KINDS.some(k=>loaded[k]&&!c[k])){
    if(CONFIG?.ga4_id) window['ga-disable-'+CONFIG.ga4_id]=true;
    clearTrackingCookies({analytics:!c.analytics,marketing:!c.marketing});
    return location.reload();
  }
  applyConsent();
}
// Apaga os cookies de métricas (_ga, _ga_<ID>...) e/ou de anúncios (_fbp, _fbc) do nosso domínio.
function clearTrackingCookies(kinds){
  const host=location.hostname, domains=['', host, '.'+host.split('.').slice(-2).join('.')];
  const re=new RegExp('^('+[kinds.analytics&&'_ga|_gid|_gat',kinds.marketing&&'_fbp|_fbc'].filter(Boolean).join('|')+')');
  if(re.source==='^()') return;
  document.cookie.split(';').map(x=>x.split('=')[0].trim()).filter(n=>re.test(n))
    .forEach(n=>domains.forEach(d=>{document.cookie=`${n}=; Max-Age=0; path=/${d?'; domain='+d:''}`}));
}
function applyConsent(){
  const c=getConsent()||{};
  // Sobrou cookie de algo que não está permitido (retirada, ou aceite antigo)? Some aqui.
  clearTrackingCookies({analytics:!c.analytics,marketing:!c.marketing});
  if(c.google&&!loaded.google){ loaded.google=true; loadScript('https://accounts.google.com/gsi/client'); }
  if(c.analytics&&CONFIG?.ga4_id&&!loaded.analytics){
    loaded.analytics=true;
    window.dataLayer=window.dataLayer||[];
    window.gtag=function(){dataLayer.push(arguments)};
    gtag('js',new Date());
    gtag('config',CONFIG.ga4_id); // já registra a primeira visualização de página
    loadScript(`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(CONFIG.ga4_id)}`);
  }
  if((c.analytics||c.marketing)&&CONFIG?.gtm_id){
    const g=v=>v?'granted':'denied', state={analytics_storage:g(c.analytics),ad_storage:g(c.marketing),ad_user_data:g(c.marketing),ad_personalization:g(c.marketing)};
    window.dataLayer=window.dataLayer||[];
    window.gtag=window.gtag||function(){dataLayer.push(arguments)};
    if(!gtmGranted){
      // Consent Mode v2: tudo negado por padrão e liberado só o que a pessoa aceitou,
      // antes do GTM carregar. As tags do Google dentro dele respeitam isso sozinhas.
      gtag('consent','default',{analytics_storage:'denied',ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});
      gtag('consent','update',state);
      dataLayer.push({'gtm.start':Date.now(),event:'gtm.js'});
      loadScript(`https://www.googletagmanager.com/gtm.js?id=${encodeURIComponent(CONFIG.gtm_id)}`);
    }else gtag('consent','update',state); // liberou mais coisa depois: só avisa o GTM
    gtmGranted={analytics:!!c.analytics,marketing:!!c.marketing};
  }
  if(c.marketing&&CONFIG?.meta_pixel_id&&!loaded.marketing){
    loaded.marketing=true;
    // Mesmo carregador do código oficial da Meta, só que legível: guarda as chamadas até o script chegar.
    const fbq=window.fbq=function(){fbq.callMethod?fbq.callMethod.apply(fbq,arguments):fbq.queue.push(arguments)};
    window._fbq=fbq; fbq.push=fbq; fbq.loaded=true; fbq.version='2.0'; fbq.queue=[];
    loadScript('https://connect.facebook.net/en_US/fbevents.js');
    fbq('init',CONFIG.meta_pixel_id); fbq('track','PageView');
  }
}
// Evento nos dois (nomes do GA4; a Meta usa os equivalentes). Sem consentimento, não faz nada.
const META_EVENTS={page_view:'PageView',sign_up:'CompleteRegistration',begin_checkout:'InitiateCheckout',purchase:'Purchase'};
function track(name,params={}){
  try{
    // No GTM, no formato de e-commerce do GA4 (os gatilhos usam o nome do evento).
    if(gtmGranted){
      const {transaction_id,value,currency,items,...rest}=params;
      if(value!=null){ dataLayer.push({ecommerce:null}); dataLayer.push({event:name,ecommerce:{transaction_id,value,currency:currency||'BRL',items},...rest}); }
      else dataLayer.push({event:name,...rest});
    }
    if(loaded.analytics) gtag('event',name,params);
    if(loaded.marketing&&META_EVENTS[name]) fbq('track',META_EVENTS[name],params.value!=null?{value:params.value,currency:params.currency||'BRL'}:{},
      params.transaction_id?{eventID:params.transaction_id}:undefined); // mesmo id do evento do servidor
  }catch{}
}
// Vai junto com o pedido: o servidor manda a compra pro GA/Meta quando o
// pagamento for aprovado, mesmo que a pessoa feche a aba. Só com consentimento,
// e só identificadores anônimos dos próprios cookies do GA e da Meta.
function trackingContext(){
  const c=getConsent()||{}, ctx={analytics:!!(c.analytics&&(loaded.analytics||gtmGranted?.analytics)),marketing:!!(c.marketing&&(loaded.marketing||gtmGranted?.marketing))};
  const cookie=n=>(document.cookie.match(new RegExp('(?:^|; )'+n+'=([^;]*)'))||[])[1]||'';
  if(ctx.analytics){
    ctx.ga_client_id=cookie('_ga').split('.').slice(2).join('.'); // GA1.1.123.456 -> 123.456
    const s=CONFIG.ga4_id?cookie('_ga_'+CONFIG.ga4_id.replace(/^G-/,'')):''; // GS1.1.<sessão>... ou GS2.1.s<sessão>$...
    ctx.ga_session_id=(s.match(/^GS2\.\d\.s(\d+)/)||s.match(/^GS1\.\d\.(\d+)/)||[])[1]||'';
  }
  if(ctx.marketing){ ctx.fbp=cookie('_fbp'); ctx.fbc=cookie('_fbc'); }
  return ctx;
}
// Compra aprovada conta uma vez só por pedido, mesmo voltando várias vezes à página.
function trackPurchase(orderId,orderNumber,value){
  let done=[]; try{ done=JSON.parse(localStorage.getItem('ripfire_tracked')||'[]'); }catch{}
  if(done.includes(orderId)) return;
  track('purchase',{transaction_id:orderNumber,value:Number(value)||0,currency:'BRL'});
  try{ localStorage.setItem('ripfire_tracked',JSON.stringify([...done,orderId].slice(-50))); }catch{}
}
function openCookiePrefs(){
  const c=getConsent()||{};
  CONSENT_KINDS.forEach(k=>{const box=$('#cookie_'+k); if(box) box.checked=!!c[k];});
  $('#cookieDetails').hidden=false; $('#cookieSave').hidden=false; $('#cookieCustomize').hidden=true;
  $('#cookieBanner').hidden=false;
}
// Roda depois do /api/config: o aviso só lista o que está configurado no servidor.
function initConsent(){
  const on={google:!!CONFIG.google_client_id,analytics:!!(CONFIG.ga4_id||CONFIG.gtm_id),marketing:!!(CONFIG.meta_pixel_id||CONFIG.gtm_id)};
  CONSENT_KINDS.forEach(k=>{$('#cookieRow_'+k).hidden=!on[k]});
  $('#cookieTrackNote').textContent=on.analytics||on.marketing
    ?'Métricas de visitas e anúncios só são ativados se você permitir.'
    :'Não usamos cookies de publicidade nem de métricas.';
  $('#cookieAccept').onclick=()=>saveConsent(true);
  $('#cookieReject').onclick=()=>saveConsent(false);
  $('#cookieCustomize').onclick=openCookiePrefs;
  $('#cookieSave').onclick=()=>saveConsent(Object.fromEntries(CONSENT_KINDS.map(k=>[k,!!$('#cookie_'+k)?.checked])));
  $('#cookiePrefsLink').onclick=ev=>{ev.preventDefault();openCookiePrefs()};
  if(!getConsent()) $('#cookieBanner').hidden=false;
  applyConsent();
}

let googleButtonRendered=false, googleInitAttempts=0;
function ensureGoogleButton(){
  const clientId=CONFIG&&CONFIG.google_client_id;
  // Sem Google configurado (ou o script dele não carregou), o bloco some: o
  // cliente via um botão "indisponível" desabilitado.
  if(!clientId){ $('#authGoogle').hidden=true; return; }
  // Sem permissão: um botão nosso no lugar; clicar nele é o aceite e carrega o Google.
  const allowed=!!getConsent()?.google;
  $('#googleConsent').hidden=allowed;
  if(!allowed) return;
  if(googleButtonRendered) return;
  if(!window.google||!google.accounts||!google.accounts.id){
    if(googleInitAttempts++<20) setTimeout(ensureGoogleButton,250);
    else $('#authGoogle').hidden=true; // script do Google não carregou
    return;
  }
  google.accounts.id.initialize({client_id:clientId, callback:handleGoogleCredential});
  google.accounts.id.renderButton($('#googleBtnContainer'),{theme:'outline',size:'large',shape:'pill',width:Math.min(380,$('#googleBtnContainer').clientWidth||380),text:'continue_with'});
  googleButtonRendered=true;
}
async function handleGoogleCredential(response){
  try{
    const r=await fetch('/api/auth/google',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({credential:response.credential})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail||'Erro ao entrar com Google.');
    currentCustomer=d; saveCustomerToStorage(); renderAccountBoxes();
    stat('#authStatus','Login com Google realizado!',true);
    setTimeout(()=>$('#authModal').close(),500);
  }catch(e){ stat('#authStatus',e.message,false); }
}
// Caixa de entrar/criar conta (logado, o botão do menu leva pra "Minha conta").
function openAuthModal(){
  switchAuthTab('login');
  $('#authModal').showModal();
  ensureGoogleButton(); // depois do showModal: o botão do Google mede a largura da caixa
  $('#loginEmail').focus(); // senão o foco cai no × de fechar
}
function switchAuthTab(tabName){
  $$('.auth-tab').forEach(b=>b.classList.toggle('active', b.dataset.authtab===tabName));
  $('#authLoginForm').hidden = tabName!=='login';
  $('#authRegisterForm').hidden = tabName!=='register';
  $('#authTitle').textContent = tabName==='login' ? 'Bem-vindo de volta' : 'Crie sua conta';
  $('#authSubtitle').textContent = tabName==='login' ? 'Entre para baixar artes e finalizar seus pedidos.' : 'Leva menos de um minuto e já libera as artes grátis.';
  $('#authStatus').textContent=''; $('#authStatus').className='';
}
function formatPhoneBR(raw){
  let digits=raw.replace(/\D/g,'');
  if(digits.startsWith('55')) digits=digits.slice(2);
  digits=digits.slice(0,11);
  const ddd=digits.slice(0,2), rest=digits.slice(2);
  let out='+55';
  if(ddd) out+=' '+ddd;
  if(rest) out+=' '+(rest.length>4 ? rest.slice(0,rest.length-4)+'-'+rest.slice(-4) : rest);
  return out;
}
function formatCpfCnpj(raw){
  let digits=raw.replace(/\D/g,'');
  if(digits.length<=11){
    digits=digits.slice(0,11);
    const p1=digits.slice(0,3),p2=digits.slice(3,6),p3=digits.slice(6,9),p4=digits.slice(9,11);
    let out=p1;
    if(p2) out+='.'+p2;
    if(p3) out+='.'+p3;
    if(p4) out+='-'+p4;
    return out;
  }
  digits=digits.slice(0,14);
  const p1=digits.slice(0,2),p2=digits.slice(2,5),p3=digits.slice(5,8),p4=digits.slice(8,12),p5=digits.slice(12,14);
  let out=p1;
  if(p2) out+='.'+p2;
  if(p3) out+='.'+p3;
  if(p4) out+='/'+p4;
  if(p5) out+='-'+p5;
  return out;
}
function bindMaskedInput(el,formatter){
  el.addEventListener('focus',()=>{ if(!el.value) el.value=formatter(''); });
  el.addEventListener('input',()=>{ el.value=formatter(el.value); });
  // Não manda só o "+55" do foco quando a pessoa não digitou nada.
  el.addEventListener('blur',()=>{ if(el.value===formatter('')) el.value=''; });
}
async function submitLogin(){
  const email=$('#loginEmail').value.trim(), password=$('#loginPassword').value;
  if(!email||!password) return stat('#authStatus','Preencha e-mail e senha.',false);
  try{
    const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail||'Erro ao entrar.');
    currentCustomer=d; saveCustomerToStorage(); renderAccountBoxes();
    stat('#authStatus','Login realizado!',true);
    setTimeout(()=>$('#authModal').close(),500);
  }catch(e){ stat('#authStatus',e.message,false); }
}
async function submitRegister(){
  const payload={
    full_name:$('#regName').value.trim(),
    email:$('#regEmail').value.trim(),
    password:$('#regPassword').value,
    phone:$('#regPhone').value.trim(),
    cep:$('#regCep').value.trim(),
    address_number:$('#regNumber').value.trim(),
    document:$('#regDocument').value.trim()
  };
  if(!payload.full_name||!payload.email||!payload.password) return stat('#authStatus','Preencha nome, e-mail e senha.',false);
  try{
    const r=await fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail||'Erro ao cadastrar.');
    currentCustomer=d; saveCustomerToStorage(); renderAccountBoxes();
    track('sign_up',{method:'email'});
    stat('#authStatus','Conta criada!',true);
    setTimeout(()=>$('#authModal').close(),500);
  }catch(e){ stat('#authStatus',e.message,false); }
}
function logoutCustomer(){
  if(currentCustomer?.token) fetch('/api/auth/logout',{method:'POST',headers:customerHeaders()}).catch(()=>{});
  currentCustomer=null; profileData=null; saveCustomerToStorage(); renderAccountBoxes();
  if($('#profile').classList.contains('active')) tab('home');
}

async function fetchConfigWithRetry(tries=20){for(let i=0;;i++){try{const r=await fetch('/api/config',{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);return await r.json().catch(()=>({}))}catch(e){if(i>=tries-1)throw e;await new Promise(res=>setTimeout(res,500))}}}
async function init(){
  CONFIG=await fetchConfigWithRetry();
  initConsent();          // depende do CONFIG: o aviso lista só o que está configurado
  countPage('home');
  gangHeightCm=sheetMinHeightCm();
  loadCustomerFromStorage();
  fillConfig();
  bind();
  renderAccountBoxes();
  refreshCustomer();
  loadLibrary();
  await loadArtworks();
  await loadShirtMockups();
  toggleSale(); updateShirtVisual(); updatePrice(); render();
  renderGangList(); renderMockupsList();
  window.addEventListener('resize',positionPrintArea);
  window.addEventListener('hashchange',openAdminFromHash);
  restoreAdminSession();
  openAdminFromHash();
  handlePaymentReturn();  // volta do Mercado Pago (?mp=retorno)
}
function fillConfig(){
  currentModel=CONFIG.shirt_models[0]?.id||'regular';
  $('#shirtModelTabs').innerHTML=CONFIG.shirt_models.map(x=>`<button type="button" class="model-tab${x.id===currentModel?' active':''}" data-model="${x.id}">${x.name}</button>`).join('');
  $$('#shirtModelTabs [data-model]').forEach(b=>b.onclick=()=>{
    currentModel=b.dataset.model;
    $$('#shirtModelTabs [data-model]').forEach(x=>x.classList.toggle('active',x===b));
    renderSizeGrid();updateShirtVisual();updatePrice();render();
  });
  $('#shirtColor').innerHTML=CONFIG.shirt_colors.map(x=>`<option>${x}</option>`).join('');
  $('#mockupModel').innerHTML=CONFIG.shirt_models.map(x=>`<option value="${x.id}">${x.name}</option>`).join('');
  $('#mockupColor').innerHTML=CONFIG.shirt_colors.map(x=>`<option>${x}</option>`).join('');
  renderSizeGrid();
  $('#aiStatus').textContent=CONFIG.ai_enabled?`IA ativa: ${CONFIG.ai_model}`:'IA desativada até configurar .env';
  $('#gangAiStatus').textContent=CONFIG.ai_enabled?`IA ativa: ${CONFIG.ai_model}`:'IA desativada até configurar .env';
  $$('.ai-tool').forEach(el=>el.hidden=!(AI_UI_ENABLED&&CONFIG.ai_enabled));
}
// "Criar com IA" fora do site por enquanto. Pra voltar: true (e OPENAI_API_KEY no servidor).
const AI_UI_ENABLED=false;
function bind(){
  $$('.nav-btn').forEach(b=>b.onclick=()=>tab(b.dataset.tab));$('#homeLogo').onclick=()=>{tab('home');window.scrollTo({top:0,behavior:'smooth'})};$('#startCustomize').onclick=()=>tab('editor');
  $('#openAuthShirt').onclick=openAuthModal;$('#openAuthGang').onclick=openAuthModal;
  $('#navAccount').onclick=()=>currentCustomer?tab('profile'):openAuthModal();
  $('#logoutBtnShirt').onclick=logoutCustomer;$('#logoutBtnGang').onclick=logoutCustomer;
  $$('#profile [data-pview]').forEach(b=>b.onclick=()=>{profileView=b.dataset.pview;renderProfile()});
  $('#pfSave').onclick=saveProfile;$('#pfPasswordSave').onclick=savePassword;
  $('#pfPasswordToggle').onclick=()=>{$('#pfPasswordForm').hidden=!$('#pfPasswordForm').hidden};
  $('#pfLogout').onclick=logoutCustomer;
  $('#pfExport').onclick=exportMyData;
  $('#pfDeleteToggle').onclick=()=>{$('#pfDeleteForm').hidden=!$('#pfDeleteForm').hidden};
  $('#pfDeleteSave').onclick=deleteMyAccount;
  $('#refreshLgpd').onclick=loadLgpdRequests;
  bindAdmin();
  $('#backupNow').onclick=backupNow;
  bindMaskedInput($('#pfPhone'),formatPhoneBR);bindMaskedInput($('#pfDocument'),formatCpfCnpj);
  $('#pfCep').addEventListener('input',e=>{e.target.value=formatCep(e.target.value)});
  $$('.auth-tab').forEach(b=>b.onclick=()=>switchAuthTab(b.dataset.authtab));
  $('#loginSubmitBtn').onclick=submitLogin;$('#registerSubmitBtn').onclick=submitRegister;
  bindMaskedInput($('#regPhone'),formatPhoneBR);bindMaskedInput($('#regDocument'),formatCpfCnpj);
  $('#adminLoginBtn').onclick=submitAdminLogin;
  $('#googleConsentBtn').onclick=()=>{saveConsent({...(getConsent()||{}),google:true});googleInitAttempts=0;ensureGoogleButton()};
  // Enter dentro de um <form method="dialog"> "clica" no primeiro botão do
  // form, que é o × de fechar: o modal sumia em vez de entrar/cadastrar.
  const onEnter=(sels,fn)=>sels.forEach(s=>$(s).addEventListener('keydown',ev=>{if(ev.key==='Enter'){ev.preventDefault();fn()}}));
  onEnter(['#loginEmail','#loginPassword'],submitLogin);
  onEnter(['#regName','#regEmail','#regPassword','#regPhone','#regCep','#regNumber','#regDocument'],submitRegister);
  onEnter(['#adminUser','#adminPassword','#adminCode'],submitAdminLogin);
  $('#saleType').onchange=()=>{toggleSale();updatePrice()};$('#shirtColor').onchange=updateShirtVisual;$('#sizeGrid').oninput=updatePrice;
  $$('#sideTabs [data-side]').forEach(b=>b.onclick=()=>selectSide(b.dataset.side));
  const addShirtFile=f=>{if(!f)return;if(!f.type.startsWith('image/'))return alert('Selecione um arquivo de imagem (PNG, JPG ou WEBP).');addImage(URL.createObjectURL(f),'UPLOAD').catch(err=>alert(err.message))};
  $('#uploadArt').onchange=e=>{addShirtFile(e.target.files[0]);e.target.value=''};
  const shirtDropEl=$('#shirtDrop');
  ['dragenter','dragover'].forEach(evt=>shirtDropEl.addEventListener(evt,ev=>{ev.preventDefault();shirtDropEl.classList.add('drag')}));
  ['dragleave','dragend'].forEach(evt=>shirtDropEl.addEventListener(evt,()=>shirtDropEl.classList.remove('drag')));
  shirtDropEl.addEventListener('drop',ev=>{ev.preventDefault();shirtDropEl.classList.remove('drag');addShirtFile(ev.dataTransfer.files[0])});
  $$('[data-open-arts]').forEach(b=>b.onclick=openArtPicker);
  $$('[data-ap-close]').forEach(b=>b.onclick=()=>$('#artPickerModal').close());
  $('#generateAI').onclick=generateAI;$('#deleteSelected').onclick=()=>{if(!selected)return;elements=elements.filter(e=>e!==selected);sidesElements[currentSide]=elements;selected=null;render()};
  $('#centerSelected').onclick=()=>{if(selected){const r=limitRect();selected.x=r.x+r.w/2;selected.y=r.y+r.h/2;clampDragToCanvas(selected);render()}};$('#bringFront').onclick=()=>{if(selected){elements=elements.filter(e=>e!==selected);elements.push(selected);sidesElements[currentSide]=elements;render()}};
  $('#shirtHandleRotate').onclick=()=>{if(!selected)return;selected.rotation=((selected.rotation||0)+90)%360;clampResizeToCanvas(selected);clampDragToCanvas(selected);render()};
  $('#shirtHandleResize').onpointerdown=ev=>{if(!selected)return;ev.preventDefault();ev.stopPropagation();shirtResize={piece:selected,pointerId:ev.pointerId};canvas.setPointerCapture(ev.pointerId)};
  bindPrintAreaDrag();
  canvas.onkeydown=shirtKeydown;
  // Alça de esticar pelo teclado: cada seta mexe só um eixo, como arrastar a alça.
  $('#shirtHandleResize').onkeydown=ev=>{
    const f={ArrowRight:[1,0],ArrowLeft:[-1,0],ArrowUp:[0,1],ArrowDown:[0,-1]}[ev.key];
    if(!f||!selected) return;
    ev.preventDefault();
    const step=ev.shiftKey?0.2:0.05;
    selected.scaleX*=1+f[0]*step; selected.scaleY*=1+f[1]*step;
    clampResizeToCanvas(selected); clampDragToCanvas(selected); render(); announce(describeSelected());
  };
  canvas.onpointerdown=pointerDown;canvas.onpointermove=pointerMove;window.onpointerup=()=>{dragging=null;if(shirtResize){canvas.hasPointerCapture(shirtResize.pointerId)&&canvas.releasePointerCapture(shirtResize.pointerId);shirtResize=null}};
  $('#checkoutBtn').onclick=createOrder;$('#refreshOrders').onclick=enterAdmin;
  $('#gangUpload').onchange=onGangUpload;$('#gangAddBtn').onclick=addGangImage;$('#gangAuto').onclick=()=>autoArrangeGang(true);$('#gangReorganize').onclick=()=>autoArrangeGang(true);
  $('#gangGenerateAI').onclick=generateGangAI;
  const gangDropEl=$('#gangDrop');
  ['dragenter','dragover'].forEach(evt=>gangDropEl.addEventListener(evt,ev=>{ev.preventDefault();gangDropEl.classList.add('drag')}));
  ['dragleave','dragend'].forEach(evt=>gangDropEl.addEventListener(evt,()=>gangDropEl.classList.remove('drag')));
  gangDropEl.addEventListener('drop',ev=>{
    ev.preventDefault();
    gangDropEl.classList.remove('drag');
    const f=ev.dataTransfer.files[0];
    if(f) handleGangFile(f);
  });
  $('#gangWidthCm').oninput=updateGangHeightPreview;
  $('#gangRotatePiece').onclick=()=>{if(!gangSelected)return;rotateGangPiece(gangSelected);recalcGangCanvasHeight();renderGangCanvas();updatePrice();};
  $('#gangFlipHBtn').onclick=()=>{if(!gangSelected)return;gangSelected.flipX=!gangSelected.flipX;renderGangCanvas();};
  $('#gangFlipVBtn').onclick=()=>{if(!gangSelected)return;gangSelected.flipY=!gangSelected.flipY;renderGangCanvas();};
  $('#checkoutBtnGang').onclick=createGangOrder;
  $('#mockupForm').onsubmit=uploadMockup;
  $('#libCategoryForm').onsubmit=createLibraryCategory;$('#libUploadForm').onsubmit=uploadLibraryArts;
  $('#mockupPhoto').onchange=previewMockupPhoto;
  bindMockupBoxDrag();
  ['mockupX','mockupY','mockupW','mockupH'].forEach(id=>$('#'+id).oninput=updateMockupPreviewBox);
}
function toggleSale(){
  const isGang = $('#saleType').value === 'DTF_ONLY';
  $('#editorLayout').classList.toggle('gang-mode', isGang);
  $$('.shirt-only').forEach(el=>el.style.display = isGang ? 'none' : 'block');
  $$('.gang-only').forEach(el=>el.style.display = isGang ? 'block' : 'none');
  if(isGang) renderGangCanvas();
  else positionPrintArea();
}
function chosenModel(){return CONFIG.shirt_models.find(x=>x.id===currentModel)||CONFIG.shirt_models[0]}
// Cada modelo pode ter a própria lista de tamanhos no config.json (o infantil
// usa 2 a 16); sem lista, vale a geral (P, M, G...).
function modelSizes(){return chosenModel()?.sizes||CONFIG.sizes}
function renderSizeGrid(){
  const sizes=modelSizes(), grid=$('#sizeGrid');
  if(grid.dataset.sizes===sizes.join(',')) return; // mesmos tamanhos: mantém as quantidades digitadas
  grid.dataset.sizes=sizes.join(',');
  if(!sizes.includes(currentSize)) currentSize=null;
  grid.innerHTML=sizes.map(s=>`<div class="size-cell${s===currentSize?' active':''}" data-pick="${esc(s)}"><button type="button" class="size-pick" title="Ver área máxima do tamanho ${esc(s)}">${esc(s)}</button><input data-size="${esc(s)}" type="number" min="0" value="0" aria-label="Quantidade no tamanho ${esc(s)}"></div>`).join('');
  $$('#sizeGrid [data-pick]').forEach(cell=>{
    cell.querySelector('.size-pick').onclick=()=>selectSize(cell.dataset.pick);
    cell.querySelector('input').addEventListener('focus',()=>selectSize(cell.dataset.pick));
  });
}
// min="0" no input não impede digitar -3 ou 1,5: sem isso o total ficava negativo.
const sizeQty=i=>Math.max(0,Math.floor(Number(i.value)||0));
function shirtQty(){return $$('#sizeGrid input').reduce((a,i)=>a+sizeQty(i),0)}
function updatePrice(){
  if(!CONFIG)return;
  if($('#saleType').value==='DTF_ONLY'){$('#priceTotal').textContent=money(calcGangTotal());return}
  $('#priceTotal').textContent=money(calcTotal());
}
function calcTotal(){const q=shirtQty(),t=$('#saleType').value,p=CONFIG.pricing,m=chosenModel();if(t==='DTF_ONLY')return q*p.dtf_only_unit;if(t==='DTF_PLUS_SHIRT')return q*(p.dtf_only_unit+m.base_price);return q*(p.dtf_only_unit+m.base_price+p.finished_press_unit)}

/* ---------- Mockup 3D da camiseta (SVG, gerado por parâmetros) ---------- */
const SHIRT_COLOR_HEX={'Preto':'#161616','Branco':'#f7f6f2','Off White':'#e9e2d0','Marrom':'#4a3324'};
const CX=215; // centro horizontal do viewBox (largura 430)
// Cada modelo tem sua silhueta própria: Regular (mais justa e comprida),
// Oversized (ombro caído, mais larga, mangas longas), Baby Look (corte
// feminino, mais justo no corpo). Isso só entra em cena se não houver
// foto cadastrada pra aquela combinação modelo+cor+lado — com as fotos
// reais que você mandou, na prática isso vira só um "reserva".
const SHIRT_PARAMS={
  regular:{collarHalfW:24,neckY:14,neckDip:22,shoulderHalfW:78,shoulderY:28,sleeveTopHalfW:112,sleeveTopY:48,sleeveTipOuterHalfW:126,sleeveTipY:88,sleeveTipInnerHalfW:100,sleeveTipInnerY:108,underarmHalfW:88,underarmY:118,hemTopHalfW:90,hemTopY:140,hemBottomHalfW:84,hemY:505,radius:10},
  oversized:{collarHalfW:26,neckY:16,neckDip:20,shoulderHalfW:92,shoulderY:34,sleeveTopHalfW:140,sleeveTopY:60,sleeveTipOuterHalfW:158,sleeveTipY:110,sleeveTipInnerHalfW:128,sleeveTipInnerY:132,underarmHalfW:108,underarmY:148,hemTopHalfW:112,hemTopY:170,hemBottomHalfW:110,hemY:505,radius:14},
  babylook:{collarHalfW:22,neckY:14,neckDip:18,shoulderHalfW:70,shoulderY:26,sleeveTopHalfW:98,sleeveTopY:44,sleeveTipOuterHalfW:108,sleeveTipY:76,sleeveTipInnerHalfW:86,sleeveTipInnerY:92,underarmHalfW:74,underarmY:100,hemTopHalfW:70,hemTopY:280,hemBottomHalfW:62,hemY:470,radius:10}
};
function shirtPoints(p){
  const right=[
    {x:p.collarHalfW,y:p.neckY},
    {x:p.shoulderHalfW,y:p.shoulderY},
    {x:p.sleeveTopHalfW,y:p.sleeveTopY},
    {x:p.sleeveTipOuterHalfW,y:p.sleeveTipY},
    {x:p.sleeveTipInnerHalfW,y:p.sleeveTipInnerY},
    {x:p.underarmHalfW,y:p.underarmY},
    {x:p.hemTopHalfW,y:p.hemTopY},
    {x:p.hemBottomHalfW,y:p.hemY}
  ];
  const left=[...right].reverse().map(pt=>({x:-pt.x,y:pt.y}));
  return [...right,...left,{x:-p.collarHalfW,y:p.neckY},{x:0,y:p.neckY+p.neckDip}];
}
function roundedPolygonPath(points,radius){
  const n=points.length, d=[];
  const sub=(a,b)=>({x:a.x-b.x,y:a.y-b.y});
  const len=v=>Math.hypot(v.x,v.y);
  const norm=v=>{const l=len(v)||1;return{x:v.x/l,y:v.y/l}};
  for(let i=0;i<n;i++){
    const cur={x:CX+points[i].x,y:points[i].y};
    const prev={x:CX+points[(i-1+n)%n].x,y:points[(i-1+n)%n].y};
    const next={x:CX+points[(i+1)%n].x,y:points[(i+1)%n].y};
    const rIn=Math.min(radius,len(sub(cur,prev))/2), rOut=Math.min(radius,len(sub(next,cur))/2);
    const nIn=norm(sub(prev,cur)), nOut=norm(sub(next,cur));
    const p1={x:cur.x+nIn.x*rIn,y:cur.y+nIn.y*rIn};
    const p2={x:cur.x+nOut.x*rOut,y:cur.y+nOut.y*rOut};
    d.push(i===0?`M ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`:`L ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`);
    d.push(`Q ${cur.x.toFixed(1)} ${cur.y.toFixed(1)} ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`);
  }
  d.push('Z');
  return d.join(' ');
}
function updateShirtSvg(){
  $('#shirtSvg').innerHTML=shirtSvgMarkup(currentModel||'regular',$('#shirtColor').value||'Preto');
}
function shirtSvgMarkup(modelId,colorName){
  const base=SHIRT_COLOR_HEX[colorName]||'#161616';
  const light=shadeColor(base,32), dark=shadeColor(base,-26), deep=shadeColor(base,-42), seam=shadeColor(base,-48);
  const p=SHIRT_PARAMS[modelId]||SHIRT_PARAMS.regular;
  const path=roundedPolygonPath(shirtPoints(p),p.radius);
  const gid='shirtGrad_'+modelId;
  return `
    <defs>
      <linearGradient id="${gid}_body" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stop-color="${deep}"/>
        <stop offset="14%" stop-color="${light}"/>
        <stop offset="46%" stop-color="${base}"/>
        <stop offset="54%" stop-color="${base}"/>
        <stop offset="86%" stop-color="${light}"/>
        <stop offset="100%" stop-color="${deep}"/>
      </linearGradient>
      <radialGradient id="${gid}_chest" cx="50%" cy="22%" r="55%">
        <stop offset="0%" stop-color="${light}" stop-opacity=".5"/>
        <stop offset="100%" stop-color="${light}" stop-opacity="0"/>
      </radialGradient>
      <linearGradient id="${gid}_shadow" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${dark}" stop-opacity="0"/>
        <stop offset="100%" stop-color="${dark}" stop-opacity=".35"/>
      </linearGradient>
      <filter id="${gid}_fabric" x="-10%" y="-10%" width="120%" height="120%">
        <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" result="noise"/>
        <feColorMatrix in="noise" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .05 0"/>
      </filter>
    </defs>
    <path d="${path}" fill="url(#${gid}_body)" stroke="${seam}" stroke-width="1.6"/>
    <path d="${path}" fill="url(#${gid}_chest)"/>
    <path d="${path}" fill="url(#${gid}_shadow)"/>
    <path d="${path}" filter="url(#${gid}_fabric)" opacity=".5"/>
    <path d="M ${(CX-p.collarHalfW*0.82).toFixed(1)} ${(p.neckY+p.neckDip*0.35).toFixed(1)} Q ${CX} ${(p.neckY+p.neckDip+6).toFixed(1)} ${(CX+p.collarHalfW*0.82).toFixed(1)} ${(p.neckY+p.neckDip*0.35).toFixed(1)}"
          fill="none" stroke="${seam}" stroke-width="2.4" opacity=".7"/>
  `;
}
function shadeColor(hex,amt){
  const n=parseInt(hex.slice(1),16);
  let r=(n>>16)+amt, g=(n>>8&255)+amt, b=(n&255)+amt;
  r=Math.max(0,Math.min(255,r));g=Math.max(0,Math.min(255,g));b=Math.max(0,Math.min(255,b));
  return '#'+(1<<24|r<<16|g<<8|b).toString(16).slice(1);
}

/* ---------- Foto real de mockup (quando cadastrada no Admin) ---------- */
async function loadShirtMockups(){
  try{ const r=await fetch('/api/shirt-mockups'); shirtMockups=r.ok?await r.json():[]; }
  catch{ shirtMockups=[]; }
}
function findMockup(modelId,color,side){
  return shirtMockups.find(m=>m.model_id===modelId && m.color===color && m.side===side) || null;
}
function selectSide(side){
  currentSide=side;
  elements=sidesElements[currentSide];
  selected=null;
  $$('#sideTabs [data-side]').forEach(x=>x.classList.toggle('active',x.dataset.side===side));
  const info=$('#sleeveInfo');
  if(!info){
    // Página antiga em cache sem o aviso das mangas: não trava as abas.
  }else if(isSleeve(side)){
    info.hidden=false;
    info.innerHTML=`<b>${SLEEVE_SIDES[side]}</b> · adicione aqui o DTF dessa manga. Área máxima ${SLEEVE_PRINT_W_CM.toFixed(0)} × ${SLEEVE_PRINT_H_CM.toFixed(1).replace('.',',')} cm, e a arte sai no tamanho real mostrado na tela.`;
  }else{
    info.hidden=true;
  }
  updateShirtVisual();render();
}
function sizeLimit(size){
  const all=(CONFIG&&CONFIG.size_print_limits)||DEFAULT_SIZE_LIMITS;
  return all[size]||DEFAULT_SIZE_LIMITS[size]||null;
}
// Retângulo (em unidades do canvas 420x480) onde a arte pode ficar. Nas
// mangas e sem tamanho escolhido, é o canvas inteiro.
// A arte pode ocupar toda a área vermelha. O limite de cada tamanho é
// conferido pelo aviso de cima e na hora de criar o pedido.
function limitRect(){
  return {x:0,y:0,w:420,h:480};
}
// Medida em cm do conjunto de artes de um lado (frente ou verso).
function sideArtSizeCm(side){
  const list=sidesElements[side]||[];
  if(!list.length) return null;
  const bs=list.map(bounds);
  const w=Math.max(...bs.map(b=>b.maxX))-Math.min(...bs.map(b=>b.minX));
  const h=Math.max(...bs.map(b=>b.maxY))-Math.min(...bs.map(b=>b.minY));
  return {w:w*cmPerUnitX(side),h:h*cmPerUnitY(side)};
}
function artFitsSize(side,size){
  const art=sideArtSizeCm(side), lim=sizeLimit(size);
  if(!art||!lim) return true;
  return art.w<=lim.w+0.05 && art.h<=lim.h+0.05;
}
function selectSize(size){
  currentSize=size;
  $$('#sizeGrid [data-pick]').forEach(c=>c.classList.toggle('active',c.dataset.pick===size));
  render();
}
const fmtCm=v=>Number(v).toFixed(1).replace('.0','').replace('.',',');
function updateLimitLabels(lim){
  const lw=$('#limitLabelW'), lh=$('#limitLabelH');
  if(!lw||!lh) return;
  const show=!!lim && !isSleeve(currentSide);
  lw.hidden=lh.hidden=!show;
  if(!show) return;
  lw.textContent=`${currentSize} · largura máx. ${fmtCm(lim.w)} cm`;
  lh.textContent=`altura máx. ${fmtCm(lim.h)} cm`;
}
function updateSizeInfo(){
  const box=$('#sizeInfo');
  const lim=currentSize?sizeLimit(currentSize):null;
  updateLimitLabels(lim);
  if(!box) return;
  if(!lim || isSleeve(currentSide)){ box.hidden=true; return; }
  const fits=artFitsSize(currentSide,currentSize);
  const art=sideArtSizeCm(currentSide);
  box.hidden=false;
  box.classList.toggle('over',!fits);
  box.innerHTML=`<b>Tamanho ${esc(currentSize)}</b> · área máxima de impressão ${fmtCm(lim.w)} × ${fmtCm(lim.h)} cm`
    +(art?` · sua arte: ${fmtCm(art.w)} × ${fmtCm(art.h)} cm`:'')
    +(fits?'':' · <b>a arte passa do limite deste tamanho, diminua ela.</b>');
}
function updateSideTabs(){
  $$('#sideTabs [data-side]').forEach(b=>b.classList.toggle('has-art',(sidesElements[b.dataset.side]||[]).length>0));
}
function updateSleeveSvg(){
  const colorName=$('#shirtColor').value||'Preto';
  const base=SHIRT_COLOR_HEX[colorName]||'#161616';
  const light=shadeColor(base,32), deep=shadeColor(base,-42), seam=shadeColor(base,-48);
  const label=SLEEVE_SIDES[currentSide]||'';
  // Manga aberta vista de lado: copa arredondada em cima (onde costura no
  // ombro), laterais afinando até a barra. A esquerda é o espelho da direita.
  const flip=currentSide==='SLEEVE_L'?'transform="translate(430,0) scale(-1,1)"':'';
  const gid='sleeveGrad';
  $('#shirtSvg').innerHTML=`
    <defs>
      <linearGradient id="${gid}" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stop-color="${deep}"/>
        <stop offset="18%" stop-color="${light}"/>
        <stop offset="50%" stop-color="${base}"/>
        <stop offset="82%" stop-color="${light}"/>
        <stop offset="100%" stop-color="${deep}"/>
      </linearGradient>
    </defs>
    <g ${flip}>
      <path d="M 70 150 C 95 70, 230 20, 360 150 L 330 470 L 100 470 Z" fill="url(#${gid})" stroke="${seam}" stroke-width="1.6"/>
      <path d="M 78 158 C 103 82, 230 34, 352 158" fill="none" stroke="${seam}" stroke-width="1.4" stroke-dasharray="5 4" opacity=".7"/>
      <path d="M 102 440 L 328 440" fill="none" stroke="${seam}" stroke-width="1.4" stroke-dasharray="5 4" opacity=".7"/>
    </g>
    <text x="215" y="505" text-anchor="middle" font-family="Arial" font-size="15" font-weight="800" fill="#555">${label.toUpperCase()}</text>
  `;
}
function updateShirtVisual(){
  const modelId=currentModel||'regular';
  const colorName=$('#shirtColor').value||'Preto';
  const photo=$('#shirtPhoto'), svg=$('#shirtSvg');
  if(isSleeve(currentSide)){
    photo.style.display='none';
    svg.style.display='block';
    updateSleeveSvg();
    positionPrintAreaOnSvg();
    return;
  }
  const mock=findMockup(modelId,colorName,currentSide);
  if(mock){
    svg.style.display='none';
    photo.style.display='block';
    if(photo.dataset.url!==mock.preview_url){
      photo.dataset.url=mock.preview_url;
      photo.src=mock.preview_url;
    }
    photo.dataset.printX=mock.print_x; photo.dataset.printY=mock.print_y;
    photo.dataset.printW=mock.print_w; photo.dataset.printH=mock.print_h;
    photo.dataset.mockupId=mock.id;
    if(photo.complete) positionPrintAreaOnPhoto();
    else photo.onload=positionPrintAreaOnPhoto;
  }else{
    photo.style.display='none';
    svg.style.display='block';
    updateShirtSvg();
    positionPrintAreaOnSvg();
  }
}
// Área de impressão no desenho da camisa (frente/verso), em fração do viewBox 430x520.
function svgPrintRect(modelId){
  const p=SHIRT_PARAMS[modelId]||SHIRT_PARAMS.regular;
  const bottomMargin=modelId==='babylook'?35:50;
  const halfW=p.underarmHalfW*0.85, topY=p.underarmY+25, botY=p.hemY-bottomMargin;
  return {px:(CX-halfW)/430, py:topY/520, pw:(halfW*2)/430, ph:(botY-topY)/520};
}
function positionPrintAreaOnSvg(){
  const svg=$('#shirtSvg');
  if(svg.style.display==='none')return;
  let px,py,pw,ph;
  if(isSleeve(currentSide)){
    // Retângulo de 180x206 no centro da manga (mesma proporção do canvas).
    px=125/430; py=175/520; pw=180/430; ph=206/520;
  }else{
    ({px,py,pw,ph}=svgPrintRect(currentModel));
  }
  const stage=svg.closest('.shirt-stage');
  const sRect=svg.getBoundingClientRect(), stRect=stage.getBoundingClientRect();
  if(!stRect.width) return; // editor escondido: calcula quando ele aparecer
  const left=sRect.left-stRect.left, top=sRect.top-stRect.top;
  const area=$('#printArea');
  area.style.position='absolute';
  area.style.transform='none';
  area.style.left=(left+sRect.width*px)+'px';
  area.style.top=(top+sRect.height*py)+'px';
  area.style.width=(sRect.width*pw)+'px';
  area.style.height=(sRect.height*ph)+'px';
  positionShirtHandles();
}
function positionPrintAreaOnPhoto(){
  const photo=$('#shirtPhoto');
  if(photo.style.display==='none' || !photo.dataset.printX) return;
  const stage=photo.closest('.shirt-stage');
  const pRect=photo.getBoundingClientRect(), sRect=stage.getBoundingClientRect();
  if(!sRect.width) return; // editor escondido: calcula quando ele aparecer
  const left=pRect.left-sRect.left, top=pRect.top-sRect.top;
  const px=Number(photo.dataset.printX)/100, py=Number(photo.dataset.printY)/100;
  const pw=Number(photo.dataset.printW)/100, ph=Number(photo.dataset.printH)/100;
  const area=$('#printArea');
  area.style.position='absolute';
  area.style.transform='none';
  area.style.left=(left+pRect.width*px)+'px';
  area.style.top=(top+pRect.height*py)+'px';
  area.style.width=(pRect.width*pw)+'px';
  area.style.height=(pRect.height*ph)+'px';
  positionShirtHandles();
}
// Só um dos dois está visível; o outro retorna na hora.
function positionPrintArea(){positionPrintAreaOnPhoto();positionPrintAreaOnSvg()}
let printAreaDrag=null, printAreaSaveTimer=null;
function bindPrintAreaDrag(){
  const move=$('#printAreaMove'), resize=$('#printAreaResize'), area=$('#printArea');
  // Pelo teclado (admin): setas movem ou redimensionam a área e salvam meio segundo depois.
  const keyAdjust=mode=>ev=>{
    const d={ArrowRight:[1,0],ArrowLeft:[-1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[ev.key];
    if(!d) return;
    ev.preventDefault();
    const s=ev.shiftKey?10:2;
    area.style.position='absolute'; area.style.transform='none';
    if(mode==='move'){ area.style.left=(parseFloat(area.style.left)||0)+d[0]*s+'px'; area.style.top=(parseFloat(area.style.top)||0)+d[1]*s+'px'; }
    else{ area.style.width=Math.max(20,(parseFloat(area.style.width)||0)+d[0]*s)+'px'; area.style.height=Math.max(20,(parseFloat(area.style.height)||0)+d[1]*s)+'px'; }
    positionShirtHandles();
    clearTimeout(printAreaSaveTimer); printAreaSaveTimer=setTimeout(savePrintAreaFromPixels,500);
    announce(`Área de impressão: ${Math.round(parseFloat(area.style.width))} por ${Math.round(parseFloat(area.style.height))} pixels na tela.`);
  };
  move.onkeydown=keyAdjust('move'); resize.onkeydown=keyAdjust('resize');
  move.onpointerdown=ev=>{
    ev.preventDefault();ev.stopPropagation();
    printAreaDrag={mode:'move',startX:ev.clientX,startY:ev.clientY,
      x:parseFloat(area.style.left)||0,y:parseFloat(area.style.top)||0,
      w:parseFloat(area.style.width)||0,h:parseFloat(area.style.height)||0};
    move.setPointerCapture(ev.pointerId);
  };
  resize.onpointerdown=ev=>{
    ev.preventDefault();ev.stopPropagation();
    printAreaDrag={mode:'resize',startX:ev.clientX,startY:ev.clientY,
      x:parseFloat(area.style.left)||0,y:parseFloat(area.style.top)||0,
      w:parseFloat(area.style.width)||0,h:parseFloat(area.style.height)||0};
    resize.setPointerCapture(ev.pointerId);
  };
  window.addEventListener('pointermove',ev=>{
    if(!printAreaDrag)return;
    const dx=ev.clientX-printAreaDrag.startX, dy=ev.clientY-printAreaDrag.startY;
    area.style.position='absolute'; area.style.transform='none';
    if(printAreaDrag.mode==='move'){
      area.style.left=(printAreaDrag.x+dx)+'px';
      area.style.top=(printAreaDrag.y+dy)+'px';
    }else{
      area.style.width=Math.max(20,printAreaDrag.w+dx)+'px';
      area.style.height=Math.max(20,printAreaDrag.h+dy)+'px';
    }
    positionShirtHandles();
  });
  window.addEventListener('pointerup',()=>{
    if(printAreaDrag) savePrintAreaFromPixels();
    printAreaDrag=null;
  });
}
async function savePrintAreaFromPixels(){
  const photo=$('#shirtPhoto'), area=$('#printArea');
  if(photo.style.display==='none' || !photo.dataset.mockupId) return;
  const stage=photo.closest('.shirt-stage');
  const pRect=photo.getBoundingClientRect(), sRect=stage.getBoundingClientRect();
  if(!pRect.width||!pRect.height) return; // foto sem tamanho ainda: a conta daria NaN
  const left=pRect.left-sRect.left, top=pRect.top-sRect.top;
  const areaLeft=parseFloat(area.style.left), areaTop=parseFloat(area.style.top);
  const areaW=parseFloat(area.style.width), areaH=parseFloat(area.style.height);
  // Arrastar a área pra fora da foto dava valor fora de 0–100: o servidor
  // recusava e o admin era deslogado. Agora a área fica presa na foto.
  const pct=v=>Math.min(100,Math.max(0,v));
  const px=pct((areaLeft-left)/pRect.width*100), py=pct((areaTop-top)/pRect.height*100);
  const pw=pct(areaW/pRect.width*100), ph=pct(areaH/pRect.height*100);
  photo.dataset.printX=px; photo.dataset.printY=py;
  photo.dataset.printW=pw; photo.dataset.printH=ph;
  positionPrintAreaOnPhoto();
  const id=photo.dataset.mockupId;
  const mockRecord=shirtMockups.find(m=>m.id===id);
  if(mockRecord){mockRecord.print_x=px;mockRecord.print_y=py;mockRecord.print_w=pw;mockRecord.print_h=ph;}
  const body=JSON.stringify({print_x:px,print_y:py,print_w:pw,print_h:ph});
  try{
    const r=await fetch(`/api/admin/shirt-mockups/${id}/area`,{method:'PATCH',headers:{'Content-Type':'application/json',...adminHeaders()},body});
    if(r.status===401){clearAdmin();alert('A área de impressão não foi salva. Entre no Admin de novo.');}
    else if(!r.ok){const d=await r.json().catch(()=>({}));alert('A área de impressão não foi salva: '+(d.detail||'erro no servidor.'));}
  }catch(e){ alert('A área de impressão não foi salva: sem conexão com o servidor.'); }
}

function previewMockupPhoto(e){
  const f=e.target.files[0]; if(!f)return;
  $('#mockupPreviewImg').src=URL.createObjectURL(f);
  $('#mockupPreviewWrap').style.display='block';
  updateMockupPreviewBox();
}
function updateMockupPreviewBox(){
  const box=$('#mockupPreviewBox');
  box.style.left=$('#mockupX').value+'%';
  box.style.top=$('#mockupY').value+'%';
  box.style.width=$('#mockupW').value+'%';
  box.style.height=$('#mockupH').value+'%';
}
let mockupDrag=null;
function bindMockupBoxDrag(){
  const box=$('#mockupPreviewBox'), handle=$('#mockupBoxResize');
  box.onpointerdown=ev=>{
    if(ev.target===handle)return;
    ev.preventDefault();
    mockupDrag={mode:'move',startX:ev.clientX,startY:ev.clientY,
      x:Number($('#mockupX').value),y:Number($('#mockupY').value),
      w:Number($('#mockupW').value),h:Number($('#mockupH').value)};
    box.setPointerCapture(ev.pointerId);
  };
  handle.onpointerdown=ev=>{
    ev.preventDefault();ev.stopPropagation();
    mockupDrag={mode:'resize',startX:ev.clientX,startY:ev.clientY,
      x:Number($('#mockupX').value),y:Number($('#mockupY').value),
      w:Number($('#mockupW').value),h:Number($('#mockupH').value)};
    handle.setPointerCapture(ev.pointerId);
  };
  window.addEventListener('pointermove',ev=>{
    if(!mockupDrag)return;
    const rect=$('#mockupPreviewWrap').getBoundingClientRect();
    const dxPct=(ev.clientX-mockupDrag.startX)/rect.width*100;
    const dyPct=(ev.clientY-mockupDrag.startY)/rect.height*100;
    if(mockupDrag.mode==='move'){
      const nx=Math.min(Math.max(0,mockupDrag.x+dxPct),100-mockupDrag.w);
      const ny=Math.min(Math.max(0,mockupDrag.y+dyPct),100-mockupDrag.h);
      $('#mockupX').value=nx.toFixed(1); $('#mockupY').value=ny.toFixed(1);
    }else{
      const nw=Math.min(Math.max(2,mockupDrag.w+dxPct),100-mockupDrag.x);
      const nh=Math.min(Math.max(2,mockupDrag.h+dyPct),100-mockupDrag.y);
      $('#mockupW').value=nw.toFixed(1); $('#mockupH').value=nh.toFixed(1);
    }
    updateMockupPreviewBox();
  });
  window.addEventListener('pointerup',()=>{mockupDrag=null});
}
async function uploadMockup(ev){
  ev.preventDefault();
  if(!adminKey) return stat('#adminMockupStatus','Entre no Admin com a chave primeiro.',false);
  const form=ev.currentTarget; // depois do primeiro await, ev.currentTarget vira null
  const fd=new FormData(form);
  let r,d;
  try{ r=await fetch('/api/admin/shirt-mockups',{method:'POST',headers:adminHeaders(),body:fd}); d=await r.json().catch(()=>({})); }
  catch{ return stat('#adminMockupStatus','Sem conexão com o servidor.',false); }
  if(r.status===401){clearAdmin();return stat('#adminMockupStatus','Chave de admin inválida.',false)}
  if(!r.ok) return stat('#adminMockupStatus',uploadError(r,d),false);
  stat('#adminMockupStatus','Mockup salvo.',true);
  form.reset();
  $('#mockupPreviewWrap').style.display='none';
  await loadShirtMockups();
  renderMockupsList();
  updateShirtVisual();
}
async function loadArtworks(){
  // Falhou? Fica sem o catálogo, mas o resto do site abre (antes derrubava o init).
  try{ const r=await fetch('/api/artworks'); artworks=r.ok?await r.json().catch(()=>({})):[]; }catch{ artworks=[]; }
  renderCatalogs();
}
function renderCatalogs(){
  const custom=artworks.filter(a=>a.can_customize);
  // Destaques do catálogo do Admin (até 6). Sem nenhum, a caixa mostra só o "Explorar artes".
  const miniHtml=custom.slice(0,6).map(a=>`<button type="button" data-use="${a.id}" title="${esc(a.name)}"><img src="${a.preview_url}" alt="${esc(a.name)}"></button>`).join('');
  $('#catalogMini').innerHTML=miniHtml; $('#catalogMini').hidden=!miniHtml;
  $('#gangCatalogMini').innerHTML=miniHtml; $('#gangCatalogMini').hidden=!miniHtml;
  $$('#catalogMini [data-use]').forEach(b=>b.onclick=()=>useCatalog(b.dataset.use));
  $$('#gangCatalogMini [data-use]').forEach(b=>b.onclick=()=>useGangCatalog(b.dataset.use));}
async function useCatalog(id){const a=artworks.find(x=>x.id===id);if(a)await addImage(a.preview_url,'CATALOGO',a.id)}
// Põe uma imagem no lado ativo da camisa, centralizada e já selecionada.
function addImage(url,origin,artworkId=null){
  return new Promise((res,rej)=>{
    const img=new Image();
    img.crossOrigin='anonymous';
    img.onload=()=>{
      // Escala inicial: na manga a arte ocupa quase tudo; na frente/verso começa pequena.
      const base=isSleeve(currentSide)?Math.min(300/img.width,340/img.height):Math.min(120/img.width,230/img.height,1);
      const lr=limitRect();
      selected={id:crypto.randomUUID(),type:'image',img,url,origin,artworkId,
        x:lr.x+lr.w/2,y:lr.y+lr.h/2,scaleX:base,scaleY:base,rotation:0,w:img.width,h:img.height};
      clampResizeToCanvas(selected);
      clampDragToCanvas(selected);
      elements.push(selected);
      render();
      res(selected);
    };
    img.onerror=()=>rej(new Error('Não foi possível carregar essa imagem.'));
    img.src=url;
  });
}
function draw(e,c=ctx){c.save();c.translate(e.x,e.y);c.rotate(e.rotation*Math.PI/180);if(e.type==='image'){c.scale(e.scaleX,e.scaleY);c.drawImage(e.img,-e.w/2,-e.h/2,e.w,e.h)}else{c.scale(e.scale,e.scale);c.fillStyle=e.color;c.font=`900 ${e.fontSize}px Arial`;c.textAlign='center';c.textBaseline='middle';c.fillText(e.text,0,0)}c.restore()}
function size(e){if(e.type==='image')return{w:e.w*e.scaleX,h:e.h*e.scaleY};ctx.font=`900 ${e.fontSize}px Arial`;return{w:ctx.measureText(e.text).width*e.scale,h:e.fontSize*1.2*e.scale}}
function toLocal(e,x,y){const dx=x-e.x,dy=y-e.y,a=-e.rotation*Math.PI/180;return{x:dx*Math.cos(a)-dy*Math.sin(a),y:dx*Math.sin(a)+dy*Math.cos(a)}}
function cmPerUnitX(side=currentSide){return isSleeve(side)?SLEEVE_PRINT_W_CM/420:(CONFIG.max_print_width_cm||60)/420}
// Mesma escala na vertical e na horizontal: a arte não deforma e a etiqueta
// em cm bate com o tamanho do arquivo de produção.
function cmPerUnitY(side=currentSide){return cmPerUnitX(side)}
function render(){
  ctx.clearRect(0,0,420,480);
  elements.forEach(e=>draw(e));
  if(selected){
    const s=size(selected);
    ctx.save();ctx.translate(selected.x,selected.y);ctx.rotate(selected.rotation*Math.PI/180);ctx.strokeStyle='#ff4b18';ctx.setLineDash([6,4]);ctx.lineWidth=2;ctx.strokeRect(-s.w/2,-s.h/2,s.w,s.h);ctx.restore();
    const b=bounds(selected);
    drawDimBadge(ctx,(s.w*cmPerUnitX()).toFixed(1)+'cm',(b.minX+b.maxX)/2,b.minY-9,false);
    drawDimBadge(ctx,(s.h*cmPerUnitY()).toFixed(1)+'cm',b.minX-9,(b.minY+b.maxY)/2,true);
  }
  positionShirtHandles();
  updateSideTabs();
  updateSizeInfo();
}
function hit(e,x,y){const l=toLocal(e,x,y),s=size(e);return Math.abs(l.x)<=s.w/2&&Math.abs(l.y)<=s.h/2}
function pos(ev){const r=canvas.getBoundingClientRect();return{x:(ev.clientX-r.left)*420/r.width,y:(ev.clientY-r.top)*480/r.height}}
function pointerDown(ev){const p=pos(ev),h=[...elements].reverse().find(e=>hit(e,p.x,p.y));selected=h||null;if(h){dragging={e:h,dx:p.x-h.x,dy:p.y-h.y};canvas.setPointerCapture(ev.pointerId)}render()}
function clampDragToCanvas(e){
  const r=limitRect(), b=bounds(e);
  if(b.minX<r.x) e.x+=(r.x-b.minX);
  else if(b.maxX>r.x+r.w) e.x-=(b.maxX-(r.x+r.w));
  if(b.minY<r.y) e.y+=(r.y-b.minY);
  else if(b.maxY>r.y+r.h) e.y-=(b.maxY-(r.y+r.h));
}
function clampResizeToCanvas(e){
  const b=bounds(e);
  const leftExtent=e.x-b.minX, rightExtent=b.maxX-e.x, topExtent=e.y-b.minY, bottomExtent=b.maxY-e.y;
  const r=limitRect();
  let ratio=1;
  if(leftExtent>0) ratio=Math.min(ratio, (e.x-r.x)/leftExtent);
  if(rightExtent>0) ratio=Math.min(ratio, (r.x+r.w-e.x)/rightExtent);
  if(topExtent>0) ratio=Math.min(ratio, (e.y-r.y)/topExtent);
  if(bottomExtent>0) ratio=Math.min(ratio, (r.y+r.h-e.y)/bottomExtent);
  ratio=Math.max(ratio,0.02);
  if(ratio<1){ e.scaleX*=ratio; e.scaleY*=ratio; }
}
function pointerMove(ev){
  const p=pos(ev);
  if(shirtResize){
    const e=shirtResize.piece,l=toLocal(e,p.x,p.y);
    e.scaleX=Math.max(6,Math.abs(l.x))*2/e.w;
    e.scaleY=Math.max(6,Math.abs(l.y))*2/e.h;
    clampResizeToCanvas(e);
    render();
    return;
  }
  if(!dragging)return;
  dragging.e.x=p.x-dragging.dx;dragging.e.y=p.y-dragging.dy;
  clampDragToCanvas(dragging.e);
  render();
}
/* ---------- Teclado e leitor de tela (editor da camiseta) ---------- */
// O mouse arrasta e estica; pelo teclado dá pra fazer tudo com o canvas em foco.
// Cada ação é anunciada na região #a11yLive (leitor de tela).
function announce(msg){const live=$('#a11yLive'); if(!live) return; live.textContent=''; setTimeout(()=>{live.textContent=msg},30);}
function describeSelected(){
  if(!selected) return elements.length?`${elements.length} ${elements.length===1?'arte':'artes'} neste lado. Aperte N para escolher.`:'Nenhuma arte neste lado ainda.';
  const s=size(selected), i=elements.indexOf(selected)+1;
  return `Arte ${i} de ${elements.length}: ${fmtCm(s.w*cmPerUnitX())} por ${fmtCm(s.h*cmPerUnitY())} cm${selected.rotation?`, girada ${selected.rotation} graus`:''}.`;
}
function shirtKeydown(ev){
  const k=ev.key, step=ev.shiftKey?10:2;
  if(k==='n'||k==='N'){
    if(!elements.length){ announce(describeSelected()); return; }
    const i=elements.indexOf(selected), n=elements.length;
    selected=elements[ev.shiftKey?(i-1+n)%n:(i+1)%n]; render(); announce(describeSelected()); ev.preventDefault(); return;
  }
  if(!selected){ if(k.startsWith('Arrow')||k==='+'||k==='-'){ announce(describeSelected()); ev.preventDefault(); } return; }
  const moves={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]};
  if(moves[k]){ selected.x+=moves[k][0]; selected.y+=moves[k][1]; clampDragToCanvas(selected); }
  else if(k==='+'||k==='='||k==='-'||k==='_'){
    const f=(k==='-'||k==='_')?(ev.shiftKey?0.8:0.95):(ev.shiftKey?1.2:1.05);
    selected.scaleX*=f; selected.scaleY*=f; clampResizeToCanvas(selected); clampDragToCanvas(selected);
  }
  else if(k==='r'||k==='R') $('#shirtHandleRotate').onclick();
  else if(k==='c'||k==='C') $('#centerSelected').onclick();
  else if(k==='Delete'||k==='Backspace'){ $('#deleteSelected').onclick(); announce('Arte removida. '+describeSelected()); ev.preventDefault(); return; }
  else if(k==='Escape'){ selected=null; render(); announce('Nenhuma arte selecionada.'); return; }
  else return;
  ev.preventDefault(); render(); announce(describeSelected());
}
function positionShirtHandles(){
  const wrap=$('#shirtHandles');
  if(!selected){ wrap.hidden=true; return; }
  wrap.hidden=false;
  const area=$('#printArea');
  const sx=area.offsetWidth/420, sy=area.offsetHeight/480;
  const s=size(selected), hw=s.w/2, hh=s.h/2, a=selected.rotation*Math.PI/180;
  const toScreen=(lx,ly)=>({x:(selected.x+lx*Math.cos(a)-ly*Math.sin(a))*sx, y:(selected.y+lx*Math.sin(a)+ly*Math.cos(a))*sy});
  const bl=toScreen(-hw,hh), br=toScreen(hw,hh);
  $('#shirtHandleRotate').style.left=bl.x+'px'; $('#shirtHandleRotate').style.top=bl.y+'px';
  $('#shirtHandleResize').style.left=br.x+'px'; $('#shirtHandleResize').style.top=br.y+'px';
}
// Geração por IA (camisa e DTF). Só com conta: cada geração custa na OpenAI.
async function requestAI(prompt){
  const r=await fetch('/api/ai/generate',{method:'POST',headers:{...customerHeaders(),'Content-Type':'application/json'},body:JSON.stringify({prompt})});
  const d=await r.json().catch(()=>({}));
  if(r.status===401){ logoutCustomer(); openAuthModal(); }
  if(!r.ok) throw new Error(d.detail||'Erro');
  return d;
}
async function generateAI(){
  const prompt=$('#aiPrompt').value.trim();if(!prompt)return;
  if(!currentCustomer){$('#aiStatus').textContent='Entre na sua conta para gerar com IA.';return openAuthModal()}
  const btn=$('#generateAI');btn.disabled=true; // cada clique é uma geração paga
  $('#aiStatus').textContent='Gerando...';
  try{const d=await requestAI(prompt);await addImage(d.url,'IA');$('#aiStatus').textContent=`Gerada com ${d.model}`}
  catch(e){$('#aiStatus').textContent=e.message}
  finally{btn.disabled=false}
}
function bounds(e){const s=size(e),a=e.rotation*Math.PI/180,hw=s.w/2,hh=s.h/2,pts=[[-hw,-hh],[hw,-hh],[hw,hh],[-hw,hh]].map(([x,y])=>({x:e.x+x*Math.cos(a)-y*Math.sin(a),y:e.y+x*Math.sin(a)+y*Math.cos(a)}));return{minX:Math.min(...pts.map(p=>p.x)),maxX:Math.max(...pts.map(p=>p.x)),minY:Math.min(...pts.map(p=>p.y)),maxY:Math.max(...pts.map(p=>p.y))}}
async function exportProductionFor(list,side='FRONT'){
  if(!list.length) return null;
  const bs=list.map(bounds);
  // Recorte justo na arte, sem margem: o arquivo sai com a medida da etiqueta.
  let minX=Math.max(0,Math.min(...bs.map(b=>b.minX))),maxX=Math.min(420,Math.max(...bs.map(b=>b.maxX))),
      minY=Math.max(0,Math.min(...bs.map(b=>b.minY))),maxY=Math.min(480,Math.max(...bs.map(b=>b.maxY)));
  const pw=maxX-minX,ph=maxY-minY,
      // Tamanho real do que está na tela, o mesmo que aparece nas etiquetas em cm.
      wcm=pw*cmPerUnitX(side),
      hcm=wcm*(ph/pw),dpi=CONFIG.dpi,ow=Math.round(wcm/2.54*dpi),oh=Math.round(hcm/2.54*dpi);
  const out=document.createElement('canvas');
  out.width=ow;out.height=oh;
  const ox=out.getContext('2d'),sx=ow/pw,sy=oh/ph;
  list.forEach(e=>{
    ox.save();ox.translate((e.x-minX)*sx,(e.y-minY)*sy);ox.rotate(e.rotation*Math.PI/180);
    if(e.type==='image'){ox.scale(e.scaleX*sx,e.scaleY*sy);ox.drawImage(e.img,-e.w/2,-e.h/2,e.w,e.h)}
    else{ox.scale(e.scale*sx,e.scale*sy);ox.fillStyle=e.color;ox.font=`900 ${e.fontSize}px Arial`;ox.textAlign='center';ox.textBaseline='middle';ox.fillText(e.text,0,0)}
    ox.restore();
  });
  const blob=await new Promise(r=>out.toBlob(r,'image/png'));
  if(!blob) throw new Error('A arte ficou grande demais pra gerar o arquivo neste navegador. Diminua a arte e tente de novo.');
  return {blob,widthCm:Number(wcm.toFixed(2)),heightCm:Number(hcm.toFixed(2)),widthPx:ow,heightPx:oh,dpi};
}
function loadImg(src){return new Promise((res,rej)=>{const img=new Image();img.onload=()=>res(img);img.onerror=()=>rej(new Error('Não foi possível carregar a foto da camisa.'));img.src=src})}
// Foto de referência pra quem vai estampar: a camisa (foto cadastrada no
// Admin ou, sem ela, o desenho) com a arte do lado no lugar exato em que o
// cliente deixou, marcada com a medida do arquivo de produção.
async function exportPlacementFor(side,art){
  const list=sidesElements[side];
  if(!list.length||!art) return null;
  const modelId=currentModel||'regular', color=$('#shirtColor').value||'Preto';
  const mock=findMockup(modelId,color,side);
  let base,rect;
  if(mock){
    base=await loadImg(mock.preview_url);
    rect={px:mock.print_x/100,py:mock.print_y/100,pw:mock.print_w/100,ph:mock.print_h/100};
  }else{
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 430 520" width="1290" height="1560">${shirtSvgMarkup(modelId,color)}</svg>`;
    base=await loadImg('data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg));
    rect=svgPrintRect(modelId);
  }
  const k=Math.min(1,1600/Math.max(base.naturalWidth,base.naturalHeight));
  const W=Math.round(base.naturalWidth*k), H=Math.round(base.naturalHeight*k);
  const out=document.createElement('canvas');out.width=W;out.height=H;
  const ox=out.getContext('2d');
  ox.fillStyle='#fff';ox.fillRect(0,0,W,H); // foto PNG sem fundo não vira preto no JPG
  ox.drawImage(base,0,0,W,H);
  // Mesmo encaixe da tela: o canvas 420x480 do editor esticado na área de impressão.
  const rx=rect.px*W, ry=rect.py*H, sx=rect.pw*W/420, sy=rect.ph*H/480;
  ox.save();ox.translate(rx,ry);ox.scale(sx,sy);list.forEach(e=>draw(e,ox));ox.restore();
  const bs=list.map(bounds);
  const x0=rx+Math.min(...bs.map(b=>b.minX))*sx, y0=ry+Math.min(...bs.map(b=>b.minY))*sy;
  const x1=rx+Math.max(...bs.map(b=>b.maxX))*sx, y1=ry+Math.max(...bs.map(b=>b.maxY))*sy;
  const u=Math.max(2,W/400);
  ox.strokeStyle='#ff4b18';ox.lineWidth=u;ox.setLineDash([4*u,3*u]);ox.strokeRect(x0,y0,x1-x0,y1-y0);ox.setLineDash([]);
  const label=`${side==='FRONT'?'FRENTE':'COSTAS'} · arte ${fmtCm(art.widthCm)} × ${fmtCm(art.heightCm)} cm`;
  ox.font=`800 ${Math.round(W/28)}px Arial`;ox.textBaseline='middle';
  const pad=Math.round(W/60), th=Math.round(W/28*1.8);
  ox.fillStyle='rgba(0,0,0,.75)';ox.fillRect(pad,pad,ox.measureText(label).width+pad*2,th);
  ox.fillStyle='#fff';ox.fillText(label,pad*2,pad+th/2);
  return await new Promise(r=>out.toBlob(r,'image/jpeg',0.9));
}
function sizeMap(){const m={};$$('#sizeGrid input').forEach(i=>m[i.dataset.size]=sizeQty(i));return m}
// Envio acima do limite: o nginx responde 413 em HTML, sem o "detail" do servidor.
const orderError=(r,d)=>d.detail||(r.status===413?'Os arquivos ficaram grandes demais para enviar. Diminua a arte ou divida o pedido.':'Erro ao criar o pedido.');
/* ---------- Envio do pedido ---------- */
// O canvas não grava DPI no PNG, e sem isso o RIP abre a arte a 72 dpi.
// Insere o bloco pHYs logo depois do IHDR (o servidor fazia isso no envio
// pelo formulário; no envio direto pro Storage o arquivo não passa por ele).
const CRC_TABLE=Array.from({length:256},(_,n)=>{let c=n;for(let k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;return c>>>0});
const crc32=bytes=>{let c=0xFFFFFFFF;for(const b of bytes)c=CRC_TABLE[(c^b)&255]^(c>>>8);return (c^0xFFFFFFFF)>>>0};
async function pngWithDpi(blob,dpi){
  const head=new Uint8Array(await blob.slice(0,65536).arrayBuffer());
  if(head.length<33||[137,80,78,71,13,10,26,10].some((b,i)=>head[i]!==b)) return blob;
  for(let p=8;p+8<=head.length;){
    const len=((head[p]<<24)|(head[p+1]<<16)|(head[p+2]<<8)|head[p+3])>>>0;
    const type=String.fromCharCode(...head.subarray(p+4,p+8));
    if(type==='pHYs') return blob;
    if(type==='IDAT') break;
    p+=12+len;
  }
  const ppm=Math.round(dpi/0.0254), chunk=new Uint8Array(21), dv=new DataView(chunk.buffer);
  dv.setUint32(0,9); chunk.set([112,72,89,115],4); dv.setUint32(8,ppm); dv.setUint32(12,ppm); chunk[16]=1;
  dv.setUint32(17,crc32(chunk.subarray(4,17)));
  return new Blob([blob.slice(0,33),chunk,blob.slice(33)],{type:'image/png'});
}
// Cria o pedido. files: [[campo, blob, nome do arquivo], ...]. Com o Supabase
// (CONFIG.direct_upload) as artes vão direto do navegador pro Storage, sem
// passar pelo servidor do site, e o pedido leva só o token do envio.
async function sendOrder(data,files,statusSel){
  const fd=new FormData();
  fd.append('order_json',JSON.stringify(data));
  if(CONFIG.direct_upload&&files.length){
    const dpi=Number(CONFIG.dpi)||300;
    const ready=await Promise.all(files.map(async([field,blob,name])=>[field,/\.png$/i.test(name)&&!field.startsWith('source_')?await pngWithDpi(blob,dpi):blob,name]));
    const ext=name=>((/\.[a-z0-9]+$/i.exec(name||'')||['.png'])[0]).toLowerCase();
    const r=await fetch('/api/orders/uploads',{method:'POST',headers:{...customerHeaders(),'Content-Type':'application/json'},
      body:JSON.stringify({files:ready.map(([field,blob,name])=>({field,size:blob.size,ext:ext(name)}))})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) return {r,d};
    let sent=0;
    const total=ready.reduce((a,[,b])=>a+b.size,0), mb=b=>(b/1048576).toFixed(1).replace('.',',');
    for(const [field,blob] of ready){
      stat(statusSel,`Enviando arte... ${mb(sent)} de ${mb(total)} MB`,true);
      const up=await fetch(d.urls[field],{method:'PUT',headers:{'Content-Type':blob.type||'application/octet-stream','x-upsert':'true'},body:blob})
        .catch(()=>null);
      if(!up||!up.ok) throw new Error('Não foi possível enviar a arte. Confira sua internet e tente de novo.');
      sent+=blob.size;
    }
    stat(statusSel,'Criando pedido...',true);
    fd.append('upload_token',d.token);
  }else{
    files.forEach(([field,blob,name])=>fd.append(field,blob,name));
  }
  const r=await fetch('/api/orders',{method:'POST',headers:customerHeaders(),body:fd});
  return {r,d:await r.json().catch(()=>({}))};
}
/* ---------- Pagamento (Mercado Pago) ---------- */
// Leva pra página de pagamento. O pedido já está salvo: quem desistir paga depois em Minha conta.
async function openCheckout(orderId){
  const r=await fetch(`/api/me/orders/${orderId}/checkout`,{method:'POST',headers:customerHeaders()}), d=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(d.detail||'Não foi possível abrir o pagamento.');
  // Dá tempo do GA/Pixel enviarem o evento antes de sair pro Mercado Pago.
  if(loaded.analytics||loaded.marketing) await new Promise(res=>setTimeout(res,400));
  location.href=d.url;
}
async function afterOrderCreated(d,statusSel){
  if(!CONFIG.online_payment) return stat(statusSel,`Pedido ${d.order_number} criado. Total: ${money(d.total)}`,true);
  stat(statusSel,`Pedido ${d.order_number} criado (${money(d.total)}). Abrindo o pagamento...`,true);
  track('begin_checkout',{value:d.total,currency:'BRL'});
  try{ await openCheckout(d.id); }
  catch(e){ stat(statusSel,`Pedido ${d.order_number} criado, mas o pagamento não abriu: ${e.message} Pague depois em Minha conta.`,false); }
}
// Confere no Mercado Pago: na volta do pagamento e no "Já paguei, verificar"
// (no PC o Mercado Pago não consegue avisar o site sozinho).
async function verifyPayment(orderId,btn,returnStatus){
  if(btn) btn.disabled=true;
  try{
    const r=await fetch(`/api/me/orders/${orderId}/sync-payment`,{method:'POST',headers:customerHeaders()}), d=await r.json().catch(()=>({}));
    if(r.status===401){ logoutCustomer(); return openAuthModal(); }
    if(!r.ok) throw new Error(d.detail||'Não foi possível verificar o pagamento agora.');
    if(d.payment_status==='PAID'){ trackPurchase(orderId,d.order_number,d.total); stat('#profileNotice','Pagamento aprovado! Seu pedido já foi liberado.',true); await loadProfile(); }
    else if(returnStatus==='rejected'||returnStatus==='null') stat('#profileNotice','O pagamento não foi concluído. Você pode tentar de novo em "Pagar agora".',false);
    else stat('#profileNotice','Ainda não recebemos a confirmação do pagamento. Pix costuma levar alguns segundos e boleto até 2 dias úteis; assim que for aprovado, o pedido muda aqui.',true);
  }catch(e){ stat('#profileNotice',e.message,false); }
  finally{ if(btn) btn.disabled=false; }
}
// Volta do Mercado Pago: ?mp=retorno&status=...&external_reference=<id do pedido>.
function handlePaymentReturn(){
  const q=new URLSearchParams(location.search), ref=q.get('external_reference');
  if(q.get('mp')!=='retorno') return;
  history.replaceState(null,'',location.pathname+location.hash); // um F5 não confere de novo
  if(!ref||!currentCustomer) return;
  tab('profile');
  verifyPayment(ref,null,q.get('status'));
}
async function createOrder(){
  if(!currentCustomer){ openAuthModal(); return stat('#checkoutStatus','Entre ou crie uma conta antes de finalizar o pedido.',false); }
  if(!Object.values(sidesElements).some(list=>list.length))
    // Com as abas de manga desligadas (index.html), a mensagem não fala delas.
    return stat('#checkoutStatus',$('#sideTabs [data-side="SLEEVE_R"]')?'Adicione uma arte na frente, nas costas ou em uma das mangas.':'Adicione uma arte na frente ou nas costas.',false);
  if(shirtQty()<1)
    return stat('#checkoutStatus','Informe a quantidade em pelo menos um tamanho (P, M, G...).',false);
  const sizesOrdered=Object.entries(sizeMap()).filter(([,q])=>q>0).map(([s])=>s);
  for(const side of ['FRONT','BACK']){
    const tooBig=sizesOrdered.filter(s=>!artFitsSize(side,s));
    if(tooBig.length){
      const art=sideArtSizeCm(side);
      const list=tooBig.map(s=>{const l=sizeLimit(s);return `${s} (máx. ${fmtCm(l.w)} × ${fmtCm(l.h)} cm)`}).join(', ');
      return stat('#checkoutStatus',`A arte ${side==='FRONT'?'da frente':'das costas'} mede ${fmtCm(art.w)} × ${fmtCm(art.h)} cm e não cabe ${tooBig.length>1?'nos tamanhos':'no tamanho'} ${list}. Diminua a arte.`,false);
    }
  }
  const btn=$('#checkoutBtn');btn.disabled=true; // evita pedido duplicado no clique duplo
  try{
    stat('#checkoutStatus','Gerando arte...',true);
    const front=await exportProductionFor(sidesElements.FRONT,'FRONT');
    const back=await exportProductionFor(sidesElements.BACK,'BACK');
    const sleeveR=await exportProductionFor(sidesElements.SLEEVE_R,'SLEEVE_R');
    const sleeveL=await exportProductionFor(sidesElements.SLEEVE_L,'SLEEVE_L');
    const placeFront=await exportPlacementFor('FRONT',front);
    const placeBack=await exportPlacementFor('BACK',back);
    const type=$('#saleType').value;
    const main=front||back||sleeveR||sleeveL;
    const sourcesOf=list=>list.map(e=>({type:e.type,origin:e.origin||'TEXT',artwork_id:e.artworkId||null,premium_id:e.premiumId||null,text:e.type==='text'?e.text:null}));
    const sleeveInfo=art=>art?{width_cm:art.widthCm,height_cm:art.heightCm,dpi:art.dpi,width_px:art.widthPx,height_px:art.heightPx}:null;
    const data={
      sale_type:type,
      customer:customerPayload(),
      product:type==='DTF_ONLY'?null:{shirt_model:chosenModel().name,shirt_model_id:chosenModel().id,color:$('#shirtColor').value,sizes:sizeMap()},
      dtf_quantity:shirtQty(),
      design:{width_cm:main.widthCm,height_cm:main.heightCm,dpi:main.dpi,width_px:main.widthPx,height_px:main.heightPx},
      has_front:!!front, has_back:!!back,
      has_sleeve_right:!!sleeveR, has_sleeve_left:!!sleeveL,
      sleeve_right:sleeveInfo(sleeveR), sleeve_left:sleeveInfo(sleeveL),
      sources_front:sourcesOf(sidesElements.FRONT),
      sources_back:sourcesOf(sidesElements.BACK),
      sources_sleeve_right:sourcesOf(sidesElements.SLEEVE_R),
      sources_sleeve_left:sourcesOf(sidesElements.SLEEVE_L),
      total:Math.round(calcTotal()*100)/100,
      tracking:trackingContext()
    };
    const files=[
      front&&['design',front.blob,'arte_producao_frente.png'],
      back&&['design_back',back.blob,'arte_producao_costas.png'],
      sleeveR&&['design_sleeve_right',sleeveR.blob,'arte_producao_manga_direita.png'],
      sleeveL&&['design_sleeve_left',sleeveL.blob,'arte_producao_manga_esquerda.png'],
      placeFront&&['placement_front',placeFront,'posicao_frente.jpg'],
      placeBack&&['placement_back',placeBack,'posicao_costas.jpg'],
    ].filter(Boolean);
    const {r,d}=await sendOrder(data,files,'#checkoutStatus');
    if(r.status===401){ logoutCustomer(); openAuthModal(); }
    if(!r.ok)throw new Error(orderError(r,d));
    if(adminKey) loadOrders();
    await afterOrderCreated(d,'#checkoutStatus');
  }catch(e){ stat('#checkoutStatus',e.message,false); }
  finally{ btn.disabled=false; }
}
// O Admin entra com usuário, senha e (se ligado) o código do 2FA e recebe um
// token de sessão de 12 h. Só o token vai nas chamadas; a senha não fica guardada.
function adminHeaders(extra){return {...(extra||{}), 'X-Admin-Token': adminKey||''}}
function saveAdminSession(){
  try{ adminKey?sessionStorage.setItem('rf_admin',JSON.stringify({token:adminKey,user:adminUser,role:adminRole})):sessionStorage.removeItem('rf_admin'); }catch{}
}
function restoreAdminSession(){
  try{ const a=JSON.parse(sessionStorage.getItem('rf_admin')||'null'); if(a&&a.token){adminKey=a.token;adminUser=a.user;adminRole=a.role;} }catch{}
}
function clearAdmin(){adminKey=null;adminUser=null;adminRole=null;saveAdminSession();syncAdminUi()}
async function logoutAdmin(){
  if(adminKey) await fetch('/api/admin/logout',{method:'POST',headers:adminHeaders()}).catch(()=>{});
  clearAdmin(); history.replaceState(null,'',location.pathname); tab('home');
}
// O botão Admin fica escondido dos clientes: aparece abrindo o site com
// #admin no fim do endereço (ex.: http://127.0.0.1:8787/#admin) ou depois
// de entrar. Mover/redimensionar a área de impressão grava no mockup, então
// essas alças também só aparecem pro admin logado.
function syncAdminUi(){
  const logged=!!adminKey;
  $('.nav-btn[data-tab="admin"]').hidden=!logged && location.hash!=='#admin';
  $('#printAreaMove').hidden=!logged;
  $('#printAreaResize').hidden=!logged;
  renderLibrary(); // "Excluir arte" só aparece pro admin
}
function openAdminFromHash(){syncAdminUi();if(location.hash==='#admin')tab('admin')}
function openAdminLogin(){
  $('#adminUser').value=''; $('#adminPassword').value=''; $('#adminCode').value=''; $('#adminCodeWrap').hidden=true;
  $('#adminLoginStatus').textContent=''; $('#adminLoginStatus').className='';
  $('#adminLoginModal').showModal();
}
async function submitAdminLogin(){
  const user=$('#adminUser').value.trim(), pass=$('#adminPassword').value, code=$('#adminCode').value.trim();
  if(!user || !pass) return stat('#adminLoginStatus','Usuário ou senha inválidos.',false);
  const btn=$('#adminLoginBtn'); btn.disabled=true;
  try{
    const r=await fetch('/api/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user,password:pass,code})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) return stat('#adminLoginStatus', d.detail||'Usuário ou senha inválidos.', false); // traz quantas tentativas sobram
    if(d.need_code){
      $('#adminCodeWrap').hidden=false; $('#adminCode').focus();
      return stat('#adminLoginStatus','Digite o código de 6 dígitos do app autenticador.',true);
    }
    adminKey=d.token; adminUser=d.user; adminRole=d.role; saveAdminSession();
  }catch(e){ return stat('#adminLoginStatus','Não foi possível falar com o servidor.',false); }
  finally{ btn.disabled=false; }
  syncAdminUi();
  stat('#adminLoginStatus','Login realizado!',true);
  setTimeout(()=>$('#adminLoginModal').close(),400);
  enterAdmin();
}
// Resposta de erro sem JSON (ex.: o nginx barra arquivo grande com um 413 em HTML).
const uploadError=(r,d)=>d.detail||(r.status===413?'Arquivo grande demais para enviar.':`Erro ${r.status}.`);
/* ============ Biblioteca de artes digitais (hospedada no site) ============ */
// Dois lados fixos (Grátis e Premium, ids 'free' e 'premium') -> pastas -> artes.
// A pasta tem o acesso do lado; quem tem premium baixa e usa as premium.
// libView.catId é o lado aberto e libView.subId a pasta aberta.
let library={categories:[]}, libView={catId:'free',subId:null}, libArts={}, libArtIndex={};
let libQuery='', libSort='name', libArtSort='recent', premiumNotice=null;
const LIB_SIDES=['free','premium'];
const ACCESS_LABEL={free:'Grátis',premium:'Premium'};
const hasPremium=()=>!!currentCustomer && (currentCustomer.premium_until||0)*1000>Date.now();
const normTxt=v=>String(v||'').normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase();
const libCat=id=>library.categories.find(c=>c.id===id);
const libSubs=id=>library.categories.filter(c=>c.parent_id===id);
const libTotal=side=>libSubs(side).reduce((a,x)=>a+(x.art_count||0),0);
const plural=(n,one,many)=>`${n} ${n===1?one:many}`;
function libBadge(access){
  if(access==='premium'&&hasPremium()) return '<span class="lib-badge total">Acesso total</span>';
  return `<span class="lib-badge ${access}">${ACCESS_LABEL[access]}</span>`;
}
async function loadLibrary(){
  try{ const r=await fetch('/api/library'); if(r.ok) library=await r.json().catch(()=>({})); }catch{}
  libArts={}; // as artes de cada categoria são buscadas de novo ao abrir
  renderLibrary(); renderLibraryAdmin();
}
const scrollLibTop=()=>$('#digital').scrollIntoView({block:'start'});
function renderLibrary(){
  if(!CONFIG) return;
  renderLibraryPremium();
  const box=$('#libraryView'), sub=libCat(libView.subId);
  if(!sub) libView.subId=null; // pasta apagada
  else libView.catId=sub.parent_id; // a pasta pode ter mudado de lado
  const side=libCat(libView.catId);
  if(!sub){
    box.innerHTML=`<div class="lib-head"><h1 class="lib-title">Biblioteca de artes</h1>
        <p>Escolha um lado, abra uma pasta e baixe em segundos, ou use direto na sua camisa e no DTF.</p></div>
      <div class="lib-sides">${LIB_SIDES.map(libSideHtml).join('')}</div>
      <div class="lib-tools">
        <input id="libSearch" placeholder="Pesquise uma pasta (exemplo: anime)" value="${esc(libQuery)}">
        <label class="lib-field"><span>Classificar pastas</span><select id="libSort">
          <option value="name">Por nome (A–Z)</option><option value="name_desc">Por nome (Z–A)</option>
          <option value="count">Mais artes</option><option value="recent">Mais recentes</option></select></label>
        <button type="button" class="lib-apply" id="libApply">Aplicar</button>
      </div>
      <div id="libGrid" class="lib-grid"></div>`;
    const apply=()=>{libQuery=$('#libSearch').value;libSort=$('#libSort').value;renderLibGrid()};
    $('#libSort').value=libSort;
    $('#libSearch').oninput=apply; $('#libSort').onchange=apply; $('#libApply').onclick=apply;
    // Trocar de lado mantém a busca: quem pesquisou "anime" no grátis já vê o anime do premium.
    $$('#libraryView [data-lib-side]').forEach(b=>b.onclick=()=>{libView={catId:b.dataset.libSide,subId:null};renderLibrary()});
    renderLibGrid();
    return;
  }
  box.innerHTML=`<button type="button" class="lib-back" data-lib-go>← Voltar a ${esc(side.name)}</button>
    <h1 class="lib-title">${esc(sub.name)}</h1>
    <p class="lib-crumb">Biblioteca → ${esc(side.name)} → ${esc(sub.name)}</p>
    ${libBadge(sub.access)}
    <div class="lib-sortbar"><label class="lib-field"><span>Ordenar artes</span><select id="libArtSort">
      <option value="recent">Mais recente</option><option value="old">Mais antigo</option><option value="name">Nome (A–Z)</option></select></label>
      <button type="button" class="lib-apply" id="libArtApply">Aplicar</button></div>
    <div id="libArts" class="lib-arts"></div>`;
  $('#libraryView [data-lib-go]').onclick=()=>{libView.subId=null;renderLibrary();scrollLibTop()};
  const sel=$('#libArtSort'), apply=()=>{libArtSort=sel.value;renderLibArts(sub)};
  sel.value=libArtSort; sel.onchange=apply; $('#libArtApply').onclick=apply;
  renderLibArts(sub);
}
function libSideHtml(id){
  const side=libCat(id), n=libSubs(id).length;
  return `<button type="button" class="lib-side ${id}${libView.catId===id?' on':''}" data-lib-side="${id}">
    <span class="lib-side-icon">${id==='premium'?'★':'↓'}</span>
    <span><b>${esc(side?.name||ACCESS_LABEL[id])}</b><small>${plural(n,'pasta','pastas')} · ${plural(libTotal(id),'arte','artes')}</small></span>
    ${libBadge(id)}</button>`;
}
function renderLibGrid(){
  const q=normTxt(libQuery.trim()), cards=libSubs(libView.catId).filter(c=>normTxt(c.name).includes(q));
  const sorters={name:(a,b)=>a.name.localeCompare(b.name,'pt-BR',{numeric:true}),name_desc:(a,b)=>b.name.localeCompare(a.name,'pt-BR',{numeric:true}),count:(a,b)=>(b.art_count||0)-(a.art_count||0),recent:(a,b)=>b.created_at-a.created_at};
  cards.sort(sorters[libSort]||sorters.name);
  $('#libGrid').innerHTML=cards.map(c=>`<button type="button" class="lib-card" data-lib-open="${c.id}">${libBadge(c.access)}
      <b>${esc(c.name)}</b><small>${plural(c.art_count||0,'arte','artes')}</small></button>`).join('')
    ||`<p class="lib-empty">${q?'Nenhuma pasta encontrada para essa busca neste lado.':'Nenhuma pasta neste lado ainda.'}</p>`;
  $$('#libGrid [data-lib-open]').forEach(b=>b.onclick=()=>{libView.subId=b.dataset.libOpen;renderLibrary();scrollLibTop()});
}
async function renderLibArts(cat){
  if(!libArts[cat.id]){
    $('#libArts').innerHTML='<p class="lib-empty">Carregando artes...</p>';
    try{ const r=await fetch(`/api/library/categories/${cat.id}/arts`); libArts[cat.id]=r.ok?await r.json().catch(()=>({})):[]; }catch{ libArts[cat.id]=[]; }
    if(libView.subId!==cat.id||!$('#libArts')) return; // a pessoa já saiu desta pasta
  }
  const locked=cat.access==='premium'&&!hasPremium();
  const sorters={recent:(a,b)=>b.created_at-a.created_at,old:(a,b)=>a.created_at-b.created_at,name:(a,b)=>a.name.localeCompare(b.name,'pt-BR')};
  const arts=[...libArts[cat.id]].sort(sorters[libArtSort]||sorters.recent);
  arts.forEach(a=>{libArtIndex[a.id]={name:a.name,access:cat.access}});
  $('#libArts').innerHTML=arts.map(a=>`<article class="lib-art${locked?' locked':''}">
      <div class="lib-art-img"><img src="/api/library/arts/${a.id}/thumb" alt="${esc(a.name)}" loading="lazy" draggable="false">${locked?'<span class="lock-badge">🔒 Premium</span>':''}</div>
      <div class="lib-art-body"><b title="${esc(a.name)}">${esc(a.name)}</b>
        ${locked?'<button type="button" class="lib-dl" data-lib-subscribe>Assinar para baixar</button>'
          :`<button type="button" class="lib-dl" data-lib-dl="${a.id}">Baixar</button>
            <div class="lib-use"><button type="button" data-lib-use="${a.id}:FINISHED_SHIRT" title="Usar na camisa">Na camisa</button><button type="button" data-lib-use="${a.id}:DTF_ONLY" title="Usar no DTF">No DTF</button></div>`}
        ${adminKey?`<button type="button" class="lib-del" data-lib-del="${a.id}">Excluir arte</button>`:''}
      </div></article>`).join('')||'<p class="lib-empty">Nenhuma arte nesta pasta ainda.</p>';
  $$('#libArts [data-lib-dl]').forEach(b=>b.onclick=()=>downloadLibraryArt(b.dataset.libDl,b));
  $$('#libArts [data-lib-use]').forEach(b=>b.onclick=()=>useLibraryArt(b.dataset.libUse,b));
  $$('#libArts [data-lib-subscribe]').forEach(b=>b.onclick=subscribePremium);
  $$('#libArts [data-lib-del]').forEach(b=>b.onclick=()=>deleteLibraryArt(b.dataset.libDel));
  // Sem "salvar imagem como" nas bloqueadas (é só a prévia, mas não precisa facilitar).
  $$('#libArts .locked img').forEach(img=>img.oncontextmenu=ev=>ev.preventDefault());
}
function renderLibraryPremium(){
  const box=$('#libraryPremium'), n=premiumNotice;
  box.innerHTML=hasPremium()
    ? `<div><b>★ Premium ativo até ${fmtDate(currentCustomer.premium_until)}</b><br>Baixe e use todas as artes premium.</div>`
    : `<div><b>★ Premium</b> · Use todas as artes premium por <b>${money(CONFIG.premium_price)}</b> por mês.
        ${n?`<div class="${n.ok?'success':'error'}">${esc(n.msg)}</div>`:''}</div>
       <button type="button" class="btn-grad" id="subscribePremium">Assinar premium</button>`;
  const b=$('#subscribePremium'); if(b) b.onclick=subscribePremium;
}
// Pedido do plano premium (biblioteca e "Minha conta"). Devolve a mensagem pra mostrar.
async function requestPremium(){
  try{
    const r=await fetch('/api/premium/subscribe',{method:'POST',headers:{...customerHeaders(),'Content-Type':'application/json'},body:JSON.stringify({tracking:trackingContext()})}), d=await r.json().catch(()=>({}));
    if(r.status===401){ logoutCustomer(); openAuthModal(); }
    if(!r.ok) throw new Error(d.detail||'Erro');
    if(adminKey) loadOrders();
    if(CONFIG.online_payment){
      track('begin_checkout',{value:d.total,currency:'BRL',items:[{item_name:'Premium'}]});
      try{ await openCheckout(d.id); return {ok:true,msg:`Pedido ${d.order_number} criado. Abrindo o pagamento...`}; }
      catch(e){ return {ok:false,msg:`Pedido ${d.order_number} criado, mas o pagamento não abriu: ${e.message} Pague depois em Minha conta.`}; }
    }
    return {ok:true,msg:`Pedido ${d.order_number} criado (${money(d.total)}). O premium é liberado assim que o pagamento for aprovado.`};
  }catch(e){ return {ok:false,msg:e.message}; }
}
async function subscribePremium(){
  if(!currentCustomer) return openAuthModal();
  const b=$('#subscribePremium'); if(b) b.disabled=true; // evita pedido duplicado no clique duplo
  premiumNotice=await requestPremium();
  renderLibraryPremium();
  $('#libraryPremium').scrollIntoView({block:'nearest'}); // quem clicou no cartão de uma arte vê a mensagem lá em cima
}
// Arquivo com o token da conta (sem conta o servidor recusa).
async function fetchDigitalBlob(url){
  const r=await fetch(url,{headers:customerHeaders()});
  if(r.status===401){ logoutCustomer(); openAuthModal(); throw new Error('Entre na sua conta de novo.'); }
  if(!r.ok){ const d=await r.json().catch(()=>({})); if(r.status===403) refreshCustomer(); throw new Error(d.detail||'Não foi possível abrir a arte.'); }
  return r.blob();
}
async function downloadLibraryArt(id,btn,name){
  if(!currentCustomer) return openAuthModal();
  btn.disabled=true;
  try{
    const blob=await fetchDigitalBlob(`/api/library/arts/${id}/file?download=1`);
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=`${name||libArtIndex[id]?.name||'arte'}.${(blob.type.split('/')[1]||'png').replace('jpeg','jpg')}`;
    a.click();
    setTimeout(()=>URL.revokeObjectURL(a.href),10000);
  }catch(e){ alert(e.message); }
  finally{ btn.disabled=false; }
}
// Leva a arte original pro editor. premiumId fica nas fontes do pedido ("Minha conta" mostra a arte premium usada).
async function useLibraryArt(ref,btn){
  if(!currentCustomer) return openAuthModal();
  const [id,saleType]=ref.split(':'), info=libArtIndex[id]||{name:'Arte',access:'free'};
  btn.disabled=true;
  try{
    const url=URL.createObjectURL(await fetchDigitalBlob(`/api/library/arts/${id}/file`));
    const premiumId=info.access==='premium'?id:null;
    tab('editor');
    $('#saleType').value=saleType; toggleSale(); updatePrice();
    if(saleType==='DTF_ONLY') handleGangImageURL(url,info.name,premiumId);
    else (await addImage(url,premiumId?'PREMIUM':'BIBLIOTECA')).premiumId=premiumId;
  }catch(e){ alert(e.message); }
  finally{ btn.disabled=false; }
}

/* ---------- Janela "Artes Ripfire" (no editor) ---------- */
// A mesma biblioteca da aba Artes digitais, em tamanho de janela: Grátis/Premium
// → pastas → artes. Clicou na arte, ela vai pra camisa (ou pra chapa do DTF).
let apView={side:'free',subId:null}, apQuery='';
async function openArtPicker(){
  const dlg=$('#artPickerModal');
  apView.subId=null; apQuery=''; $('#apStatus').textContent='';
  const dtf=$('#saleType').value==='DTF_ONLY';
  $('#apSubtitle').textContent=dtf?'Escolha uma arte e ela entra na sua chapa de DTF.':'Escolha uma arte e ela vai direto pra sua camisa.';
  renderArtPicker();
  if(!dlg.open) dlg.showModal();
  if(!library.categories.length){ await loadLibrary(); renderArtPicker(); }
}
function renderArtPicker(){
  $('#apSides').innerHTML=LIB_SIDES.map(id=>`<button type="button" role="tab" class="ap-side ${id}${apView.side===id?' on':''}" aria-selected="${apView.side===id}" data-ap-side="${id}">
      <span class="ap-side-icon" aria-hidden="true">${id==='premium'?'★':'↓'}</span><span><b>${esc(libCat(id)?.name||ACCESS_LABEL[id])}</b>
      <small>${plural(libTotal(id),'arte','artes')}</small></span>${id==='premium'?libBadge('premium'):''}</button>`).join('');
  $$('#apSides [data-ap-side]').forEach(b=>b.onclick=()=>{apView={side:b.dataset.apSide,subId:null};renderArtPicker()});
  const prem=$('#apPremium'), locked=apView.side==='premium'&&!hasPremium();
  prem.hidden=!locked;
  if(locked){
    prem.innerHTML=`<span><b>★ Premium</b> · Use todas as artes premium por <b>${money(CONFIG.premium_price)}</b> por mês.</span>
      <button type="button" class="btn-grad" data-ap-sub>Assinar</button>`;
    prem.querySelector('[data-ap-sub]').onclick=()=>{$('#artPickerModal').close();tab('digital');subscribePremium()};
  }
  const sub=libCat(apView.subId);
  if(!sub){
    $('#apBody').innerHTML=`<input id="apSearch" class="ap-search" type="search" placeholder="Buscar pasta (ex.: anime)" value="${esc(apQuery)}" aria-label="Buscar pasta">
      <div id="apFolders" class="ap-folders"></div>`;
    $('#apSearch').oninput=e=>{apQuery=e.target.value;renderApFolders()};
    renderApFolders();
    return;
  }
  $('#apBody').innerHTML=`<button type="button" class="ap-back" data-ap-back>← ${esc(libCat(apView.side)?.name||'Voltar')}</button>
    <h3 class="ap-folder-title">${esc(sub.name)}</h3><div id="apArts" class="ap-arts"><p class="ap-empty">Carregando artes...</p></div>`;
  $('#apBody [data-ap-back]').onclick=()=>{apView.subId=null;renderArtPicker()};
  renderApArts(sub);
}
function renderApFolders(){
  const q=normTxt(apQuery.trim());
  const cards=libSubs(apView.side).filter(c=>normTxt(c.name).includes(q))
    .sort((a,b)=>a.name.localeCompare(b.name,'pt-BR',{numeric:true}));
  $('#apFolders').innerHTML=cards.map(c=>`<button type="button" class="ap-folder" data-ap-open="${c.id}">
      <span class="ap-folder-icon" aria-hidden="true">▣</span><b>${esc(c.name)}</b><small>${plural(c.art_count||0,'arte','artes')}</small></button>`).join('')
    ||`<p class="ap-empty">${q?'Nenhuma pasta encontrada.':library.categories.length?'Nenhuma pasta neste lado ainda.':'Carregando...'}</p>`;
  $$('#apFolders [data-ap-open]').forEach(b=>b.onclick=()=>{apView.subId=b.dataset.apOpen;renderArtPicker()});
}
async function renderApArts(cat){
  if(!libArts[cat.id]){
    try{ const r=await fetch(`/api/library/categories/${cat.id}/arts`); libArts[cat.id]=r.ok?await r.json().catch(()=>[]):[]; }catch{ libArts[cat.id]=[]; }
  }
  if(apView.subId!==cat.id||!$('#apArts')) return; // saiu da pasta enquanto carregava
  const locked=cat.access==='premium'&&!hasPremium();
  const arts=[...libArts[cat.id]].sort((a,b)=>b.created_at-a.created_at);
  arts.forEach(a=>{libArtIndex[a.id]={name:a.name,access:cat.access}});
  $('#apArts').innerHTML=arts.map(a=>`<button type="button" class="ap-art${locked?' locked':''}" data-ap-use="${a.id}" title="${esc(a.name)}">
      <span class="ap-art-img"><img src="/api/library/arts/${a.id}/thumb" alt="" loading="lazy" draggable="false">${locked?'<span class="lock-badge">🔒</span>':''}</span>
      <span class="ap-art-name">${esc(a.name)}</span><span class="ap-art-cta">${locked?'Premium':'Usar arte'}</span></button>`).join('')
    ||'<p class="ap-empty">Nenhuma arte nesta pasta ainda.</p>';
  $$('#apArts [data-ap-use]').forEach(b=>b.onclick=()=>locked?($('#apStatus').textContent='Essa arte é premium. Assine para usar.'):pickArt(b.dataset.apUse,b));
}
async function pickArt(id,btn){
  if(!currentCustomer){ $('#artPickerModal').close(); return openAuthModal(); }
  const info=libArtIndex[id]||{name:'Arte',access:'free'}, premiumId=info.access==='premium'?id:null;
  $$('#apArts [data-ap-use]').forEach(b=>b.disabled=true);
  $('#apStatus').textContent=`Carregando "${info.name}"...`;
  try{
    const url=URL.createObjectURL(await fetchDigitalBlob(`/api/library/arts/${id}/file`));
    if($('#saleType').value==='DTF_ONLY') handleGangImageURL(url,info.name,premiumId);
    else (await addImage(url,premiumId?'PREMIUM':'BIBLIOTECA')).premiumId=premiumId;
    $('#artPickerModal').close();
  }catch(e){ $('#apStatus').textContent=e.message; }
  finally{ $$('#apArts [data-ap-use]').forEach(b=>b.disabled=false); }
}

/* ---------- Admin da biblioteca ---------- */
async function libAdminFetch(url,opts={}){
  const r=await fetch(url,{...opts,headers:adminHeaders(opts.headers)}), d=await r.json().catch(()=>({}));
  if(r.status===401){ clearAdmin(); throw new Error('Entre no Admin de novo.'); }
  if(!r.ok) throw new Error(d.detail||'Erro');
  return d;
}
async function deleteLibraryArt(id){
  if(!confirm(`Excluir a arte "${libArtIndex[id]?.name||''}"? O arquivo é apagado e não dá para desfazer.`)) return;
  try{ await libAdminFetch(`/api/admin/library/arts/${id}`,{method:'DELETE'}); await loadLibrary(); }
  catch(e){ alert(e.message); }
}

/* ============ Minha conta (perfil do cliente) ============ */
let profileData=null, profileView='overview', profilePremiumNotice=null;
const ORDER_TYPES={FINISHED_SHIRT:'Camisa pronta',DTF_PLUS_SHIRT:'DTF + camisa',DTF_ONLY:'Somente DTF',PREMIUM_SUB:'Premium',DIGITAL_ART:'Arte digital'};
const SIDE_NAMES={FRONT:'Frente',BACK:'Costas',SLEEVE_R:'Manga direita',SLEEVE_L:'Manga esquerda'};
const MONTHS=['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
const fmtDate=ts=>new Date(ts*1000).toLocaleDateString('pt-BR');
const formatCep=v=>{const d=String(v||'').replace(/\D/g,'').slice(0,8);return d.length>5?d.slice(0,5)+'-'+d.slice(5):d};
const joinPt=list=>list.length>1?list.slice(0,-1).join(', ')+' e '+list[list.length-1]:(list[0]||'');
const premiumDaysLeft=()=>hasPremium()?Math.ceil((currentCustomer.premium_until*1000-Date.now())/86400000):0;
async function loadProfile(){
  if(!currentCustomer){ tab('home'); return openAuthModal(); }
  // A aba aparece na hora com os dados da conta; pedidos e downloads entram quando chegam.
  renderProfile(); fillProfileForm();
  try{
    const r=await fetch('/api/me/profile',{headers:customerHeaders()});
    if(r.status===401){ logoutCustomer(); return openAuthModal(); }
    // 404 = servidor ainda na versão antiga, sem as rotas da conta.
    if(r.status===404) throw new Error('Não foi possível carregar seus pedidos agora. Se você é o dono do site, reinicie o servidor (start.bat).');
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail||'Erro ao carregar a conta.');
    profileData=d;
    currentCustomer={...currentCustomer,...d.customer}; saveCustomerToStorage(); renderAccountBoxes();
    renderProfile(); fillProfileForm();
  }catch(e){ $('#profileOrders').innerHTML=`<p class="error">${esc(e.message)}</p>`; }
}
function renderProfile(){
  if(!currentCustomer) return;
  const c=currentCustomer, d=profileData||{orders:[],downloads:[]}, created=new Date((c.created_at||Date.now()/1000)*1000);
  $('#profileAvatar').textContent=customerInitial();
  $('#profileName').textContent=c.full_name||'Minha conta';
  $('#profileEmail').textContent=`${c.email} · cliente desde ${MONTHS[created.getMonth()]}/${created.getFullYear()}`;
  $('#profilePremiumChip').hidden=!hasPremium();
  $('#profileStats').innerHTML=[[profileData?d.orders.length:'–','pedidos'],[profileData?d.downloads.length:'–','artes baixadas'],[premiumDaysLeft(),'dias de premium']]
    .map(([n,l])=>`<div class="pstat"><b>${n}</b><span>${l}</span></div>`).join('');
  // Visão geral mostra tudo resumido; as outras sub-abas mostram uma parte inteira.
  const v=profileView, all=v!=='overview';
  $$('#profileTabs [data-pview]').forEach(b=>b.classList.toggle('active',b.dataset.pview===v));
  Object.entries({profileOrdersPanel:'orders',profileDownloadsPanel:'downloads',profilePremium:'premium',profileDataPanel:'data'})
    .forEach(([id,view])=>{$('#'+id).hidden=all&&v!==view});
  $('#profileLayout').classList.toggle('single',all);
  renderProfileOrders(all?d.orders:d.orders.slice(0,3));
  $('#profileOrdersMore').hidden=all||d.orders.length<=3;
  renderProfileDownloads(all?d.downloads:d.downloads.slice(0,8));
  $('#profileDownloadsMore').hidden=all||d.downloads.length<=8;
  renderProfilePremium();
}
function orderIcon(o){
  if(o.sale_type==='PREMIUM_SUB') return '<span class="o-star">★</span>';
  if(o.sale_type==='DTF_ONLY') return '<svg viewBox="0 0 100 100"><rect x="18" y="8" width="64" height="84" rx="4" fill="#fff" stroke="#d6d6d6"/><rect x="24" y="14" width="22" height="18" rx="2" fill="#9b59b6"/><rect x="50" y="14" width="26" height="18" rx="2" fill="#c39bd3"/><rect x="24" y="36" width="34" height="22" rx="2" fill="#7d3f9a"/><rect x="62" y="36" width="14" height="22" rx="2" fill="#b9b9b9"/></svg>';
  const fill=SHIRT_COLOR_HEX[o.product?.color]||'#1b1b1b';
  return `<svg viewBox="0 0 100 100"><path d="M30 10 L40 6 Q50 14 60 6 L70 10 L90 24 L82 38 L74 33 L74 94 L26 94 L26 33 L18 38 L10 24 Z" fill="${fill}" stroke="#bdbdbd" stroke-width="1"/><rect x="38" y="34" width="24" height="26" rx="2" fill="#9b59b6"/></svg>`;
}
function orderSizes(o){return Object.entries(o.product?.sizes||{}).filter(([,q])=>q>0)}
function orderSummary(o){
  const date=fmtDate(o.created_at);
  if(o.sale_type==='PREMIUM_SUB') return `${date} · ${o.premium_days||30} dias de acesso a todas as artes premium`;
  if(o.sale_type==='DTF_ONLY'){
    const n=o.sources.length, qty=o.sources.reduce((a,s)=>a+(Number(s.qty)||0),0);
    return `${date}${o.sheet?` · chapa ${fmtCm(o.sheet.w)} × ${fmtCm(o.sheet.h)} cm`:''} · ${n} ${n===1?'arte':'artes'}, ${qty} ${qty===1?'peça':'peças'}`;
  }
  const p=o.product||{}, sizes=orderSizes(o), qty=sizes.reduce((a,[,q])=>a+q,0);
  const sides=Object.keys(o.art_sizes||{}).map(s=>SIDE_NAMES[s].toLowerCase());
  return `${date} · ${p.shirt_model||''} ${p.color||''} · ${qty} ${qty===1?'peça':'peças'} (${sizes.map(([s,q])=>`${s} ×${q}`).join(', ')})${sides.length?' · '+joinPt(sides):''}`;
}
// Etapas que o cliente vê: pago = em produção; "Pronto" é marcado no Admin.
// Etapas do Admin: Em análise / Em fila / Em impressão = "Em produção"; Finalizado = Pronto; Enviado.
const CUSTOMER_STAGE={EM_ANALISE:'em análise',EM_FILA:'na fila',EM_IMPRESSAO:'imprimindo'};
function orderSteps(o){
  const cur=o.stage==='ENVIADO'?5:(o.status==='PRONTO'||o.stage==='FINALIZADO')?4:o.payment_status==='PAID'?3:1;
  const sub=CUSTOMER_STAGE[o.stage];
  return `<div class="p-steps">${['Pedido feito','Pagamento aprovado',`Em produção${cur===3&&sub?` · ${sub}`:''}`,'Pronto','Enviado'].map((label,i)=>{
    const n=i+1, cls=n<cur||cur===5?'done':n===cur?'now':'';
    return `${i?`<span class="p-bar${n<=cur?' done':''}"></span>`:''}<span class="p-step ${cls}"><i>${cls==='done'?'✓':cls==='now'?'●':''}</i>${label}</span>`;
  }).join('')}</div>`;
}
function renderProfileOrders(list){
  $('#profileOrders').innerHTML=list.map(o=>{
    // Não pago em 24 h: o servidor cancela (Política de Reembolso) e o link de pagamento vence.
    const premium=o.sale_type==='PREMIUM_SUB', canceled=o.status==='CANCELADO', pending=o.payment_status!=='PAID'&&!canceled;
    const chip=canceled?'<span class="pchip wait">Cancelado · não pago em 24 h</span>':pending?'<span class="pchip wait">Aguardando pagamento</span>':premium?'<span class="pchip ok">✓ Liberado</span>':'';
    return `<article class="p-order">
      <div class="o-thumb">${orderIcon(o)}</div>
      <div><div class="o-title">Pedido ${esc(o.order_number)} · ${esc(ORDER_TYPES[o.sale_type]||o.sale_type)}</div><div class="o-meta">${esc(orderSummary(o))}</div></div>
      <div class="o-right"><div class="o-total">${money(o.total)}</div>${chip}
        ${pending&&CONFIG.online_payment?`<button type="button" class="btn-grad small" data-pay-order="${o.id}">Pagar agora</button>
          <button type="button" class="plink" data-check-pay="${o.id}">Já paguei, verificar</button>`:''}
        ${premium?'':`<button type="button" class="plink" data-detail="${o.id}">Ver detalhes ▼</button>`}</div>
      ${premium||canceled?'':orderSteps(o)}
      <div class="o-detail" id="detail_${o.id}" hidden></div>
    </article>`;
  }).join('')||(profileData?`<div class="p-empty">${orderIcon({})}<b>Seu primeiro pedido aparece aqui</b>
      <span>Assim que você fizer um pedido de camisa ou DTF, ele aparece com as etapas e as fotos de posição.</span>
      <button type="button" class="btn-grad" data-go-editor>Personalizar agora</button></div>`
    :'<p class="muted-note">Carregando seus pedidos...</p>');
  $$('#profileOrders [data-detail]').forEach(b=>b.onclick=()=>toggleOrderDetail(b));
  $$('#profileOrders [data-pay-order]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await openCheckout(b.dataset.payOrder)}catch(e){stat('#profileNotice',e.message,false);b.disabled=false}});
  $$('#profileOrders [data-check-pay]').forEach(b=>b.onclick=()=>verifyPayment(b.dataset.checkPay,b));
  $$('#profileOrders [data-go-editor]').forEach(b=>b.onclick=()=>tab('editor'));
}
async function toggleOrderDetail(btn){
  const o=profileData.orders.find(x=>x.id===btn.dataset.detail), box=$('#detail_'+o.id);
  box.hidden=!box.hidden;
  btn.textContent=box.hidden?'Ver detalhes ▼':'Ocultar detalhes ▲';
  if(box.hidden||box.dataset.ready) return;
  box.dataset.ready='1';
  const items=[];
  if(o.sale_type==='DTF_ONLY') o.sources.forEach(s=>items.push(`${esc(s.name||'Sem título')} · ${fmtCm(s.width_cm)} × ${fmtCm(s.height_cm)} cm · ${esc(s.qty)} ${Number(s.qty)===1?'peça':'peças'}`));
  else{
    Object.entries(o.art_sizes).forEach(([s,a])=>{if(!o.placements.includes(s))items.push(`${SIDE_NAMES[s]}: ${fmtCm(a.w)} × ${fmtCm(a.h)} cm`)});
    const sizes=orderSizes(o);
    if(sizes.length) items.push('Tamanhos: '+sizes.map(([s,q])=>`${esc(s)} ×${q}`).join(', '));
  }
  o.premium_arts.forEach(n=>items.push(`Arte premium "${esc(n)}"`));
  box.innerHTML=o.placements.map(s=>`<figure class="o-pos" data-pos="${s}"><div class="o-pos-img">Carregando...</div>
      <figcaption>${SIDE_NAMES[s]}${o.art_sizes[s]?` · ${fmtCm(o.art_sizes[s].w)} × ${fmtCm(o.art_sizes[s].h)} cm`:''}</figcaption></figure>`).join('')
    +(items.length?`<ul>${items.map(i=>`<li>${i}</li>`).join('')}</ul>`:'');
  // Fotos de posição geradas no pedido (só o dono do pedido consegue abrir).
  for(const s of o.placements){
    const holder=box.querySelector(`[data-pos="${s}"] .o-pos-img`);
    try{ holder.innerHTML=`<img src="${URL.createObjectURL(await fetchDigitalBlob(`/api/me/orders/${o.id}/placement/${s}`))}" alt="Posição: ${SIDE_NAMES[s]}">`; }
    catch{ holder.textContent='Foto indisponível'; }
  }
}
function renderProfileDownloads(list){
  $('#profileDownloads').innerHTML=list.map(dl=>{
    const ok=!!dl.available;
    return `<div class="p-dl"><div class="p-dl-img">${ok?`<img src="/api/library/arts/${dl.art_id}/thumb" alt="" loading="lazy">`:''}</div>
      <div class="p-dl-body"><b>${esc(dl.art_name)}</b><small>${ok?esc(dl.category||''):'Não está mais no site'}</small>
      <button type="button" data-redl="${dl.art_id}"${ok?'':' disabled'}>Baixar de novo</button></div></div>`;
  }).join('')||(profileData?`<div class="p-empty"><span class="p-empty-art"></span><b>Suas artes baixadas ficam aqui</b>
      <span>Baixe artes grátis na aba Artes digitais e pegue de novo quando quiser.</span>
      <button type="button" class="btn-grad" data-go-digital>Ver artes grátis</button></div>`
    :'<p class="muted-note">Carregando seus downloads...</p>');
  $$('#profileDownloads [data-redl]').forEach(b=>b.onclick=()=>downloadLibraryArt(b.dataset.redl,b,list.find(x=>x.art_id===b.dataset.redl)?.art_name));
  $$('#profileDownloads [data-go-digital]').forEach(b=>b.onclick=()=>tab('digital'));
}
function renderProfilePremium(){
  const box=$('#profilePremium'), price=money(CONFIG.premium_price), days=Number(CONFIG.premium_days)||30;
  const n=profilePremiumNotice, notice=n?`<p class="${n.ok?'success':'error'}">${esc(n.msg)}</p>`:'';
  if(hasPremium()){
    const left=premiumDaysLeft(), total=Math.max(days,left);
    box.innerHTML=`<span class="pchip premium">★ PREMIUM ATIVO</span>
      <h2>Até ${fmtDate(currentCustomer.premium_until)}</h2><p>Todas as artes premium liberadas: baixe e use na camisa e no DTF.</p>
      <div class="p-meter"><span style="width:${Math.round(left/total*100)}%"></span></div>
      <div class="p-meter-legend"><span>${left} ${left===1?'dia restante':'dias restantes'}</span><span>de ${total}</span></div>
      <button type="button" id="profileRenew" class="btn-grad full">Renovar +${days} dias · ${price}</button>
      <p class="small">Renovando antes de vencer, os dias novos somam aos que faltam.</p>${notice}`;
  }else{
    box.innerHTML=`<span class="pchip premium">★ PREMIUM</span>
      <h2>Libere todas as artes</h2><p>Use todas as artes premium por ${price} por mês.</p>
      <button type="button" id="profileRenew" class="btn-grad full">Assinar premium · ${price}</button>
      <button type="button" class="btn-line full on-dark" data-go-digital>Ver artes premium</button>${notice}`;
  }
  $('#profileRenew').onclick=async()=>{
    $('#profileRenew').disabled=true; // evita pedido duplicado no clique duplo
    profilePremiumNotice=await requestPremium();
    renderProfilePremium();
    if(profilePremiumNotice.ok) loadProfile(); // o pedido novo aparece na lista
  };
  $$('#profilePremium [data-go-digital]').forEach(b=>b.onclick=()=>tab('digital'));
}
function fillProfileForm(){
  const c=currentCustomer;
  $('#pfName').value=c.full_name||''; $('#pfEmail').value=c.email||'';
  $('#pfPhone').value=c.phone?formatPhoneBR(c.phone):''; $('#pfCep').value=formatCep(c.cep);
  $('#pfNumber').value=c.address_number||''; $('#pfDocument').value=c.document?formatCpfCnpj(c.document):'';
  $('#pfCurrentWrap').hidden=!c.has_password;
  $('#pfDeletePassWrap').hidden=!c.has_password; // conta só do Google: confirma digitando EXCLUIR
  $('#pfPasswordToggle').textContent=c.has_password?'Trocar senha':'Criar senha';
}
async function saveProfile(){
  const payload={full_name:$('#pfName').value.trim(),phone:$('#pfPhone').value.trim(),cep:$('#pfCep').value.trim(),address_number:$('#pfNumber').value.trim(),document:$('#pfDocument').value.trim()};
  const btn=$('#pfSave'); btn.disabled=true;
  try{
    const r=await fetch('/api/me',{method:'PATCH',headers:{...customerHeaders(),'Content-Type':'application/json'},body:JSON.stringify(payload)}), d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail||'Erro ao salvar.');
    currentCustomer={...currentCustomer,...d}; saveCustomerToStorage(); renderAccountBoxes(); renderProfile(); fillProfileForm();
    stat('#pfStatus','Dados salvos!',true);
  }catch(e){ stat('#pfStatus',e.message,false); }
  finally{ btn.disabled=false; }
}
async function savePassword(){
  const current=$('#pfCurrent').value, next=$('#pfNew').value;
  if(next.length<8) return stat('#pfPassStatus','A nova senha precisa ter pelo menos 8 caracteres.',false);
  if(next!==$('#pfConfirm').value) return stat('#pfPassStatus','As senhas não conferem.',false);
  const btn=$('#pfPasswordSave'); btn.disabled=true;
  try{
    const r=await fetch('/api/me/password',{method:'POST',headers:{...customerHeaders(),'Content-Type':'application/json'},body:JSON.stringify({current_password:current,new_password:next})}), d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail||'Erro ao salvar a senha.');
    currentCustomer.has_password=true; saveCustomerToStorage(); fillProfileForm();
    ['#pfCurrent','#pfNew','#pfConfirm'].forEach(s=>$(s).value='');
    $('#pfPasswordForm').hidden=true;
    stat('#pfStatus','Senha salva! Os outros aparelhos saíram da conta.',true);
  }catch(e){ stat('#pfPassStatus',e.message,false); }
  finally{ btn.disabled=false; }
}
/* ---------- LGPD: cópia dos dados e exclusão da conta ---------- */
async function exportMyData(){
  const btn=$('#pfExport'); btn.disabled=true;
  try{
    const r=await fetch('/api/me/export',{headers:customerHeaders()});
    if(r.status===401){ logoutCustomer(); return openAuthModal(); }
    if(!r.ok) throw new Error('Não foi possível gerar o arquivo agora.');
    const a=document.createElement('a'); a.href=URL.createObjectURL(await r.blob()); a.download='meus-dados-ripfire.json'; a.click();
    setTimeout(()=>URL.revokeObjectURL(a.href),10000);
    stat('#pfStatus','Arquivo com seus dados baixado.',true);
  }catch(e){ stat('#pfStatus',e.message,false); }
  finally{ btn.disabled=false; }
}
async function deleteMyAccount(){
  const confirmText=$('#pfDeleteConfirm').value.trim();
  if(confirmText.toUpperCase()!=='EXCLUIR') return stat('#pfDeleteStatus','Digite EXCLUIR para confirmar.',false);
  const btn=$('#pfDeleteSave'); btn.disabled=true;
  try{
    const r=await fetch('/api/me',{method:'DELETE',headers:{...customerHeaders(),'Content-Type':'application/json'},body:JSON.stringify({confirm:confirmText,password:$('#pfDeletePass').value})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail||'Não foi possível excluir agora.');
    // O token já não existe no servidor: só limpa o navegador.
    currentCustomer=null; profileData=null; saveCustomerToStorage(); renderAccountBoxes(); tab('home');
    alert('Sua conta foi excluída.');
  }catch(e){ stat('#pfDeleteStatus',e.message,false); }
  finally{ btn.disabled=false; }
}
/* ---------- Admin: backup dos dados ---------- */
const fmtSize=b=>b>1048576?(b/1048576).toFixed(1).replace('.',',')+' MB':Math.max(1,Math.round(b/1024))+' KB';
async function loadBackups(){
  if(!adminKey) return;
  const r=await fetch('/api/admin/backups',{headers:adminHeaders()});
  if(r.status===401){ clearAdmin(); return; }
  const d=await r.json().catch(()=>({}));
  if(!r.ok){ $('#backupList').innerHTML=`<p>${esc(d.detail||'Erro ao carregar.')}</p>`; return; }
  const last=d.backups[0];
  $('#backupInfo').textContent=`Automático a cada ${d.every_hours} h, guarda ${d.keep_days} dias. `
    +(last?`Último: ${new Date(last.created_at*1000).toLocaleString('pt-BR')}.`:'Nenhum backup ainda.')+` Pasta: ${d.folder}`;
  $('#backupList').innerHTML=d.backups.map(b=>`<div class="gang-item"><div class="gi-info"><b>${esc(b.name.replace(/^studio_|\.db$/g,''))}</b><small>${fmtSize(b.size)}</small></div>
      <button type="button" data-backup="${esc(b.name)}" aria-label="Baixar o backup ${esc(b.name)}">Baixar</button></div>`).join('')
    ||'<p class="small-note">O primeiro backup é feito quando o servidor liga.</p>';
  $$('[data-backup]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;
    try{
      const r=await fetch(`/api/admin/backups/${encodeURIComponent(b.dataset.backup)}`,{headers:adminHeaders()});
      if(r.status===401){ clearAdmin(); return; }
      if(!r.ok) throw new Error('Não foi possível baixar.');
      const a=document.createElement('a'); a.href=URL.createObjectURL(await r.blob()); a.download=b.dataset.backup; a.click();
      setTimeout(()=>URL.revokeObjectURL(a.href),10000);
    }catch(e){ stat('#backupStatus',e.message,false); }
    finally{ b.disabled=false; }
  });
}
async function backupNow(){
  const btn=$('#backupNow'); btn.disabled=true;
  try{
    const r=await fetch('/api/admin/backups',{method:'POST',headers:adminHeaders()}), d=await r.json().catch(()=>({}));
    if(r.status===401){ clearAdmin(); return; }
    if(!r.ok) throw new Error(d.detail||'Erro ao fazer o backup.');
    stat('#backupStatus','Backup feito: '+d.name,true);
    loadBackups();
  }catch(e){ stat('#backupStatus',e.message,false); }
  finally{ btn.disabled=false; }
}
/* ============ Admin: painel, pedidos, clientes, custos, artes, mockups ============ */
const ADM={pane:'dash',days:30,group:'day',orders:[],customers:[],dash:null,filter:'ALL',libSel:null};
const STAGES=[['PAGO','Pago'],['EM_ANALISE','Em análise'],['EM_FILA','Em fila'],['EM_IMPRESSAO','Em impressão'],['FINALIZADO','Finalizado'],['ENVIADO','Enviado']];
const STAGE_LABEL=Object.fromEntries(STAGES);
const ORDER_FILTERS=[['ALL','Todos'],['AGUARDANDO','Aguardando pagamento'],...STAGES,['PREMIUM','Premium'],['CANCELADO','Cancelados']];
const FILTER_LABEL=Object.fromEntries(ORDER_FILTERS);
// Em que ponto o pedido está, numa chave só (filtro, cor e contagem).
function orderStageKey(o){
  if(o.status==='CANCELADO') return 'CANCELADO';
  if(o.payment_status!=='PAID') return 'AGUARDANDO';
  if(o.sale_type==='PREMIUM_SUB') return 'PREMIUM';
  return o.stage||(o.status==='PRONTO'?'FINALIZADO':'PAGO');
}
async function admFetch(url,opts={}){
  const r=await fetch(url,{...opts,headers:adminHeaders(opts.headers)}), d=await r.json().catch(()=>({}));
  if(r.status===401){ clearAdmin(); openAdminLogin(); throw new Error('Entre no Admin de novo.'); }
  if(!r.ok) throw new Error(d.detail||'Erro ao falar com o servidor.');
  return d;
}
const pct=(a,b)=>b?(a/b*100).toFixed(1).replace('.',',')+'%':'–';
const localDay=t=>{const d=new Date(t);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
const waLink=phone=>{const d=String(phone||'').replace(/\D/g,'');return d?`https://wa.me/${d.length<=11?'55'+d:d}`:''};

function admShow(pane){
  ADM.pane=pane;
  $$('[data-adm]').forEach(b=>{const on=b.dataset.adm===pane; b.classList.toggle('on',on); b.setAttribute('aria-selected',String(on));});
  $$('.adm-pane').forEach(p=>p.hidden=p.dataset.pane!==pane);
  ({dash:loadDashboard,orders:loadOrders,customers:loadCustomers,costs:loadCosts,arts:renderLibraryAdmin,
    mockups:renderMockupsList,more:()=>{loadLgpdRequests();loadBackups()},security:loadSecurity}[pane]||(()=>{}))();
}
const STAFF_PANES={producao:['orders','security']};
function enterAdmin(){
  if(!adminKey){ openAdminLogin(); return; }
  const allowed=STAFF_PANES[adminRole];
  $('#admWho').textContent=`Logado como ${adminUser} · ${adminRole==='producao'?'Produção':'Admin'}`;
  $$('[data-adm]').forEach(b=>b.hidden=!!allowed&&!allowed.includes(b.dataset.adm));
  if(allowed&&!allowed.includes(ADM.pane)) ADM.pane='orders';
  admShow(ADM.pane);
}
function bindAdmin(){
  $$('[data-adm]').forEach(b=>b.onclick=()=>admShow(b.dataset.adm));
  $('#refreshOrders').onclick=()=>admShow(ADM.pane);
  $('#adminLogout').onclick=logoutAdmin;
  $('#staffForm').onsubmit=addStaff;
  $$('#admin [data-days]').forEach(b=>b.onclick=()=>{ADM.days=Number(b.dataset.days);$$('#admin [data-days]').forEach(x=>x.classList.toggle('on',x===b));loadDashboard()});
  $$('#admin [data-group]').forEach(b=>b.onclick=()=>{ADM.group=b.dataset.group;$$('#admin [data-group]').forEach(x=>x.classList.toggle('on',x===b));if(ADM.dash)renderDashboard()});
  $('#ordersSearch').oninput=renderOrders;
  $('#customersSearch').oninput=renderCustomers;
  $('#costsForm').onsubmit=saveCosts;
  $('#mockupToggle').onclick=()=>{const w=$('#mockupFormWrap'), open=w.hidden; w.hidden=!open; $('#mockupToggle').setAttribute('aria-expanded',String(open));
    $('#mockupToggle').textContent=open?'Fechar':'+ Adicionar ou trocar foto'; if(open) w.scrollIntoView({block:'nearest'});};
  const drop=$('#libUploadForm');
  ['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('drag')}));
  ['dragleave','dragend','drop'].forEach(e=>drop.addEventListener(e,()=>drop.classList.remove('drag')));
  drop.addEventListener('drop',ev=>{ev.preventDefault();uploadLibraryFiles([...ev.dataTransfer.files])});
  $('#libFiles').onchange=e=>{uploadLibraryFiles([...e.target.files]);e.target.value=''};
}

/* ---------- Painel ---------- */
async function loadDashboard(){
  if(!adminKey) return;
  $('#dashCards').innerHTML='<p class="adm-note">Carregando...</p>';
  try{
    const [dash,orders]=await Promise.all([admFetch(`/api/admin/dashboard?days=${ADM.days}`),admFetch('/api/orders')]);
    ADM.dash=dash; ADM.orders=orders;
  }catch(e){ $('#dashCards').innerHTML=`<p class="error">${esc(e.message)}</p>`; return; }
  renderDashboard();
}
function periodKey(day,group){
  if(group==='day') return day;
  if(group==='month') return day.slice(0,7);
  const d=new Date(day+'T12:00:00'); d.setDate(d.getDate()-((d.getDay()+6)%7)); return localDay(d.getTime()); // segunda-feira
}
function periodLabel(k,group){
  if(group==='month'){const [y,m]=k.split('-');return `${MONTHS[+m-1]}/${y.slice(2)}`;}
  const [,m,d]=k.split('-'); return `${group==='week'?'sem ':''}${d}/${m}`;
}
function renderDashboard(){
  const D=ADM.dash, g=ADM.group, rows={};
  const R=k=>rows[k]||(rows[k]={visits:0,signups:0,premium:0,orders:0,paid:0,revenue:0,cost:0});
  for(let t=Date.now()-(D.days-1)*864e5;t<=Date.now()+1;t+=864e5) R(periodKey(localDay(t),g));
  // "visit" = abriu o site (1 por aba). Antes dele existir, conta a página inicial.
  const hasVisit=D.hits.some(h=>h.page==='visit');
  D.hits.forEach(h=>{ if(h.page===(hasVisit?'visit':'home')) R(periodKey(h.day,g)).visits+=h.hits; });
  D.signups.forEach(s=>{ R(periodKey(s.day,g)).signups+=s.n; });
  D.orders.forEach(o=>{
    const x=R(periodKey(o.day,g)), paid=o.payment_status==='PAID';
    if(o.sale_type==='PREMIUM_SUB'){ if(paid) x.premium++; }
    else { x.orders++; if(paid) x.paid++; }
    if(paid){ x.revenue+=Number(o.total)||0; x.cost+=Number(o.cost)||0; }
  });
  const keys=Object.keys(rows).sort(), sum=k=>keys.reduce((a,x)=>a+rows[x][k],0);
  const visits=sum('visits'), revenue=sum('revenue'), cost=sum('cost'), profit=revenue-cost, paid=sum('paid');
  const T=D.totals||{};
  $('#dashCostsHint').hidden=!!D.costs_set;
  const card=(label,value,sub='',cls='')=>`<div class="adm-kpi ${cls}"><span>${label}</span><b>${value}</b>${sub?`<small>${sub}</small>`:''}</div>`;
  $('#dashCards').innerHTML=[
    card('Visitas',visits.toLocaleString('pt-BR'),'pessoas que abriram o site'),
    card('Contas criadas',sum('signups'),`${T.customers||0} no total`),
    card('Viraram premium',sum('premium'),`${T.premium_active||0} premium ativos`),
    card('Pedidos feitos',sum('orders'),`${paid} pagos · conversão ${pct(paid,visits)}`),
    card('Receita',money(revenue),`ticket médio ${paid?money(revenue/(paid+sum('premium'))):'–'}`,'money'),
    card('Custo',money(cost),D.costs_set?'pela aba Custos':'preencha a aba Custos','cost'),
    card('Lucro',money(profit),`margem ${pct(profit,revenue)}`,profit>=0?'profit':'loss'),
    card('Em produção agora',T.in_production||0,'pagos e não finalizados'),
  ].join('');
  const labels=keys.map(k=>periodLabel(k,g));
  barChart($('#dashChartMoney'),labels,[
    {name:'Receita',color:'#9b59b6',values:keys.map(k=>rows[k].revenue)},
    {name:'Custo',color:'#e2873d',values:keys.map(k=>rows[k].cost)},
    {name:'Lucro',color:'#2e9e6b',values:keys.map(k=>rows[k].revenue-rows[k].cost)}],v=>money(v));
  barChart($('#dashChartPeople'),labels,[
    {name:'Visitas',color:'#6a6ad1',values:keys.map(k=>rows[k].visits)},
    {name:'Contas novas',color:'#c39bd3',values:keys.map(k=>rows[k].signups)}],v=>String(Math.round(v)));
  renderDashStages();
  const list=[...keys].reverse().filter(k=>Object.values(rows[k]).some(Boolean));
  $('#dashTable').innerHTML=list.length?`<table class="adm-table"><thead><tr><th>${{day:'Dia',week:'Semana',month:'Mês'}[g]}</th><th>Visitas</th><th>Contas</th><th>Premium</th><th>Pedidos</th><th>Pagos</th><th>Receita</th><th>Custo</th><th>Lucro</th></tr></thead><tbody>
    ${list.map(k=>{const x=rows[k];return `<tr><td><b>${periodLabel(k,g)}</b></td><td>${x.visits}</td><td>${x.signups}</td><td>${x.premium}</td><td>${x.orders}</td><td>${x.paid}</td><td>${money(x.revenue)}</td><td>${money(x.cost)}</td><td class="${x.revenue-x.cost<0?'neg':''}">${money(x.revenue-x.cost)}</td></tr>`}).join('')}
    </tbody></table>`:'<p class="adm-empty">Sem movimento no período.</p>';
}
function renderDashStages(){
  const count={}; ADM.orders.forEach(o=>{const k=orderStageKey(o); count[k]=(count[k]||0)+1;});
  $('#dashStages').innerHTML=[['AGUARDANDO','Aguardando pagamento'],...STAGES].map(([k,l])=>
    `<button type="button" class="adm-stage st-${k}" data-goto-stage="${k}"><b>${count[k]||0}</b><span>${l}</span></button>`).join('');
  $$('#dashStages [data-goto-stage]').forEach(b=>b.onclick=()=>{ADM.filter=b.dataset.gotoStage;admShow('orders')});
}
// Gráfico de barras simples em SVG (sem biblioteca).
function barChart(el,labels,series,fmt){
  const W=640,H=230,pl=58,pb=28,pt=10,pr=8, n=labels.length||1;
  const vals=series.flatMap(s=>s.values), max=Math.max(1,...vals), min=Math.min(0,...vals), span=max-min;
  const y=v=>pt+(H-pt-pb)*(1-(v-min)/span), gw=(W-pl-pr)/n, bw=Math.max(1.5,Math.min(18,(gw-4)/series.length));
  const ticks=[0,.25,.5,.75,1].map(f=>min+span*f);
  const step=Math.ceil(n/12);
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map(s=>s.name).join(', '))}">
    ${ticks.map(v=>`<line x1="${pl}" x2="${W-pr}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${pl-6}" y="${y(v)+4}" class="ax" text-anchor="end">${esc(fmt(v))}</text>`).join('')}
    ${series.map((s,si)=>s.values.map((v,i)=>{const x=pl+gw*i+(gw-bw*series.length)/2+bw*si, y0=y(Math.max(0,v)), y1=y(Math.min(0,v));
      return `<rect x="${x}" y="${y0}" width="${bw}" height="${Math.max(0,y1-y0)}" rx="2" fill="${s.color}"><title>${esc(labels[i])} · ${esc(s.name)}: ${esc(fmt(v))}</title></rect>`}).join('')).join('')}
    ${labels.map((l,i)=>i%step?'':`<text x="${pl+gw*i+gw/2}" y="${H-8}" class="ax" text-anchor="middle">${esc(l)}</text>`).join('')}
  </svg><div class="adm-legend">${series.map(s=>`<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>`;
}

/* ---------- Pedidos ---------- */
async function loadOrders(){
  if(!adminKey) return;
  try{ ADM.orders=await admFetch('/api/orders'); }
  catch(e){ $('#ordersTable').innerHTML=`<p class="error">${esc(e.message)}</p>`; return; }
  renderOrders();
  if(ADM.pane==='dash'&&ADM.dash) renderDashStages();
}
function orderLine(o){
  const d=o.data||{}, p=d.product||{};
  if(o.sale_type==='DTF_ONLY'){const a=d.production_art||{}, dpi=Number(CONFIG.dpi)||300;
    return a.height_px?`chapa ${fmtCm(a.height_px/dpi*2.54)} cm · ${(d.sources||[]).length} arte(s)`:'chapa';}
  if(o.sale_type==='PREMIUM_SUB') return `${d.premium_days||30} dias`;
  const sizes=Object.entries(p.sizes||{}).filter(([,q])=>q>0).map(([s,q])=>`${s}×${q}`).join(' ');
  return `${p.shirt_model||''} ${p.color||''} · ${sizes}`;
}
function renderOrders(){
  const counts={ALL:ADM.orders.length}; ADM.orders.forEach(o=>{const k=orderStageKey(o);counts[k]=(counts[k]||0)+1;});
  $('#ordersFilters').innerHTML=ORDER_FILTERS.filter(([k])=>k==='ALL'||counts[k]).map(([k,l])=>
    `<button type="button" class="adm-chip st-${k}${ADM.filter===k?' on':''}" data-ofilter="${k}">${l} <b>${counts[k]||0}</b></button>`).join('');
  $$('#ordersFilters [data-ofilter]').forEach(b=>b.onclick=()=>{ADM.filter=b.dataset.ofilter;renderOrders()});
  const q=normTxt($('#ordersSearch').value.trim());
  const list=ADM.orders.filter(o=>(ADM.filter==='ALL'||orderStageKey(o)===ADM.filter)
    &&(!q||normTxt(`${o.order_number} ${o.customer_name} ${o.customer_email||''}`).includes(q)));
  $('#ordersTable').innerHTML=list.length?`<table class="adm-table adm-orders"><thead><tr>
      <th>Pedido</th><th>Cliente</th><th>Produto</th><th>Valor</th><th>Etapa</th><th>Ações</th></tr></thead><tbody>
    ${list.map(o=>{const k=orderStageKey(o), prod=o.payment_status==='PAID'&&o.sale_type!=='PREMIUM_SUB', wa=waLink(o.customer_whatsapp);
      const profit=o.cost==null?null:Number(o.total)-Number(o.cost);
      return `<tr>
        <td><b>${esc(o.order_number)}</b><small>${fmtDate(o.created_at)}</small></td>
        <td>${esc(o.customer_name)}<small>${esc(o.customer_email||'')}${wa?` · <a href="${wa}" target="_blank" rel="noopener">WhatsApp</a>`:''}</small></td>
        <td>${esc(ORDER_TYPES[o.sale_type]||o.sale_type)}<small>${esc(orderLine(o))}</small></td>
        <td><b>${money(o.total)}</b>${profit==null?'':`<small>custo ${money(o.cost)} · <span class="${profit<0?'neg':'pos'}">lucro ${money(profit)}</span></small>`}</td>
        <td>${prod?`<select class="adm-stage-sel st-${k}" data-stage="${o.id}" aria-label="Etapa do pedido ${esc(o.order_number)}">${STAGES.map(([sk,sl])=>`<option value="${sk}"${sk===k?' selected':''}>${sl}</option>`).join('')}</select>`
          :`<span class="adm-pill st-${k}">${FILTER_LABEL[k]}</span>`}${o.stage_by?`<small>por ${esc(o.stage_by)}${o.stage_at?` · ${new Date(o.stage_at*1000).toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'})}`:''}</small>`:''}</td>
        <td class="adm-actions">${adminRole!=='producao'&&o.payment_status!=='PAID'&&o.status!=='CANCELADO'?`<button type="button" class="adm-btn sm" data-pay="${o.id}" data-num="${esc(o.order_number)}" title="Pagamento recebido fora do site">Marcar pago</button>`:''}
          ${o.sale_type!=='PREMIUM_SUB'?`<button type="button" class="adm-btn sm ghost" data-files="${o.id}" data-name="${esc(o.order_number)}" title="Artes e dados do pedido num .zip">Baixar ZIP</button>`:''}</td>
      </tr>`;}).join('')}</tbody></table>`
    :`<p class="adm-empty">${ADM.orders.length?'Nenhum pedido com esse filtro.':'Nenhum pedido ainda.'}</p>`;
  $$('#ordersTable [data-stage]').forEach(sel=>sel.onchange=async()=>{
    sel.disabled=true;
    try{ const d=await admFetch(`/api/admin/orders/${sel.dataset.stage}/stage`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage:sel.value})});
      const o=ADM.orders.find(x=>x.id===sel.dataset.stage); o.stage=sel.value; o.stage_by=d.stage_by; o.stage_at=Date.now()/1000; if(['FINALIZADO','ENVIADO'].includes(sel.value)) o.status='PRONTO';
      renderOrders(); }
    catch(e){ alert(e.message); sel.disabled=false; }
  });
  $$('#ordersTable [data-pay]').forEach(b=>b.onclick=async()=>{
    // Libera a produção: com pagamento real no site, um clique errado sai caro.
    if(!confirm(`Marcar o pedido ${b.dataset.num} como pago? Use só se o pagamento foi recebido fora do site.`)) return;
    try{ await admFetch(`/api/orders/${b.dataset.pay}/mark-paid`,{method:'POST'}); loadOrders(); }catch(e){ alert(e.message); }
  });
  $$('#ordersTable [data-files]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;
    try{
      const r=await fetch(`/api/admin/orders/${b.dataset.files}/files`,{headers:adminHeaders()});
      if(r.status===401){ clearAdmin(); return openAdminLogin(); }
      if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.detail||'Erro ao baixar os arquivos do pedido'); }
      const a=document.createElement('a'); a.href=URL.createObjectURL(await r.blob()); a.download=`pedido_${b.dataset.name}.zip`; a.click();
      setTimeout(()=>URL.revokeObjectURL(a.href),10000);
    }catch(e){ alert(e.message); }
    finally{ b.disabled=false; }
  });
}

/* ---------- Clientes ---------- */
async function loadCustomers(){
  try{ ADM.customers=await admFetch('/api/admin/customers'); }
  catch(e){ $('#customersTable').innerHTML=`<p class="error">${esc(e.message)}</p>`; return; }
  renderCustomers();
}
function renderCustomers(){
  const q=normTxt($('#customersSearch').value.trim()), now=Date.now()/1000;
  const list=ADM.customers.filter(c=>!q||normTxt(`${c.full_name} ${c.email} ${c.phone}`).includes(q));
  const prem=ADM.customers.filter(c=>c.premium_until>now).length;
  $('#customersCount').textContent=`${plural(ADM.customers.length,'conta','contas')} · ${prem} premium ativo${prem===1?'':'s'}`;
  $('#customersTable').innerHTML=list.length?`<table class="adm-table"><thead><tr><th>Cliente</th><th>Telefone</th><th>Conta criada</th><th>Premium</th><th>Pedidos</th><th>Total pago</th></tr></thead><tbody>
    ${list.map(c=>{const wa=waLink(c.phone);return `<tr>
      <td><b>${esc(c.full_name)}</b><small>${esc(c.email)}${c.google?' · Google':''}</small></td>
      <td>${c.phone?`${esc(formatPhoneBR(c.phone))}${wa?`<small><a href="${wa}" target="_blank" rel="noopener">WhatsApp</a></small>`:''}`:'–'}</td>
      <td>${fmtDate(c.created_at)}</td>
      <td>${c.premium_until>now?`<span class="adm-pill st-PREMIUM">até ${fmtDate(c.premium_until)}</span>`:c.premium_until?'<small>vencido</small>':'–'}</td>
      <td>${c.orders}</td><td>${money(c.spent)}</td></tr>`}).join('')}</tbody></table>`
    :`<p class="adm-empty">${ADM.customers.length?'Nenhum cliente encontrado.':'Nenhuma conta criada ainda.'}</p>`;
}

/* ---------- Custos ---------- */
async function loadCosts(){
  let d; try{ d=await admFetch('/api/admin/costs'); }catch(e){ return stat('#costsStatus',e.message,false); }
  const f=$('#costsForm');
  ['dtf_per_meter','press_per_piece','packaging_per_order','payment_fee_pct'].forEach(k=>f.elements[k].value=d.costs[k]||'');
  $('#costsRoll').textContent=fmtCm(sheetWidthCm());
  $('#costsShirts').innerHTML=d.models.map(m=>`<label><span>${esc(m.name)}</span><div class="adm-money"><i>R$</i>
    <input type="number" min="0" step="0.01" data-shirt="${m.id}" value="${d.costs.shirt_cost[m.id]||''}"></div></label>`).join('');
}
async function saveCosts(ev){
  ev.preventDefault();
  const f=ev.currentTarget, body={shirt_cost:{}};
  ['dtf_per_meter','press_per_piece','packaging_per_order','payment_fee_pct'].forEach(k=>body[k]=f.elements[k].value||0);
  $$('#costsShirts [data-shirt]').forEach(i=>body.shirt_cost[i.dataset.shirt]=i.value||0);
  try{ await admFetch('/api/admin/costs',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    stat('#costsStatus','Custos salvos. O painel já usa os valores novos.',true); ADM.dash=null; }
  catch(e){ stat('#costsStatus',e.message,false); }
}

/* ---------- Artes (biblioteca) ---------- */
function renderLibraryAdmin(){
  const byName=list=>[...list].sort((a,b)=>a.name.localeCompare(b.name,'pt-BR',{numeric:true}));
  if(!libCat(ADM.libSel)) ADM.libSel=(byName(libSubs('free'))[0]||byName(libSubs('premium'))[0]||{}).id||null;
  $('#libTree').innerHTML=LIB_SIDES.filter(libCat).map(id=>`<div class="adm-lib-group"><div class="adm-lib-gtitle ${id}">${id==='premium'?'★':'↓'} ${esc(libCat(id).name)}
      <small>${plural(libTotal(id),'arte','artes')}</small></div>
      ${byName(libSubs(id)).map(c=>`<button type="button" class="adm-lib-folder${c.id===ADM.libSel?' on':''}" data-lib-sel="${c.id}">
        <span>${esc(c.name)}</span><small>${c.art_count||0}</small></button>`).join('')||'<p class="adm-note">Nenhuma pasta.</p>'}</div>`).join('');
  $$('#libTree [data-lib-sel]').forEach(b=>b.onclick=()=>{ADM.libSel=b.dataset.libSel;renderLibraryAdmin()});
  const c=libCat(ADM.libSel);
  $('#libUploadForm').hidden=!c;
  if(!c){ $('#libFolderHead').innerHTML='<h3>Crie uma pasta pra começar</h3>'; $('#libAdminArts').innerHTML=''; return; }
  const to=c.access==='premium'?'free':'premium';
  $('#libFolderHead').innerHTML=`<div><h3>${esc(c.name)}</h3><span class="lib-badge ${c.access}">${ACCESS_LABEL[c.access]}</span> <small class="adm-note">${plural(c.art_count||0,'arte','artes')}</small></div>
    <div class="adm-lib-actions"><button type="button" class="adm-btn sm ghost" data-lib-act="rename">Renomear</button>
      <button type="button" class="adm-btn sm ghost" data-lib-act="move">Mover para ${ACCESS_LABEL[to]}</button>
      <button type="button" class="adm-btn sm danger" data-lib-act="delete">Excluir pasta</button></div>`;
  const act=async fn=>{try{await fn();await loadLibrary()}catch(e){alert(e.message)}};
  const patch=body=>libAdminFetch(`/api/admin/library/categories/${c.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  $$('#libFolderHead [data-lib-act]').forEach(b=>b.onclick=()=>{
    const a=b.dataset.libAct;
    if(a==='rename'){const name=prompt('Novo nome da pasta:',c.name); if(name&&name.trim()) act(()=>patch({name}));}
    if(a==='move'&&confirm(`Mover "${c.name}" para ${ACCESS_LABEL[to]}? ${c.art_count?`As ${plural(c.art_count,'arte','artes')} passam a ser ${to==='premium'?'premium':'grátis'}.`:''}`)) act(()=>patch({parent_id:to}));
    if(a==='delete'&&confirm(`Excluir a pasta "${c.name}" e ${plural(c.art_count||0,'arte','artes')}? Os arquivos são apagados e não dá para desfazer.`))
      act(()=>libAdminFetch(`/api/admin/library/categories/${c.id}`,{method:'DELETE'}));
  });
  renderLibraryAdminArts(c);
}
async function renderLibraryAdminArts(c){
  const box=$('#libAdminArts');
  if(!libArts[c.id]){ box.innerHTML='<p class="adm-note">Carregando artes...</p>';
    try{ const r=await fetch(`/api/library/categories/${c.id}/arts`); libArts[c.id]=r.ok?await r.json().catch(()=>[]):[]; }catch{ libArts[c.id]=[]; } }
  if(ADM.libSel!==c.id) return;
  const arts=[...libArts[c.id]].sort((a,b)=>b.created_at-a.created_at);
  arts.forEach(a=>{libArtIndex[a.id]={name:a.name,access:c.access}});
  box.innerHTML=arts.map(a=>`<figure class="adm-art"><div><img src="/api/library/arts/${a.id}/thumb" alt="" loading="lazy"></div>
      <figcaption title="${esc(a.name)}">${esc(a.name)}</figcaption><button type="button" data-lib-del="${a.id}" aria-label="Excluir ${esc(a.name)}" title="Excluir">🗑</button></figure>`).join('')
    ||'<p class="adm-empty">Nenhuma arte nesta pasta. Envie pela área acima.</p>';
  $$('#libAdminArts [data-lib-del]').forEach(b=>b.onclick=()=>deleteLibraryArt(b.dataset.libDel));
}
async function createLibraryCategory(ev){
  ev.preventDefault();
  try{
    const d=await libAdminFetch('/api/admin/library/categories',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:$('#libCatName').value,parent_id:$('#libParent').value})});
    $('#libCatName').value=''; stat('#libCatStatus','Pasta criada!',true); ADM.libSel=d.id; await loadLibrary();
  }catch(e){ stat('#libCatStatus',e.message,false); }
}
function uploadLibraryArts(ev){ ev.preventDefault(); }
async function uploadLibraryFiles(files){
  const target=ADM.libSel;
  files=files.filter(f=>/^image\//.test(f.type));
  if(!target) return stat('#libUploadStatus','Crie ou escolha uma pasta primeiro.',false);
  if(!files.length) return stat('#libUploadStatus','Escolha imagens PNG, JPG ou WEBP.',false);
  const fd=new FormData(); fd.append('category_id',target); files.forEach(f=>fd.append('files',f));
  $('#libUploadForm').classList.add('busy');
  stat('#libUploadStatus',`Enviando ${plural(files.length,'arte','artes')}...`,true);
  try{
    const d=await libAdminFetch('/api/admin/library/arts',{method:'POST',body:fd});
    stat('#libUploadStatus',`${d.saved} ${d.saved===1?'arte enviada':'artes enviadas'}${d.skipped.length?`. Ignorados: ${d.skipped.join(', ')}`:''}.`,true);
    delete libArts[target]; await loadLibrary();
  }catch(e){ stat('#libUploadStatus',e.message,false); }
  finally{ $('#libUploadForm').classList.remove('busy'); }
}

/* ---------- Mockups ---------- */
function renderMockupsList(){
  const models=(CONFIG?.shirt_models)||[], byModel={};
  shirtMockups.forEach(m=>(byModel[m.model_id]||(byModel[m.model_id]=[])).push(m));
  const ids=[...models.map(m=>m.id),...Object.keys(byModel).filter(id=>!models.some(m=>m.id===id))];
  $('#mockupsList').innerHTML=ids.filter(id=>byModel[id]).map(id=>{
    const name=models.find(m=>m.id===id)?.name||id;
    const list=byModel[id].sort((a,b)=>(a.color+a.side).localeCompare(b.color+b.side));
    return `<div class="adm-mk-group"><h4>${esc(name)} <small>${plural(list.length,'foto','fotos')}</small></h4><div class="adm-mk-grid">
      ${list.map(m=>`<figure class="adm-mk"><div><img src="${m.preview_url}" alt="" loading="lazy"></div>
        <figcaption><b>${esc(m.color)}</b> · ${m.side==='BACK'?'Costas':'Frente'}</figcaption>
        <button type="button" data-delmock="${m.id}" aria-label="Remover foto ${esc(name)} ${esc(m.color)}" title="Remover">🗑</button></figure>`).join('')}</div></div>`;
  }).join('')||'<p class="adm-empty">Nenhuma foto cadastrada: o editor usa o desenho padrão.</p>';
  $$('#mockupsList [data-delmock]').forEach(b=>b.onclick=async()=>{
    if(!confirm('Remover esta foto de mockup?')) return;
    try{ await admFetch(`/api/admin/shirt-mockups/${b.dataset.delmock}`,{method:'DELETE'}); }catch(e){ return alert(e.message); }
    await loadShirtMockups(); renderMockupsList(); updateShirtVisual();
  });
}

/* ---------- Admin: segurança (2FA e equipe) ---------- */
async function loadSecurity(){
  let me;
  try{ me=await admFetch('/api/admin/me'); }catch(e){ return; }
  const badge=$('#tfaBadge');
  badge.textContent=me.two_factor?'Ligada':'Desligada';
  badge.style.setProperty('--sc',me.two_factor?'#2e9e6b':'#9a9aa0');
  $('#tfaBody').innerHTML=me.two_factor
    ?`<p class="adm-note">Seu login já pede o código do app. Para desligar, confirme a senha e um código.</p>
      <form id="tfaOff" class="adm-staff-form"><label><span class="adm-label">Senha</span><input id="tfaOffPass" type="password" required autocomplete="current-password"></label>
      <label><span class="adm-label">Código</span><input id="tfaOffCode" inputmode="numeric" maxlength="7" required></label>
      <button class="adm-btn danger">Desligar 2FA</button></form><div id="tfaStatus" role="status" aria-live="polite"></div>`
    :`<button type="button" id="tfaStart" class="primary">Ligar verificação em duas etapas</button><div id="tfaSetup"></div><div id="tfaStatus" role="status" aria-live="polite"></div>`;
  if(me.two_factor){
    $('#tfaOff').onsubmit=async ev=>{ev.preventDefault();
      try{ await admFetch('/api/admin/2fa/disable',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$('#tfaOffPass').value,code:$('#tfaOffCode').value})});
        loadSecurity(); }catch(e){ stat('#tfaStatus',e.message,false); } };
  }else{
    $('#tfaStart').onclick=startTwoFactor;
  }
  $('#staffCard').hidden=me.role!=='admin';
  if(me.role==='admin') loadStaff();
}
async function startTwoFactor(){
  try{
    const d=await admFetch('/api/admin/2fa/setup',{method:'POST'});
    $('#tfaStart').hidden=true;
    $('#tfaSetup').innerHTML=`<div class="adm-tfa">
      <canvas id="tfaQr" width="220" height="220" aria-label="QR code para o app autenticador"></canvas>
      <ol><li>Abra o app autenticador no celular e escolha <b>adicionar conta</b> / <b>ler QR code</b>.</li>
      <li>Aponte para o QR code. Sem câmera? Digite a chave: <code>${esc(d.secret.replace(/(.{4})/g,'$1 ').trim())}</code></li>
      <li>Digite o código de 6 dígitos que aparece no app:</li></ol>
      <form id="tfaConfirm" class="adm-staff-form"><input id="tfaCode" inputmode="numeric" autocomplete="one-time-code" maxlength="7" placeholder="000 000" required>
      <button class="primary">Confirmar e ligar</button></form></div>`;
    try{ await loadScriptOnce('https://cdnjs.cloudflare.com/ajax/libs/qrious/4.0.2/qrious.min.js'); new QRious({element:$('#tfaQr'),value:d.uri,size:220,padding:12}); }
    catch{ $('#tfaQr').hidden=true; }
    $('#tfaCode').focus();
    $('#tfaConfirm').onsubmit=async ev=>{ev.preventDefault();
      try{ await admFetch('/api/admin/2fa/enable',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:$('#tfaCode').value})});
        await loadSecurity(); stat('#tfaStatus','Pronto! A partir de agora o login do Admin pede o código do app.',true); }
      catch(e){ stat('#tfaStatus',e.message,false); } };
  }catch(e){ stat('#tfaStatus',e.message,false); }
}
async function loadStaff(){
  let list=[];
  try{ list=await admFetch('/api/admin/staff'); }catch(e){ return stat('#staffStatus',e.message,false); }
  $('#staffTable').innerHTML=`<table class="adm-table"><thead><tr><th>Usuário</th><th>Papel</th><th>2FA</th><th>Origem</th><th></th></tr></thead><tbody>
    ${list.map(u=>`<tr><td><b>${esc(u.user)}</b>${u.user===adminUser?' <small>(você)</small>':''}</td><td>${esc(u.role_name)}</td>
      <td>${u.two_factor?'<span class="pos">Ligado</span>':'<span class="neg">Desligado</span>'}</td>
      <td>${u.source==='config'?'config.json':'Criado no Admin'}</td>
      <td class="adm-actions">${u.two_factor&&u.user!==adminUser?`<button type="button" class="adm-btn sm" data-reset2fa="${esc(u.user)}">Zerar 2FA</button>`:''}
        ${u.source!=='config'&&u.user!==adminUser?`<button type="button" class="adm-btn sm danger" data-delstaff="${esc(u.user)}">Remover</button>`:''}</td></tr>`).join('')}
    </tbody></table>`;
  $$('[data-reset2fa]').forEach(b=>b.onclick=async()=>{ if(!confirm(`Desligar o 2FA de ${b.dataset.reset2fa}? Use quando a pessoa perdeu o celular.`)) return;
    try{ await admFetch(`/api/admin/staff/${encodeURIComponent(b.dataset.reset2fa)}/2fa`,{method:'DELETE'}); loadStaff(); }catch(e){ stat('#staffStatus',e.message,false); } });
  $$('[data-delstaff]').forEach(b=>b.onclick=async()=>{ if(!confirm(`Remover o usuário ${b.dataset.delstaff}?`)) return;
    try{ await admFetch(`/api/admin/staff/${encodeURIComponent(b.dataset.delstaff)}`,{method:'DELETE'}); loadStaff(); }catch(e){ stat('#staffStatus',e.message,false); } });
}
async function addStaff(ev){
  ev.preventDefault();
  try{
    await admFetch('/api/admin/staff',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user:$('#staffUser').value.trim(),password:$('#staffPass').value,role:$('#staffRole').value})});
    $('#staffForm').reset(); stat('#staffStatus','Usuário criado.',true); loadStaff();
  }catch(e){ stat('#staffStatus',e.message,false); }
}

/* ---------- Admin: solicitações LGPD ---------- */
const LGPD_STATUS={ABERTO:'Aberto',EM_ANDAMENTO:'Em andamento',CONCLUIDO:'Concluído',RECUSADO:'Recusado'};
async function loadLgpdRequests(){
  if(!adminKey) return;
  const r=await fetch('/api/admin/lgpd/requests',{headers:adminHeaders()});
  if(r.status===401){ clearAdmin(); return; }
  const list=await r.json().catch(()=>[]);
  if(!r.ok){ $('#lgpdTable').innerHTML=`<p>${esc(list.detail||'Erro ao carregar.')}</p>`; return; }
  const now=Date.now()/1000;
  $('#lgpdTable').innerHTML=list.length?`<table class="orders-table"><thead><tr>
      <th>Protocolo</th><th>Titular</th><th>Pedido</th><th>Mensagem</th><th>Prazo</th><th>Situação</th></tr></thead><tbody>
    ${list.map(q=>{const open=q.status==='ABERTO'||q.status==='EM_ANDAMENTO', late=open&&now>q.due_at;
      return `<tr><td><b>${esc(q.protocol)}</b><br><small>${fmtDate(q.created_at)}</small></td>
        <td>${esc(q.name)}<br><small>${esc(q.email)}${q.document?' · '+esc(q.document):''}${q.customer_id?' · tem conta':''}</small></td>
        <td>${esc(q.kind_label)}</td><td style="max-width:320px;white-space:pre-wrap">${esc(q.message)||'-'}</td>
        <td>${open?`<span class="badge"${late?' style="background:#fdd;color:#b11"':''}>${late?'ATRASADO · ':''}${fmtDate(q.due_at)}</span>`:(q.answered_at?'respondido '+fmtDate(q.answered_at):'-')}</td>
        <td><select data-lgpd="${q.id}">${Object.entries(LGPD_STATUS).map(([k,l])=>`<option value="${k}"${k===q.status?' selected':''}>${l}</option>`).join('')}</select></td></tr>`;}).join('')}
    </tbody></table>`:'<p>Nenhuma solicitação.</p>';
  $$('[data-lgpd]').forEach(s=>s.onchange=async()=>{
    const r=await fetch(`/api/admin/lgpd/requests/${s.dataset.lgpd}`,{method:'PATCH',headers:adminHeaders({'Content-Type':'application/json'}),body:JSON.stringify({status:s.value})});
    if(r.status===401){ clearAdmin(); return; }
    if(!r.ok) alert('Não foi possível salvar a situação.');
    loadLgpdRequests();
  });
}
function stat(sel,msg,ok){const e=$(sel);e.textContent=msg;e.className=ok?'success':'error'}

/* ============ Chapa de produção (DTF gang sheet) ============ */
const GANG_SCALE=6.5; // px por cm na tela (a exportação final usa o DPI de verdade) — só afeta o zoom visual, a chapa continua com o tamanho real em cm
let gangItems=[];   // {id, img, url, name, wCm, hCm, qty}
let gangPieces=[];  // instâncias já posicionadas: {itemId,img,wCm,hCm,xCm,yCm,angle,flipX,flipY}
let gangHeightCm=20;
let gangDrag=null;
let gangSelected=null;       // única peça selecionada (controla os handles de girar/esticar)
let gangSelectedSet=new Set(); // seleção múltipla (shift+clique / quadrante)
let gangAnchor=null;         // âncora do shift+clique
let gangMarquee=null;        // quadrante sendo arrastado pra selecionar
const gangCanvas=$('#gangCanvas'), gctx=gangCanvas.getContext('2d');
function setGangSelection(pieces){
  gangSelectedSet=new Set(pieces);
  gangSelected=gangSelectedSet.size===1 ? [...gangSelectedSet][0] : null;
  updateGangPieceButtons();
}
function piecesInRect(x1,y1,x2,y2){
  const rx1=Math.min(x1,x2), rx2=Math.max(x1,x2), ry1=Math.min(y1,y2), ry2=Math.max(y1,y2);
  return gangPieces.filter(p=>p.xCm<rx2 && p.xCm+p.wCm>rx1 && p.yCm<ry2 && p.yCm+p.hCm>ry1);
}

function sheetWidthCm(){return Number(CONFIG.dtf_sheet_width_cm)||58.5}
function sheetMinHeightCm(){return Number(CONFIG.dtf_sheet_min_height_cm)||200}

function onGangUpload(e){
  const f=e.target.files[0];
  if(f) handleGangFile(f);
}
/* PDF e TIFF: o navegador não mostra sozinho. Os leitores (pdf.js e UTIF)
   só são baixados quando o cliente escolhe um arquivo desses. O arquivo
   original vai junto no pedido; a produção usa ele, não a prévia. */
const GANG_LIBS={
  pdf:['https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js'],
  tiff:['https://cdnjs.cloudflare.com/ajax/libs/pako/1.0.11/pako.min.js','https://cdn.jsdelivr.net/npm/utif@3.1.0/UTIF.min.js']
};
const gangLibLoads={};
function loadScriptOnce(src){
  return new Promise((res,rej)=>{const s=document.createElement('script');s.src=src;s.onload=res;
    s.onerror=()=>{s.remove();rej(new Error('Não foi possível abrir esse tipo de arquivo agora. Confira sua internet e tente de novo.'))};
    document.head.appendChild(s);});
}
function needGangLib(kind){
  if(!gangLibLoads[kind]) gangLibLoads[kind]=(async()=>{for(const u of GANG_LIBS[kind]) await loadScriptOnce(u);})()
    .catch(e=>{delete gangLibLoads[kind];throw e;});
  return gangLibLoads[kind];
}
function gangFileKind(f){
  const n=(f.name||'').toLowerCase();
  if(f.type==='application/pdf'||n.endsWith('.pdf')) return 'pdf';
  if(f.type==='image/tiff'||/\.tiff?$/.test(n)) return 'tiff';
  if(/^image\/(png|jpeg|webp)$/.test(f.type)||/\.(png|jpe?g|webp)$/.test(n)) return 'png';
  return null;
}
const canvasToURL=c=>new Promise((res,rej)=>c.toBlob(b=>b?res(URL.createObjectURL(b)):rej(new Error('Arquivo grande demais para mostrar.')),'image/png'));
async function pdfPreview(f){
  await needGangLib('pdf');
  pdfjsLib.GlobalWorkerOptions.workerSrc='https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
  const doc=await pdfjsLib.getDocument({data:new Uint8Array(await f.arrayBuffer())}).promise;
  const page=await doc.getPage(1), base=page.getViewport({scale:1});
  // Prévia em até ~4000 px no lado maior (a produção usa o PDF original, em vetor).
  const scale=Math.min(300/72, 4000/Math.max(base.width,base.height));
  const vp=page.getViewport({scale}), c=document.createElement('canvas');
  c.width=Math.max(1,Math.round(vp.width)); c.height=Math.max(1,Math.round(vp.height));
  await page.render({canvasContext:c.getContext('2d'),viewport:vp,background:'rgba(0,0,0,0)'}).promise;
  doc.destroy();
  return {url:await canvasToURL(c), widthCm:base.width/72*2.54};
}
async function tiffPreview(f){
  await needGangLib('tiff');
  const buf=await f.arrayBuffer(), ifds=UTIF.decode(buf), ifd=ifds[0];
  if(!ifd) throw new Error('Esse TIFF não tem imagem.');
  UTIF.decodeImage(buf,ifd);
  const rgba=UTIF.toRGBA8(ifd), c=document.createElement('canvas');
  c.width=ifd.width; c.height=ifd.height;
  c.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(rgba.buffer,rgba.byteOffset,rgba.length),ifd.width,ifd.height),0,0);
  // Tamanho físico gravado no arquivo (XResolution, em dpi), quando existe.
  const xr=Array.isArray(ifd.t282)?Number(ifd.t282[0]):Number(ifd.t282), unit=ifd.t296?Number(ifd.t296[0]):2;
  const dpi=xr>0?(unit===3?xr*2.54:xr):0;
  return {url:await canvasToURL(c), widthCm:dpi>=72?ifd.width/dpi*2.54:0};
}
async function handleGangFile(f){
  const kind=gangFileKind(f);
  if(!kind) return stat('#gangAddStatus','Use PNG, PDF, TIFF, JPG ou WEBP.',false);
  if(kind==='png') return handleGangImageURL(URL.createObjectURL(f), f.name, null, {file:f,kind});
  stat('#gangAddStatus',`Abrindo o ${kind.toUpperCase()}...`,true);
  try{
    const p=kind==='pdf'?await pdfPreview(f):await tiffPreview(f);
    handleGangImageURL(p.url, f.name, null, {file:f,kind,widthCm:p.widthCm});
  }catch(e){ console.error(e); stat('#gangAddStatus',e.message||'Não foi possível abrir esse arquivo.',false); }
}
let gangPendingSource=null; // {file, kind, widthCm} do arquivo que o cliente escolheu
function handleGangImageURL(url,name,premiumId=null,source=null){
  const img=new Image();
  img.crossOrigin='anonymous';
  img.onload=()=>{
    gangPendingSource=source;
    $('#gangWidthCm').dataset.ratio=String(img.height/img.width);
    const natural=source&&source.widthCm>=3?Math.min(Number(source.widthCm.toFixed(1)),sheetWidthCm()-1.2):0;
    if(natural) $('#gangWidthCm').value=natural;
    else if(!$('#gangWidthCm').value)$('#gangWidthCm').value=18;
    $('#gangWidthCm').dataset.pendingUrl=url;
    $('#gangWidthCm').dataset.pendingName=name;
    $('#gangWidthCm').dataset.pendingPremium=premiumId||'';
    gangPendingImg=img;
    updateGangHeightPreview();
    $('#gangDropImg').src=url;
    $('#gangDropName').textContent=name;
    $('#gangDrop').classList.add('has-file');
    stat('#gangAddStatus','Imagem carregada — confira a largura e clique em Adicionar.',true);
  };
  img.onerror=()=>stat('#gangAddStatus','Não foi possível carregar essa imagem.',false);
  img.src=url;
}
async function useGangCatalog(id){
  const a=artworks.find(x=>x.id===id);
  if(a) handleGangImageURL(a.preview_url, a.name);
}
async function generateGangAI(){
  const prompt=$('#gangAiPrompt').value.trim();
  if(!prompt)return;
  if(!currentCustomer){$('#gangAiStatus').textContent='Entre na sua conta para gerar com IA.';return openAuthModal()}
  const btn=$('#gangGenerateAI');btn.disabled=true; // cada clique é uma geração paga
  $('#gangAiStatus').textContent='Gerando...';
  try{
    const d=await requestAI(prompt);
    handleGangImageURL(d.url,'Arte IA');
    $('#gangAiStatus').textContent=`Gerada com ${d.model}`;
  }catch(e){ $('#gangAiStatus').textContent=e.message; }
  finally{ btn.disabled=false; }
}
let gangPendingImg=null;
function updateGangHeightPreview(){
  const ratio=Number($('#gangWidthCm').dataset.ratio||0);
  const w=Number($('#gangWidthCm').value)||0;
  $('#gangHeightCm').value = ratio&&w ? (w*ratio).toFixed(1) : '';
}
function addGangImage(){
  if(!gangPendingImg)return stat('#gangAddStatus','Selecione uma imagem primeiro.',false);
  const wCm=Number($('#gangWidthCm').value);
  if(!wCm||wCm<=0)return stat('#gangAddStatus','Informe a largura.',false);
  const ratio=Number($('#gangWidthCm').dataset.ratio||(gangPendingImg.height/gangPendingImg.width));
  const hCm=Number((wCm*ratio).toFixed(2));
  const maxW=sheetWidthCm()-1.2; // largura do rolo menos a margem dos dois lados
  if(wCm>maxW && hCm>maxW) return stat('#gangAddStatus',`A imagem não cabe na largura da chapa (máx. ${fmtCm(maxW)} cm).`,false);
  const qty=Math.max(1,Math.floor(Number($('#gangQty').value))||1);
  const newItem={
    id:crypto.randomUUID(), img:gangPendingImg,
    url:$('#gangWidthCm').dataset.pendingUrl,
    name:$('#gangWidthCm').dataset.pendingName||'Sem título',
    premiumId:$('#gangWidthCm').dataset.pendingPremium||null,
    wCm, hCm, qty,
    file:gangPendingSource?gangPendingSource.file:null,
    kind:gangPendingSource?gangPendingSource.kind:'png'
  };
  gangItems.push(newItem);
  gangPendingImg=null; gangPendingSource=null;
  $('#gangUpload').value=''; $('#gangWidthCm').value=''; $('#gangHeightCm').value=''; $('#gangQty').value=1;
  delete $('#gangWidthCm').dataset.ratio;
  $('#gangDrop').classList.remove('has-file');
  $('#gangDropImg').src='';
  $('#gangDropName').textContent='';
  stat('#gangAddStatus','Imagem adicionada à chapa.',true);
  renderGangList();
  appendGangPieces(newItem);
  renderGangCanvas();
  updatePrice();
}
/* Nova imagem sempre entra no final da fila (embaixo das já colocadas),
   sem reorganizar o que o cliente já posicionou/girou/esticou. */
function appendGangPieces(it){
  const width=sheetWidthCm(), gap=0.6;
  const startY=gangPieces.reduce((m,p)=>Math.max(m,p.yCm+p.hCm),0) + (gangPieces.length?gap:gap);
  let x=gap, y=startY, shelfH=0;
  // Mais larga que o rolo: entra deitada (igual ao packGangSheet).
  let w=it.wCm, h=it.hCm, angle=0;
  if(w>width-2*gap && h<=width-2*gap){ [w,h]=[h,w]; angle=90; }
  for(let i=0;i<it.qty;i++){
    if(x+w > width-gap){ y+=shelfH+gap; x=gap; shelfH=0; }
    gangPieces.push({itemId:it.id,img:it.img,wCm:w,hCm:h,xCm:x,yCm:y,angle,flipX:false,flipY:false});
    x+=w+gap; shelfH=Math.max(shelfH,h);
  }
  recalcGangCanvasHeight();
}
/* Lista "Imagens": cada arte com a medida (largura × altura) e a quantidade.
   A medida é a mesma da chapa: esticar uma peça na chapa muda a medida da arte
   (e de todas as cópias dela), e mudar aqui muda as peças na chapa. */
const r1=v=>Math.round(Number(v)*10)/10;
const gangMaxW=()=>sheetWidthCm()-1.2; // largura do rolo menos a margem dos dois lados
function pieceUnrotated(p){ const odd=(p.angle||0)%180===90; return odd?{w:p.hCm,h:p.wCm}:{w:p.wCm,h:p.hCm}; }
function applyItemSizeToPieces(it,except){
  gangPieces.filter(o=>o.itemId===it.id&&o!==except).forEach(o=>{
    const odd=(o.angle||0)%180===90;
    o.wCm=odd?it.hCm:it.wCm; o.hCm=odd?it.wCm:it.hCm;
    clampGangPiece(o);
  });
}
// Depois de esticar uma peça na chapa: a arte passa a ter essa medida.
function syncGangItemFromPiece(p){
  const it=p&&gangItems.find(x=>x.id===p.itemId);
  if(!it) return;
  const s=pieceUnrotated(p);
  it.wCm=r1(s.w); it.hCm=r1(s.h);
  applyItemSizeToPieces(it,p);
  recalcGangCanvasHeight();
  renderGangList();
}
function setGangItemSize(it,dim,value){
  let v=Number(String(value).replace(',','.'));
  if(!(v>0)) return renderGangList();
  const ratio=it.hCm/it.wCm;
  let w=dim==='w'?v:v/ratio;
  w=Math.min(Math.max(w,1),gangMaxW());
  it.wCm=r1(w); it.hCm=r1(w*ratio);
  applyItemSizeToPieces(it,null);
  renderGangList(); autoArrangeGang(true);
}
function setGangItemQty(it,qty){
  it.qty=Math.max(1,Math.min(999,Math.floor(Number(qty))||1));
  renderGangList(); autoArrangeGang(true);
}
function renderGangList(){
  $('#gangCount').textContent=gangItems.length;
  $('#gangList').innerHTML=gangItems.map(it=>`
    <div class="gl-item" data-item="${it.id}">
      <div class="gl-top">
        <img src="${it.url}" alt="">
        <div class="gl-name"><b title="${esc(it.name)}">${esc(it.name)}</b><small>${fmtCm(it.wCm)} × ${fmtCm(it.hCm)} cm · ${it.qty} ${it.qty===1?'peça':'peças'}</small></div>
        <button type="button" class="gl-del" data-del="${it.id}" title="Remover" aria-label="Remover ${esc(it.name)}">🗑</button>
      </div>
      <div class="gl-controls">
        <div class="gl-size">
          <label><span>Largura</span><input type="number" min="1" step="0.5" value="${r1(it.wCm)}" data-gl-w="${it.id}" aria-label="Largura em cm de ${esc(it.name)}"></label>
          <span class="gl-x" aria-hidden="true">×</span>
          <label><span>Altura</span><input type="number" min="1" step="0.5" value="${r1(it.hCm)}" data-gl-h="${it.id}" aria-label="Altura em cm de ${esc(it.name)}"></label>
          <span class="gl-unit">cm</span>
        </div>
        <div class="gl-qty" role="group" aria-label="Quantidade de ${esc(it.name)}">
          <span>Qtd</span>
          <button type="button" data-q="${it.id}:-1" aria-label="Menos uma">−</button>
          <input type="number" min="1" value="${it.qty}" data-qty="${it.id}" aria-label="Quantidade">
          <button type="button" data-q="${it.id}:1" aria-label="Mais uma">+</button>
        </div>
      </div>
    </div>
  `).join('') || '<p class="gl-empty">Nenhuma imagem adicionada.</p>';
  const item=id=>gangItems.find(x=>x.id===id);
  $$('#gangList [data-gl-w]').forEach(inp=>inp.onchange=()=>{const it=item(inp.dataset.glW); if(it) setGangItemSize(it,'w',inp.value)});
  $$('#gangList [data-gl-h]').forEach(inp=>inp.onchange=()=>{const it=item(inp.dataset.glH); if(it) setGangItemSize(it,'h',inp.value)});
  $$('#gangList [data-qty]').forEach(inp=>inp.onchange=()=>{const it=item(inp.dataset.qty); if(it) setGangItemQty(it,inp.value)});
  $$('#gangList [data-q]').forEach(b=>b.onclick=()=>{const [id,d]=b.dataset.q.split(':'), it=item(id); if(it) setGangItemQty(it,it.qty+Number(d))});
  $$('#gangList [data-del]').forEach(b=>b.onclick=()=>{
    gangItems=gangItems.filter(x=>x.id!==b.dataset.del);
    renderGangList(); autoArrangeGang(true);
  });
}

/* Empacotamento por "prateleiras": ordena as peças da mais alta pra mais
   baixa e vai enchendo linhas dentro da largura do rolo, tentando girar
   90° a peça quando isso ajuda a aproveitar melhor o espaço restante. */
function packGangSheet(){
  const width=sheetWidthCm(), gap=0.6;
  const pieces=[];
  gangItems.forEach(it=>{
    for(let i=0;i<it.qty;i++) pieces.push({itemId:it.id,img:it.img,wCm:it.wCm,hCm:it.hCm,flipX:false,flipY:false});
  });
  pieces.sort((a,b)=>b.hCm-a.hCm);

  let x=gap, y=gap, shelfH=0;
  const placed=[];
  pieces.forEach(p=>{
    let w=p.wCm, h=p.hCm, angle=0;
    if(w>width-2*gap && h<=width-2*gap){ [w,h]=[h,w]; angle=90; }
    if(x+w > width-gap){ y+=shelfH+gap; x=gap; shelfH=0; }
    if(w>width-2*gap){
      // Peça maior que o rolo mesmo girada: encaixa do jeito que dá.
      w=Math.min(w,width-2*gap);
    }
    placed.push({...p, xCm:x, yCm:y, wCm:w, hCm:h, angle});
    x+=w+gap; shelfH=Math.max(shelfH,h);
  });
  const totalH=y+shelfH+gap;
  return {placed, totalH: Math.max(totalH, sheetMinHeightCm())};
}
function autoArrangeGang(rerender){
  const {placed,totalH}=packGangSheet();
  gangPieces=placed; gangHeightCm=totalH;
  gangAnchor=null; setGangSelection([]);
  if(rerender) renderGangCanvas();
  updatePrice();
}
function clampGangPiece(p){
  const width=sheetWidthCm();
  p.xCm=Math.min(Math.max(0,p.xCm), Math.max(0,width-p.wCm));
  p.yCm=Math.max(0,p.yCm);
}
function recalcGangCanvasHeight(){
  const maxBottom=gangPieces.reduce((m,p)=>Math.max(m,p.yCm+p.hCm),0);
  gangHeightCm=Math.max(sheetMinHeightCm(), maxBottom+0.6);
}
function rotateGangPiece(p){
  const cx=p.xCm+p.wCm/2, cy=p.yCm+p.hCm/2;
  [p.wCm,p.hCm]=[p.hCm,p.wCm];
  p.angle=((p.angle||0)+90)%360;
  p.xCm=cx-p.wCm/2; p.yCm=cy-p.hCm/2;
  clampGangPiece(p);
}
function updateGangPieceButtons(){
  const has=!!gangSelected;
  $('#gangRotatePiece').disabled=!has;
  $('#gangFlipHBtn').disabled=!has;
  $('#gangFlipVBtn').disabled=!has;
}
function calcGangTotal(){
  if(!CONFIG||!gangPieces.length)return 0; // chapa vazia não tem preço (antes já mostrava o mínimo de 2 m)
  const meters=(gangHeightCm||0)/100;
  const tiers=(CONFIG.pricing&&CONFIG.pricing.dtf_gang_sheet_tiers)||[];
  let pricePerM=tiers.length?tiers[tiers.length-1].price_per_m:39.9;
  for(const t of tiers){ if(meters<=t.up_to_m){ pricePerM=t.price_per_m; break; } }
  return Math.max(meters,0.1)*pricePerM;
}
function renderGangRulers(){
  const width=sheetWidthCm();
  $('#gangAreaLabel').textContent=`ÁREA DE IMPRESSÃO — ${width}CM × ${gangHeightCm.toFixed(0)}CM`;
  const top=$('#gangRulerTop'), left=$('#gangRulerLeft');
  top.style.width=(width*GANG_SCALE)+'px'; top.innerHTML='';
  for(let c=0;c<=width;c+=10){const m=document.createElement('div');m.className='ruler-mark';m.style.left=(c*GANG_SCALE)+'px';m.textContent=c;top.appendChild(m);}
  left.style.height=(gangHeightCm*GANG_SCALE)+'px'; left.innerHTML='';
  for(let c=0;c<=gangHeightCm;c+=10){const m=document.createElement('div');m.className='ruler-mark';m.style.top=(c*GANG_SCALE)+'px';m.textContent=c;left.appendChild(m);}
}
function pieceOverlaps(a,b){
  return a.xCm < b.xCm+b.wCm && a.xCm+a.wCm > b.xCm && a.yCm < b.yCm+b.hCm && a.yCm+a.hCm > b.yCm;
}
function renderGangCanvas(){
  const width=sheetWidthCm();
  gangCanvas.width=Math.round(width*GANG_SCALE);
  gangCanvas.height=Math.round(Math.max(gangHeightCm,10)*GANG_SCALE);
  renderGangRulers();
  gctx.clearRect(0,0,gangCanvas.width,gangCanvas.height);
  gangPieces.forEach((p,i)=>{
    const x=p.xCm*GANG_SCALE, y=p.yCm*GANG_SCALE, w=p.wCm*GANG_SCALE, h=p.hCm*GANG_SCALE;
    const angle=p.angle||0, odd=angle%180===90;
    gctx.save();
    gctx.translate(x+w/2,y+h/2);
    if(angle) gctx.rotate(angle*Math.PI/180);
    if(p.flipX) gctx.scale(-1,1);
    if(p.flipY) gctx.scale(1,-1);
    const dw=odd?h:w, dh=odd?w:h;
    gctx.drawImage(p.img,-dw/2,-dh/2,dw,dh);
    gctx.restore();
    const colliding=gangPieces.some((o,j)=>j!==i&&pieceOverlaps(p,o));
    const isSelected=gangSelectedSet.has(p);
    gctx.strokeStyle = colliding ? '#e23c3c' : isSelected ? '#9b59b6' : (odd?'#12a06b':'#6a6ad1');
    gctx.setLineDash(colliding||isSelected?[]:[5,3]);
    gctx.lineWidth = isSelected?3:2;
    gctx.strokeRect(x,y,w,h);
    drawDimBadge(gctx, p.wCm.toFixed(1)+'cm', x+w/2, y+7, false);
    drawDimBadge(gctx, p.hCm.toFixed(1)+'cm', x+7, y+h/2, true);
  });
  if(gangMarquee){
    const x1=gangMarquee.x1*GANG_SCALE, y1=gangMarquee.y1*GANG_SCALE, x2=gangMarquee.x2*GANG_SCALE, y2=gangMarquee.y2*GANG_SCALE;
    gctx.save();
    gctx.fillStyle='rgba(155,89,182,.15)';
    gctx.strokeStyle='#9b59b6';
    gctx.lineWidth=1.5;
    gctx.setLineDash([4,3]);
    gctx.fillRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1));
    gctx.strokeRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1));
    gctx.restore();
  }
  positionGangHandles();
}
function drawDimBadge(context, text, cx, cy, vertical){
  context.save();
  context.translate(cx,cy);
  if(vertical) context.rotate(-Math.PI/2);
  context.font='9px Arial';
  const tw=context.measureText(text).width;
  const padX=3, padY=1.5, bw=tw+padX*2, bh=9+padY*2;
  context.fillStyle='rgba(5,5,5,.72)';
  if(context.roundRect){ context.beginPath(); context.roundRect(-bw/2,-bh/2,bw,bh,3); context.fill(); }
  else context.fillRect(-bw/2,-bh/2,bw,bh);
  context.fillStyle='#fff';
  context.textAlign='center';
  context.textBaseline='middle';
  context.fillText(text,0,0.5);
  context.restore();
}
function positionGangHandles(){
  const wrap=$('#gangHandles');
  if(!gangSelected){ wrap.hidden=true; return; }
  wrap.hidden=false;
  const x=gangSelected.xCm*GANG_SCALE, y=gangSelected.yCm*GANG_SCALE, w=gangSelected.wCm*GANG_SCALE, h=gangSelected.hCm*GANG_SCALE;
  $('#gangHandleRotate').style.left=(x+4)+'px';
  $('#gangHandleRotate').style.top=(y+h-4)+'px';
  $('#gangHandleResize').style.left=(x+w-4)+'px';
  $('#gangHandleResize').style.top=(y+h-4)+'px';
}
function gangPos(ev){
  const r=gangCanvas.getBoundingClientRect();
  return {x:(ev.clientX-r.left)*gangCanvas.width/r.width/GANG_SCALE, y:(ev.clientY-r.top)*gangCanvas.height/r.height/GANG_SCALE};
}
let gangResize=null;
gangCanvas.onpointerdown=ev=>{
  const p=gangPos(ev);
  const hit=[...gangPieces].reverse().find(pc=>p.x>=pc.xCm&&p.x<=pc.xCm+pc.wCm&&p.y>=pc.yCm&&p.y<=pc.yCm+pc.hCm);

  if(hit && ev.shiftKey){
    if(gangAnchor){
      const ax1=gangAnchor.xCm, ay1=gangAnchor.yCm, ax2=ax1+gangAnchor.wCm, ay2=ay1+gangAnchor.hCm;
      const bx1=hit.xCm, by1=hit.yCm, bx2=bx1+hit.wCm, by2=by1+hit.hCm;
      setGangSelection(piecesInRect(Math.min(ax1,bx1),Math.min(ay1,by1),Math.max(ax2,bx2),Math.max(ay2,by2)));
    } else {
      gangAnchor=hit;
      setGangSelection([hit]);
    }
    renderGangCanvas();
    return;
  }

  if(hit && (ev.ctrlKey || ev.metaKey)){
    const next=new Set(gangSelectedSet);
    if(next.has(hit)) next.delete(hit); else next.add(hit);
    setGangSelection(next);
    gangAnchor=hit;
    renderGangCanvas();
    return;
  }

  if(hit){
    gangAnchor=hit;
    if(!gangSelectedSet.has(hit) || gangSelectedSet.size<=1) setGangSelection([hit]);
    const pieces=[...gangSelectedSet];
    gangDrag={
      pieces,
      startPointer:{x:p.x,y:p.y},
      startPositions:pieces.map(pc=>({piece:pc,xCm:pc.xCm,yCm:pc.yCm})),
      pointerId:ev.pointerId
    };
    gangCanvas.setPointerCapture(ev.pointerId);
    renderGangCanvas();
    return;
  }

  // clicou em espaço vazio: começa a desenhar um quadrante de seleção
  gangAnchor=null;
  gangMarquee={x1:p.x,y1:p.y,x2:p.x,y2:p.y,pointerId:ev.pointerId,moved:false};
  gangCanvas.setPointerCapture(ev.pointerId);
  renderGangCanvas();
};
gangCanvas.onpointermove=ev=>{
  const p=gangPos(ev);
  if(gangDrag){
    const dx=p.x-gangDrag.startPointer.x, dy=p.y-gangDrag.startPointer.y;
    gangDrag.startPositions.forEach(sp=>{ sp.piece.xCm=sp.xCm+dx; sp.piece.yCm=sp.yCm+dy; });
    gangDrag.pieces.forEach(clampGangPiece);
    renderGangCanvas();
  } else if(gangResize){
    const pc=gangResize.piece, width=sheetWidthCm();
    let newW=Math.max(1,p.x-pc.xCm), newH=Math.max(1,p.y-pc.yCm);
    newW=Math.min(newW, width-pc.xCm);
    pc.wCm=newW; pc.hCm=newH;
    renderGangCanvas();
  } else if(gangMarquee){
    gangMarquee.x2=p.x; gangMarquee.y2=p.y;
    if(Math.hypot(gangMarquee.x2-gangMarquee.x1, gangMarquee.y2-gangMarquee.y1)>1) gangMarquee.moved=true;
    renderGangCanvas();
  }
};
function endGangInteraction(){
  if(gangDrag){
    if(gangCanvas.hasPointerCapture(gangDrag.pointerId)) gangCanvas.releasePointerCapture(gangDrag.pointerId);
    recalcGangCanvasHeight();
    renderGangCanvas();
    updatePrice();
    gangDrag=null;
  }
  if(gangResize){
    if(gangCanvas.hasPointerCapture(gangResize.pointerId)) gangCanvas.releasePointerCapture(gangResize.pointerId);
    syncGangItemFromPiece(gangResize.piece);
    recalcGangCanvasHeight();
    renderGangCanvas();
    updatePrice();
    gangResize=null;
  }
  if(gangMarquee){
    if(gangCanvas.hasPointerCapture(gangMarquee.pointerId)) gangCanvas.releasePointerCapture(gangMarquee.pointerId);
    if(gangMarquee.moved){
      setGangSelection(piecesInRect(gangMarquee.x1,gangMarquee.y1,gangMarquee.x2,gangMarquee.y2));
      gangAnchor=gangSelected||[...gangSelectedSet][0]||null;
    } else {
      setGangSelection([]);
      gangAnchor=null;
    }
    gangMarquee=null;
    renderGangCanvas();
  }
}
gangCanvas.onpointerup=endGangInteraction;
gangCanvas.onpointercancel=endGangInteraction;
/* ---------- Teclado e leitor de tela (chapa de DTF) ---------- */
function describeGang(){
  if(!gangSelected) return gangPieces.length?`${gangPieces.length} ${gangPieces.length===1?'peça':'peças'} na chapa. Aperte N para escolher.`:'A chapa ainda está vazia.';
  const p=gangSelected, item=gangItems.find(it=>it.id===p.itemId), i=gangPieces.indexOf(p)+1;
  return `Peça ${i} de ${gangPieces.length}${item?`, ${item.name}`:''}: ${fmtCm(p.wCm)} por ${fmtCm(p.hCm)} cm, a ${fmtCm(p.xCm)} cm da esquerda e ${fmtCm(p.yCm)} cm do topo.`;
}
gangCanvas.onkeydown=ev=>{
  const k=ev.key, step=ev.shiftKey?5:0.5;
  if(k==='n'||k==='N'){
    if(gangPieces.length){ const i=gangPieces.indexOf(gangSelected), n=gangPieces.length; gangAnchor=gangPieces[ev.shiftKey?(i-1+n)%n:(i+1)%n]; setGangSelection([gangAnchor]); renderGangCanvas(); }
    announce(describeGang()); ev.preventDefault(); return;
  }
  const p=gangSelected;
  if(!p){ if(k.startsWith('Arrow')){ announce(describeGang()); ev.preventDefault(); } return; }
  const moves={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]};
  if(moves[k]){ p.xCm+=moves[k][0]; p.yCm+=moves[k][1]; clampGangPiece(p); }
  else if(k==='+'||k==='='||k==='-'||k==='_'){
    const f=(k==='-'||k==='_')?(ev.shiftKey?0.8:0.95):(ev.shiftKey?1.2:1.05);
    p.wCm=Math.max(1,Math.min(p.wCm*f,sheetWidthCm())); p.hCm=Math.max(1,p.hCm*f); clampGangPiece(p);
    syncGangItemFromPiece(p);
  }
  else if(k==='r'||k==='R') rotateGangPiece(p);
  else if(k==='h'||k==='H') p.flipX=!p.flipX;
  else if(k==='v'||k==='V') p.flipY=!p.flipY;
  else if(k==='Escape'){ setGangSelection([]); renderGangCanvas(); announce('Nenhuma peça selecionada.'); return; }
  else return;
  ev.preventDefault(); recalcGangCanvasHeight(); renderGangCanvas(); updatePrice(); announce(describeGang());
};
$('#gangHandleRotate').onclick=()=>{
  if(!gangSelected)return;
  rotateGangPiece(gangSelected);
  recalcGangCanvasHeight();
  renderGangCanvas();
  updatePrice();
};
$('#gangHandleResize').onkeydown=ev=>{
  const d={ArrowRight:[1,0],ArrowLeft:[-1,0],ArrowDown:[0,1],ArrowUp:[0,-1]}[ev.key];
  if(!d||!gangSelected) return;
  ev.preventDefault();
  const p=gangSelected, s=ev.shiftKey?5:0.5;
  p.wCm=Math.max(1,Math.min(p.wCm+d[0]*s,sheetWidthCm()-p.xCm)); p.hCm=Math.max(1,p.hCm+d[1]*s);
  syncGangItemFromPiece(p);
  recalcGangCanvasHeight(); renderGangCanvas(); updatePrice(); announce(describeGang());
};
$('#gangHandleResize').onpointerdown=ev=>{
  if(!gangSelected)return;
  ev.preventDefault();
  gangResize={piece:gangSelected,pointerId:ev.pointerId};
  gangCanvas.setPointerCapture(ev.pointerId);
};

async function exportGangSheet(){
  if(!gangPieces.length) throw new Error('Adicione ao menos uma imagem à chapa.');
  const width=sheetWidthCm(), dpi=CONFIG.dpi, gap=0.6;
  // O arquivo vai só de onde começa a arte até onde ela termina (com a mesma
  // folga de 0,6 cm das peças). A chapa da tela tem no mínimo 2 m, e antes o
  // arquivo saía com essa altura toda mesmo com 50 cm de arte.
  const top=Math.max(0,Math.min(...gangPieces.map(p=>p.yCm))-gap);
  const heightCm=Math.max(...gangPieces.map(p=>p.yCm+p.hCm))+gap-top;
  const ow=Math.round(width/2.54*dpi), oh=Math.round(heightCm/2.54*dpi);
  const out=document.createElement('canvas'); out.width=ow; out.height=oh;
  const ox=out.getContext('2d'), sx=ow/width, sy=oh/heightCm;
  gangPieces.forEach(p=>{
    const x=p.xCm*sx, y=(p.yCm-top)*sy, w=p.wCm*sx, h=p.hCm*sy;
    const angle=p.angle||0, odd=angle%180===90;
    const dw=odd?h:w, dh=odd?w:h;
    ox.save(); ox.translate(x+w/2,y+h/2);
    if(angle) ox.rotate(angle*Math.PI/180);
    if(p.flipX) ox.scale(-1,1);
    if(p.flipY) ox.scale(1,-1);
    ox.drawImage(p.img,-dw/2,-dh/2,dw,dh);
    ox.restore();
  });
  const blob=await new Promise(r=>out.toBlob(r,'image/png'));
  // O navegador não gera canvas com mais de ~32 mil px de lado (≈ 2,7 m a 300 dpi).
  if(!blob) throw new Error('A chapa ficou comprida demais pra gerar no navegador. Divida em dois pedidos.');
  return {blob, widthCm:width, heightCm:Number(heightCm.toFixed(2)), widthPx:ow, heightPx:oh, dpi};
}
/* Cada arte da chapa vai também separada, no arquivo original (PNG, PDF,
   TIFF...). Arte da biblioteca ou da IA não tem arquivo: vai a imagem em PNG. */
async function gangOriginalFiles(){
  const out=[];
  for(const [i,it] of gangItems.entries()){
    let blob=it.file, name=it.file&&it.file.name;
    if(!blob){
      const c=document.createElement('canvas'); c.width=it.img.naturalWidth||it.img.width; c.height=it.img.naturalHeight||it.img.height;
      c.getContext('2d').drawImage(it.img,0,0);
      blob=await new Promise(r=>c.toBlob(r,'image/png'));
      name=(it.name||'arte').replace(/\.[a-z0-9]+$/i,'')+'.png';
      it.kind='png';
    }
    if(blob) out.push([`source_${i+1}`,blob,name]);
  }
  return out;
}
async function createGangOrder(){
  if(!currentCustomer){ openAuthModal(); return stat('#checkoutStatusGang','Entre ou crie uma conta antes de finalizar o pedido.',false); }
  if(!gangItems.length) return stat('#checkoutStatusGang','Adicione ao menos uma imagem.',false);
  const btn=$('#checkoutBtnGang');btn.disabled=true; // evita pedido duplicado no clique duplo
  try{
    stat('#checkoutStatusGang','Gerando chapa...',true);
    const p=await exportGangSheet();
    const totalQty=gangItems.reduce((a,it)=>a+it.qty,0);
    const data={
      sale_type:'DTF_ONLY',
      customer:customerPayload(),
      product:null,
      dtf_quantity:totalQty,
      design:{width_cm:p.widthCm,height_cm:p.heightCm,dpi:p.dpi,width_px:p.widthPx,height_px:p.heightPx},
      sources:gangItems.map(it=>({type:'image',origin:it.premiumId?'PREMIUM':'UPLOAD',premium_id:it.premiumId||null,name:it.name,width_cm:it.wCm,height_cm:it.hCm,qty:it.qty,format:it.kind||'png'})),
      total:Math.round(calcGangTotal()*100)/100,
      tracking:trackingContext()
    };
    stat('#checkoutStatusGang','Separando as artes...',true);
    const originals=await gangOriginalFiles();
    const {r,d}=await sendOrder(data,[['design',p.blob,'chapa_producao.png'],...originals],'#checkoutStatusGang');
    if(r.status===401){ logoutCustomer(); openAuthModal(); }
    if(!r.ok) throw new Error(orderError(r,d));
    if(adminKey) loadOrders();
    await afterOrderCreated(d,'#checkoutStatusGang');
  }catch(e){ stat('#checkoutStatusGang',e.message,false); }
  finally{ btn.disabled=false; }
}

/* ---------- Estados para o leitor de tela ---------- */
// O site marca a aba/botão ativo com a classe "active"/"on". Aqui isso vira
// aria-pressed / aria-current, em qualquer lugar e mesmo nos botões que o JS cria depois.
const PRESSABLE='.side-tab,.model-tab,#profileTabs button,.auth-tab,.lib-side';
function syncAriaStates(){
  $$(PRESSABLE).forEach(b=>{const v=String(b.classList.contains('active')||b.classList.contains('on')); if(b.getAttribute('aria-pressed')!==v) b.setAttribute('aria-pressed',v);});
  $$('.nav-btn,#navAccount').forEach(b=>{const on=b.classList.contains('active')||b.classList.contains('current'); if(on!==b.hasAttribute('aria-current')) on?b.setAttribute('aria-current','page'):b.removeAttribute('aria-current');});
}
let ariaQueued=false;
new MutationObserver(()=>{ if(ariaQueued) return; ariaQueued=true; requestAnimationFrame(()=>{ariaQueued=false;syncAriaStates()}); })
  .observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});
// Ao fechar um modal, o foco volta pro botão que abriu (antes caía no começo da página).
$$('dialog').forEach(d=>{
  const open=d.showModal.bind(d);
  d.showModal=()=>{ d._opener=document.activeElement; open(); };
  d.addEventListener('close',()=>{ if(d._opener&&document.contains(d._opener)) d._opener.focus(); });
});

init().catch(e=>{console.error(e);alert('Erro ao iniciar: '+e.message)});
})();
