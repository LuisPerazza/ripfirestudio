(() => {
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let CONFIG, artworks=[], selected=null, dragging=null, digitalArt=null, adminKey=null, adminUser=null, shirtMockups=[];
let shirtResize=null; // {piece,pointerId} - arraste do quadradinho de esticar (mesmo modelo da chapa de DTF)
let currentModel='regular', currentSide='FRONT';
let sidesElements={FRONT:[],BACK:[]};
let elements=sidesElements.FRONT; // sempre aponta pro array do lado ativo (frente OU verso)
const canvas=$("#designCanvas"), ctx=canvas.getContext("2d");
const money=v=>Number(v||0).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]));
function tab(id){$$('.tab').forEach(x=>x.classList.toggle('active',x.id===id));$$('.nav-btn').forEach(x=>x.classList.toggle('active',x.dataset.tab===id));document.body.classList.toggle('is-home',id==='home');document.body.classList.toggle('is-editor',id==='editor');document.body.classList.toggle('is-digital',id==='digital');if(id==='admin')enterAdmin();}

/* ============ Conta do cliente (login/cadastro) ============ */
let currentCustomer=null; // {id,email,full_name,phone,cep,address_number,document}
function loadCustomerFromStorage(){
  try{ const raw=localStorage.getItem('ripfire_customer'); if(raw) currentCustomer=JSON.parse(raw); }catch{ currentCustomer=null; }
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
  [['accountInfoShirt','openAuthShirt','logoutBtnShirt'],['accountInfoGang','openAuthGang','logoutBtnGang'],['accountInfoDigital','openAuthDigital',null]].forEach(([infoId,openId,logoutId])=>{
    $('#'+infoId).textContent = logged ? `Logado como ${currentCustomer.full_name} (${currentCustomer.email})` : 'Você ainda não entrou com uma conta.';
    $('#'+openId).hidden = logged;
    if(logoutId) $('#'+logoutId).hidden = !logged;
  });
}
let googleButtonRendered=false, googleInitAttempts=0;
function ensureGoogleButton(){
  const clientId=CONFIG&&CONFIG.google_client_id;
  if(!clientId){ $('#googleBtnFallback').hidden=false; return; }
  if(googleButtonRendered) return;
  if(!window.google||!google.accounts||!google.accounts.id){
    if(googleInitAttempts++<20) setTimeout(ensureGoogleButton,250);
    return;
  }
  google.accounts.id.initialize({client_id:clientId, callback:handleGoogleCredential});
  google.accounts.id.renderButton($('#googleBtnContainer'),{theme:'outline',size:'large',width:380,text:'continue_with'});
  googleButtonRendered=true;
}
async function handleGoogleCredential(response){
  try{
    const r=await fetch('/api/auth/google',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({credential:response.credential})});
    const d=await r.json();
    if(!r.ok) throw new Error(d.detail||'Erro ao entrar com Google.');
    currentCustomer=d; saveCustomerToStorage(); renderAccountBoxes();
    stat('#authStatus','Login com Google realizado!',true);
    setTimeout(()=>$('#authModal').close(),500);
  }catch(e){ stat('#authStatus',e.message,false); }
}
function openAuthModal(){ switchAuthTab('login'); ensureGoogleButton(); $('#authModal').showModal(); }
function switchAuthTab(tabName){
  $$('.auth-tab').forEach(b=>b.classList.toggle('active', b.dataset.authtab===tabName));
  $('#authLoginForm').hidden = tabName!=='login';
  $('#authRegisterForm').hidden = tabName!=='register';
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
}
async function submitLogin(){
  const email=$('#loginEmail').value.trim(), password=$('#loginPassword').value;
  if(!email||!password) return stat('#authStatus','Preencha e-mail e senha.',false);
  try{
    const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})});
    const d=await r.json();
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
    const d=await r.json();
    if(!r.ok) throw new Error(d.detail||'Erro ao cadastrar.');
    currentCustomer=d; saveCustomerToStorage(); renderAccountBoxes();
    stat('#authStatus','Conta criada!',true);
    setTimeout(()=>$('#authModal').close(),500);
  }catch(e){ stat('#authStatus',e.message,false); }
}
function logoutCustomer(){ currentCustomer=null; saveCustomerToStorage(); renderAccountBoxes(); }

async function init(){CONFIG=await fetch('/api/config').then(r=>r.json());gangHeightCm=sheetMinHeightCm();loadCustomerFromStorage();fillConfig();bind();renderAccountBoxes();await loadArtworks();await loadShirtMockups();toggleSale();updateShirtVisual();updatePrice();render();renderGangList();renderMockupsList();window.addEventListener('resize',()=>{positionPrintAreaOnPhoto();positionPrintAreaOnSvg()});}
function fillConfig(){
  currentModel=CONFIG.shirt_models[0]?.id||'regular';
  $('#shirtModelTabs').innerHTML=CONFIG.shirt_models.map(x=>`<button type="button" class="model-tab${x.id===currentModel?' active':''}" data-model="${x.id}">${x.name}</button>`).join('');
  $$('#shirtModelTabs [data-model]').forEach(b=>b.onclick=()=>{
    currentModel=b.dataset.model;
    $$('#shirtModelTabs [data-model]').forEach(x=>x.classList.toggle('active',x===b));
    updateShirtVisual();updatePrice();
  });
  $('#shirtColor').innerHTML=CONFIG.shirt_colors.map(x=>`<option>${x}</option>`).join('');
  $('#mockupModel').innerHTML=CONFIG.shirt_models.map(x=>`<option value="${x.id}">${x.name}</option>`).join('');
  $('#mockupColor').innerHTML=CONFIG.shirt_colors.map(x=>`<option>${x}</option>`).join('');
  $('#sizeGrid').innerHTML=CONFIG.sizes.map(s=>`<div><b>${s}</b><input data-size="${s}" type="number" min="0" value="0"></div>`).join('');
  $('#aiStatus').textContent=CONFIG.ai_enabled?`IA ativa: ${CONFIG.ai_model}`:'IA desativada até configurar .env';
  $('#gangAiStatus').textContent=CONFIG.ai_enabled?`IA ativa: ${CONFIG.ai_model}`:'IA desativada até configurar .env';
}
function bind(){
  $$('.nav-btn').forEach(b=>b.onclick=()=>tab(b.dataset.tab));$('#startCustomize').onclick=()=>tab('editor');
  $('#openAuthShirt').onclick=openAuthModal;$('#openAuthGang').onclick=openAuthModal;$('#openAuthDigital').onclick=openAuthModal;
  $('#logoutBtnShirt').onclick=logoutCustomer;$('#logoutBtnGang').onclick=logoutCustomer;
  $$('.auth-tab').forEach(b=>b.onclick=()=>switchAuthTab(b.dataset.authtab));
  $('#loginSubmitBtn').onclick=submitLogin;$('#registerSubmitBtn').onclick=submitRegister;
  bindMaskedInput($('#regPhone'),formatPhoneBR);bindMaskedInput($('#regDocument'),formatCpfCnpj);
  $('#adminLoginBtn').onclick=submitAdminLogin;
  $('#saleType').onchange=()=>{toggleSale();updatePrice()};$('#shirtColor').onchange=updateShirtVisual;$('#dtfQty').oninput=updatePrice;$('#sizeGrid').oninput=()=>{syncQty();updatePrice()};
  $$('#sideTabs [data-side]').forEach(b=>b.onclick=()=>{
    currentSide=b.dataset.side;
    elements=sidesElements[currentSide];
    selected=null;
    $$('#sideTabs [data-side]').forEach(x=>x.classList.toggle('active',x===b));
    updateShirtVisual();render();
  });
  $('#uploadArt').onchange=e=>{const f=e.target.files[0];if(f)addImage(URL.createObjectURL(f),'UPLOAD')};
  $('#generateAI').onclick=generateAI;$('#deleteSelected').onclick=()=>{if(!selected)return;elements=elements.filter(e=>e!==selected);sidesElements[currentSide]=elements;selected=null;render()};
  $('#centerSelected').onclick=()=>{if(selected){selected.x=210;selected.y=240;render()}};$('#bringFront').onclick=()=>{if(selected){elements=elements.filter(e=>e!==selected);elements.push(selected);sidesElements[currentSide]=elements;render()}};
  $('#shirtHandleRotate').onclick=()=>{if(!selected)return;selected.rotation=((selected.rotation||0)+90)%360;render()};
  $('#shirtHandleResize').onpointerdown=ev=>{if(!selected)return;ev.preventDefault();ev.stopPropagation();shirtResize={piece:selected,pointerId:ev.pointerId};canvas.setPointerCapture(ev.pointerId)};
  bindPrintAreaDrag();
  canvas.onpointerdown=pointerDown;canvas.onpointermove=pointerMove;window.onpointerup=()=>{dragging=null;if(shirtResize){canvas.hasPointerCapture(shirtResize.pointerId)&&canvas.releasePointerCapture(shirtResize.pointerId);shirtResize=null}};
  $('#checkoutBtn').onclick=createOrder;$('#artworkForm').onsubmit=uploadArtwork;$('#refreshOrders').onclick=enterAdmin;$('#confirmDigital').onclick=createDigitalOrder;
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
  else positionPrintAreaOnPhoto();
}
function chosenModel(){return CONFIG.shirt_models.find(x=>x.id===currentModel)||CONFIG.shirt_models[0]}
function shirtQty(){return $$('#sizeGrid input').reduce((a,i)=>a+(Number(i.value)||0),0)}
function syncQty(){if($('#saleType').value!=='DTF_ONLY'){const q=shirtQty();if(q>0)$('#dtfQty').value=q}}
function updatePrice(){
  if(!CONFIG)return;
  if($('#saleType').value==='DTF_ONLY'){$('#priceTotal').textContent=money(calcGangTotal());return}
  const q=Math.max(1,Number($('#dtfQty').value)||1),t=$('#saleType').value,p=CONFIG.pricing,m=chosenModel();let total=q*p.dtf_only_unit;if(t==='DTF_PLUS_SHIRT')total=q*(p.dtf_only_unit+m.base_price);if(t==='FINISHED_SHIRT')total=q*(p.dtf_only_unit+m.base_price+p.finished_press_unit);$('#priceTotal').textContent=money(total)
}
function calcTotal(){const q=Math.max(1,Number($('#dtfQty').value)||1),t=$('#saleType').value,p=CONFIG.pricing,m=chosenModel();if(t==='DTF_ONLY')return q*p.dtf_only_unit;if(t==='DTF_PLUS_SHIRT')return q*(p.dtf_only_unit+m.base_price);return q*(p.dtf_only_unit+m.base_price+p.finished_press_unit)}

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
  const modelId=currentModel||'regular';
  const colorName=$('#shirtColor').value||'Preto';
  const base=SHIRT_COLOR_HEX[colorName]||'#161616';
  const light=shadeColor(base,32), dark=shadeColor(base,-26), deep=shadeColor(base,-42), seam=shadeColor(base,-48);
  const p=SHIRT_PARAMS[modelId]||SHIRT_PARAMS.regular;
  const path=roundedPolygonPath(shirtPoints(p),p.radius);
  const gid='shirtGrad_'+modelId;
  $('#shirtSvg').innerHTML=`
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
  try{ shirtMockups=await fetch('/api/shirt-mockups').then(r=>r.json()); }
  catch{ shirtMockups=[]; }
}
function findMockup(modelId,color,side){
  return shirtMockups.find(m=>m.model_id===modelId && m.color===color && m.side===side) || null;
}
function updateShirtVisual(){
  const modelId=currentModel||'regular';
  const colorName=$('#shirtColor').value||'Preto';
  const mock=findMockup(modelId,colorName,currentSide);
  const photo=$('#shirtPhoto'), svg=$('#shirtSvg');
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
function positionPrintAreaOnSvg(){
  const svg=$('#shirtSvg');
  if(svg.style.display==='none')return;
  const p=SHIRT_PARAMS[currentModel]||SHIRT_PARAMS.regular;
  const bottomMargin=currentModel==='babylook'?35:50;
  const halfW=p.underarmHalfW*0.85, topY=p.underarmY+25, botY=p.hemY-bottomMargin;
  const px=(CX-halfW)/430, py=topY/520, pw=(halfW*2)/430, ph=(botY-topY)/520;
  const stage=svg.closest('.shirt-stage');
  const sRect=svg.getBoundingClientRect(), stRect=stage.getBoundingClientRect();
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
let printAreaDrag=null;
function bindPrintAreaDrag(){
  const move=$('#printAreaMove'), resize=$('#printAreaResize'), area=$('#printArea');
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
  const left=pRect.left-sRect.left, top=pRect.top-sRect.top;
  const areaLeft=parseFloat(area.style.left), areaTop=parseFloat(area.style.top);
  const areaW=parseFloat(area.style.width), areaH=parseFloat(area.style.height);
  const px=(areaLeft-left)/pRect.width*100, py=(areaTop-top)/pRect.height*100;
  const pw=areaW/pRect.width*100, ph=areaH/pRect.height*100;
  photo.dataset.printX=px; photo.dataset.printY=py;
  photo.dataset.printW=pw; photo.dataset.printH=ph;
  const id=photo.dataset.mockupId;
  const mockRecord=shirtMockups.find(m=>m.id===id);
  if(mockRecord){mockRecord.print_x=px;mockRecord.print_y=py;mockRecord.print_w=pw;mockRecord.print_h=ph;}
  const body=JSON.stringify({print_x:px,print_y:py,print_w:pw,print_h:ph});
  try{
    let r=await fetch(`/api/admin/shirt-mockups/${id}/area`,{method:'PATCH',headers:{'Content-Type':'application/json',...adminHeaders()},body});
    if(r.status===401){
      const u=prompt('Usuário de admin pra salvar a área de impressão:');
      if(!u) return;
      const k=prompt('Senha/chave de admin:');
      if(!k) return;
      adminUser=u; adminKey=k;
      r=await fetch(`/api/admin/shirt-mockups/${id}/area`,{method:'PATCH',headers:{'Content-Type':'application/json',...adminHeaders()},body});
    }
    if(!r.ok){adminKey=null;adminUser=null;}
  }catch(e){}
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
  const fd=new FormData(ev.currentTarget);
  const r=await fetch('/api/admin/shirt-mockups',{method:'POST',headers:adminHeaders(),body:fd});
  const d=await r.json();
  if(r.status===401){adminKey=null;adminUser=null;return stat('#adminMockupStatus','Chave de admin inválida.',false)}
  if(!r.ok) return stat('#adminMockupStatus',d.detail||'Erro',false);
  stat('#adminMockupStatus','Mockup salvo.',true);
  ev.currentTarget.reset();
  $('#mockupPreviewWrap').style.display='none';
  await loadShirtMockups();
  renderMockupsList();
  updateShirtVisual();
}
function renderMockupsList(){
  $('#mockupsList').innerHTML=shirtMockups.map(m=>`
    <div class="gang-item">
      <img src="${m.preview_url}" alt="">
      <div class="gi-info"><b>${esc(m.model_id)} · ${esc(m.color)} · ${m.side==='BACK'?'Verso':'Frente'}</b><small>área ${m.print_w}×${m.print_h}%</small></div>
      <button data-delmock="${m.id}" title="Remover">🗑</button>
    </div>
  `).join('') || '<p style="font-size:12px;color:var(--muted)">Nenhum mockup cadastrado — o editor usa o desenho padrão.</p>';
  $$('[data-delmock]').forEach(b=>b.onclick=async()=>{
    const r=await fetch(`/api/admin/shirt-mockups/${b.dataset.delmock}`,{method:'DELETE',headers:adminHeaders()});
    if(r.status===401){adminKey=null;adminUser=null;return}
    await loadShirtMockups(); renderMockupsList(); updateShirtVisual();
  });
}
async function loadArtworks(){artworks=await fetch('/api/artworks').then(r=>r.json());renderCatalogs()}
function renderCatalogs(){
  const custom=artworks.filter(a=>a.can_customize);
  const miniHtml=custom.slice(0,9).map(a=>`<button data-use="${a.id}"><img src="${a.preview_url}" alt="${esc(a.name)}"></button>`).join('')||'<small>Cadastre artes no Admin.</small>';
  $('#catalogMini').innerHTML=miniHtml;
  $('#gangCatalogMini').innerHTML=miniHtml;
  $$('#catalogMini [data-use]').forEach(b=>b.onclick=()=>useCatalog(b.dataset.use));
  $$('#gangCatalogMini [data-use]').forEach(b=>b.onclick=()=>useGangCatalog(b.dataset.use));
  const dig=artworks.filter(a=>a.can_sell_digital);$('#digitalCatalog').innerHTML=dig.map(a=>`<article class="art-card"><div class="art-img"><img src="${a.preview_url}" alt="${esc(a.name)}"></div><div class="art-body"><h3>${esc(a.name)}</h3><div class="art-meta">${esc(a.category)} · ${money(a.digital_price)}</div><div class="art-actions"><button data-custom="${a.id}">Personalizar</button><button data-buy="${a.id}">Comprar arquivo</button></div></div></article>`).join('')||'<p>Nenhuma arte cadastrada.</p>';$$('[data-custom]').forEach(b=>b.onclick=async()=>{tab('editor');await useCatalog(b.dataset.custom)});$$('[data-buy]').forEach(b=>b.onclick=()=>openDigital(b.dataset.buy));
}
async function useCatalog(id){const a=artworks.find(x=>x.id===id);if(a)await addImage(a.preview_url,'CATALOGO',a.id)}
function addImage(url,origin,artworkId=null){return new Promise((res,rej)=>{const img=new Image();img.crossOrigin='anonymous';img.onload=()=>{const base=Math.min(120/img.width,230/img.height,1);selected={id:crypto.randomUUID(),type:'image',img,url,origin,artworkId,x:210,y:240,scaleX:base,scaleY:base,rotation:0,w:img.width,h:img.height};elements.push(selected);render();res(selected)};img.onerror=rej;img.src=url})}
function draw(e){ctx.save();ctx.translate(e.x,e.y);ctx.rotate(e.rotation*Math.PI/180);if(e.type==='image'){ctx.scale(e.scaleX,e.scaleY);ctx.drawImage(e.img,-e.w/2,-e.h/2,e.w,e.h)}else{ctx.scale(e.scale,e.scale);ctx.fillStyle=e.color;ctx.font=`900 ${e.fontSize}px Arial`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(e.text,0,0)}ctx.restore()}
function size(e){if(e.type==='image')return{w:e.w*e.scaleX,h:e.h*e.scaleY};ctx.font=`900 ${e.fontSize}px Arial`;return{w:ctx.measureText(e.text).width*e.scale,h:e.fontSize*1.2*e.scale}}
function toLocal(e,x,y){const dx=x-e.x,dy=y-e.y,a=-e.rotation*Math.PI/180;return{x:dx*Math.cos(a)-dy*Math.sin(a),y:dx*Math.sin(a)+dy*Math.cos(a)}}
function cmPerUnitX(){return (CONFIG.max_print_width_cm||60)/420}
function cmPerUnitY(){return (CONFIG.max_print_height_cm||72)/480}
function render(){
  ctx.clearRect(0,0,420,480);
  elements.forEach(draw);
  if(selected){
    const s=size(selected);
    ctx.save();ctx.translate(selected.x,selected.y);ctx.rotate(selected.rotation*Math.PI/180);ctx.strokeStyle='#ff4b18';ctx.setLineDash([6,4]);ctx.lineWidth=2;ctx.strokeRect(-s.w/2,-s.h/2,s.w,s.h);ctx.restore();
    const b=bounds(selected);
    drawDimBadge(ctx,(s.w*cmPerUnitX()).toFixed(1)+'cm',(b.minX+b.maxX)/2,b.minY-9,false);
    drawDimBadge(ctx,(s.h*cmPerUnitY()).toFixed(1)+'cm',b.minX-9,(b.minY+b.maxY)/2,true);
  }
  positionShirtHandles();
}
function hit(e,x,y){const l=toLocal(e,x,y),s=size(e);return Math.abs(l.x)<=s.w/2&&Math.abs(l.y)<=s.h/2}
function pos(ev){const r=canvas.getBoundingClientRect();return{x:(ev.clientX-r.left)*420/r.width,y:(ev.clientY-r.top)*480/r.height}}
function pointerDown(ev){const p=pos(ev),h=[...elements].reverse().find(e=>hit(e,p.x,p.y));selected=h||null;if(h)dragging={e:h,dx:p.x-h.x,dy:p.y-h.y};render()}
function clampDragToCanvas(e){
  const b=bounds(e);
  if(b.minX<0) e.x+=(-b.minX);
  if(b.maxX>420) e.x-=(b.maxX-420);
  if(b.minY<0) e.y+=(-b.minY);
  if(b.maxY>480) e.y-=(b.maxY-480);
}
function clampResizeToCanvas(e){
  const b=bounds(e);
  const leftExtent=e.x-b.minX, rightExtent=b.maxX-e.x, topExtent=e.y-b.minY, bottomExtent=b.maxY-e.y;
  let ratio=1;
  if(leftExtent>0) ratio=Math.min(ratio, e.x/leftExtent);
  if(rightExtent>0) ratio=Math.min(ratio, (420-e.x)/rightExtent);
  if(topExtent>0) ratio=Math.min(ratio, e.y/topExtent);
  if(bottomExtent>0) ratio=Math.min(ratio, (480-e.y)/bottomExtent);
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
async function generateAI(){const prompt=$('#aiPrompt').value.trim();if(!prompt)return;$('#aiStatus').textContent='Gerando...';try{const r=await fetch('/api/ai/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Erro');await addImage(d.url,'IA');$('#aiStatus').textContent=`Gerada com ${d.model}`}catch(e){$('#aiStatus').textContent=e.message}}
function bounds(e){const s=size(e),a=e.rotation*Math.PI/180,hw=s.w/2,hh=s.h/2,pts=[[-hw,-hh],[hw,-hh],[hw,hh],[-hw,hh]].map(([x,y])=>({x:e.x+x*Math.cos(a)-y*Math.sin(a),y:e.y+x*Math.sin(a)+y*Math.cos(a)}));return{minX:Math.min(...pts.map(p=>p.x)),maxX:Math.max(...pts.map(p=>p.x)),minY:Math.min(...pts.map(p=>p.y)),maxY:Math.max(...pts.map(p=>p.y))}}
async function exportProductionFor(list){
  if(!list.length) return null;
  const bs=list.map(bounds);
  let minX=Math.max(0,Math.min(...bs.map(b=>b.minX))-4),maxX=Math.min(420,Math.max(...bs.map(b=>b.maxX))+4),
      minY=Math.max(0,Math.min(...bs.map(b=>b.minY))-4),maxY=Math.min(480,Math.max(...bs.map(b=>b.maxY))+4);
  const pw=maxX-minX,ph=maxY-minY,
      wcm=Math.min(CONFIG.max_print_width_cm,Math.max(2,Number($('#designWidthCm').value)||28)),
      hcm=wcm*(ph/pw),dpi=CONFIG.dpi,ow=Math.round(wcm/2.54*dpi),oh=Math.round(hcm/2.54*dpi),
      out=document.createElement('canvas');
  out.width=ow;out.height=oh;
  const ox=out.getContext('2d'),sx=ow/pw,sy=oh/ph;
  list.forEach(e=>{
    ox.save();ox.translate((e.x-minX)*sx,(e.y-minY)*sy);ox.rotate(e.rotation*Math.PI/180);
    if(e.type==='image'){ox.scale(e.scaleX*sx,e.scaleY*sy);ox.drawImage(e.img,-e.w/2,-e.h/2,e.w,e.h)}
    else{ox.scale(e.scale*sx,e.scale*sy);ox.fillStyle=e.color;ox.font=`900 ${e.fontSize}px Arial`;ox.textAlign='center';ox.textBaseline='middle';ox.fillText(e.text,0,0)}
    ox.restore();
  });
  const blob=await new Promise(r=>out.toBlob(r,'image/png'));
  return {blob,widthCm:wcm,heightCm:Number(hcm.toFixed(2)),widthPx:ow,heightPx:oh,dpi};
}
function sizeMap(){const m={};$$('#sizeGrid input').forEach(i=>m[i.dataset.size]=Number(i.value)||0);return m}
async function createOrder(){
  if(!currentCustomer){ openAuthModal(); return stat('#checkoutStatus','Entre ou crie uma conta antes de finalizar o pedido.',false); }
  if(!sidesElements.FRONT.length && !sidesElements.BACK.length)
    return stat('#checkoutStatus','Adicione uma arte na frente ou no verso.',false);
  try{
    stat('#checkoutStatus','Gerando arte...',true);
    const front=await exportProductionFor(sidesElements.FRONT);
    const back=await exportProductionFor(sidesElements.BACK);
    const type=$('#saleType').value;
    const main=front||back;
    const data={
      sale_type:type,
      customer:customerPayload(),
      product:type==='DTF_ONLY'?null:{shirt_model:chosenModel().name,color:$('#shirtColor').value,sizes:sizeMap()},
      dtf_quantity:Math.max(1,Number($('#dtfQty').value)||1),
      design:{width_cm:main.widthCm,height_cm:main.heightCm,dpi:main.dpi,width_px:main.widthPx,height_px:main.heightPx},
      has_front:!!front, has_back:!!back,
      sources_front:sidesElements.FRONT.map(e=>({type:e.type,origin:e.origin||'TEXT',artwork_id:e.artworkId||null,text:e.type==='text'?e.text:null})),
      sources_back:sidesElements.BACK.map(e=>({type:e.type,origin:e.origin||'TEXT',artwork_id:e.artworkId||null,text:e.type==='text'?e.text:null})),
      total:calcTotal()
    };
    const fd=new FormData();
    fd.append('order_json',JSON.stringify(data));
    if(front) fd.append('design',front.blob,'arte_producao_frente.png');
    if(back) fd.append('design_back',back.blob,'arte_producao_costas.png');
    const r=await fetch('/api/orders',{method:'POST',body:fd}),d=await r.json();
    if(!r.ok)throw new Error(d.detail||'Erro');
    stat('#checkoutStatus',`Pedido ${d.order_number} criado. Pasta: ${d.folder}`,true);
    await loadOrders();
  }catch(e){ stat('#checkoutStatus',e.message,false); }
}
function adminHeaders(extra){return {...(extra||{}), 'X-Admin-Key': adminKey||'', 'X-Admin-User': adminUser||''}}
function openAdminLogin(){
  $('#adminUser').value=''; $('#adminPassword').value='';
  $('#adminLoginStatus').textContent=''; $('#adminLoginStatus').className='';
  $('#adminLoginModal').showModal();
}
async function submitAdminLogin(){
  const user=$('#adminUser').value.trim(), pass=$('#adminPassword').value;
  if(!user || !pass) return stat('#adminLoginStatus','Usuário ou senha inválidos.',false);
  adminUser=user; adminKey=pass;
  const r=await fetch('/api/orders',{headers:adminHeaders()});
  if(r.status===401){ adminKey=null; adminUser=null; return stat('#adminLoginStatus','Usuário ou senha inválidos.',false); }
  stat('#adminLoginStatus','Login realizado!',true);
  setTimeout(()=>$('#adminLoginModal').close(),400);
  await loadOrders();
}
async function enterAdmin(){
  if(!adminKey){ openAdminLogin(); return; }
  await loadOrders();
}
async function uploadArtwork(ev){ev.preventDefault();if(!adminKey){$('#adminArtStatus').textContent='Entre no Admin com a chave primeiro.';return}const fd=new FormData(ev.currentTarget);fd.set('can_customize',ev.currentTarget.can_customize.checked?'true':'false');fd.set('can_sell_digital',ev.currentTarget.can_sell_digital.checked?'true':'false');const r=await fetch('/api/admin/artworks',{method:'POST',headers:adminHeaders(),body:fd}),d=await r.json();if(r.status===401){adminKey=null;adminUser=null;return stat('#adminArtStatus','Chave de admin inválida.',false)}if(!r.ok)return stat('#adminArtStatus',d.detail||'Erro',false);stat('#adminArtStatus','Arte cadastrada.',true);ev.currentTarget.reset();await loadArtworks()}
async function loadOrders(){
    const r=await fetch("/api/orders",{headers:adminHeaders()});
    if(r.status===401){adminKey=null;adminUser=null;$("#ordersTable").innerHTML="<p>Chave de admin inválida.</p>";return}
    const orders=await r.json();
    $("#ordersTable").innerHTML=orders.length?`
      <table class="orders-table">
        <thead><tr>
          <th>Pedido</th><th>Cliente</th><th>Tipo</th><th>Total</th>
          <th>Pagamento</th><th>Status</th><th>Pasta produção</th><th>Ação</th>
        </tr></thead>
        <tbody>${orders.map(o=>`<tr>
          <td><b>${o.order_number}</b></td>
          <td>${esc(o.customer_name)}</td>
          <td>${o.sale_type}</td>
          <td>${money(o.total)}</td>
          <td><span class="badge">${o.payment_status}</span></td>
          <td><span class="badge">${o.status}</span></td>
          <td>${esc(o.production_drop_path||"-")}</td>
          <td>${o.payment_status==="PAID"
            ? "Liberado"
            : `<button data-pay="${o.id}">SIMULAR PAGAMENTO</button>`}</td>
        </tr>`).join("")}</tbody>
      </table>`:"<p>Nenhum pedido.</p>";

    $$("[data-pay]").forEach(b=>b.addEventListener("click",async()=>{
      const r=await fetch(`/api/orders/${b.dataset.pay}/mark-paid`,{method:"POST",headers:adminHeaders()});
      const d=await r.json();
      if(r.status===401){adminKey=null;adminUser=null;alert("Chave de admin inválida.");return;}
      if(!r.ok){alert(d.detail||"Erro ao liberar pedido");return;}
      loadOrders();
    }));
  }

  function openDigital(id){digitalArt=artworks.find(a=>a.id===id);if(!digitalArt)return;$('#digitalSelected').innerHTML=`<p><b>${esc(digitalArt.name)}</b><br>${money(digitalArt.digital_price)}</p>`;$('#digitalStatus').textContent='';$('#digitalCheckout').showModal()}
async function createDigitalOrder(){if(!currentCustomer){openAuthModal();return stat('#digitalStatus','Entre ou crie uma conta antes de finalizar o pedido.',false)}const data={sale_type:'DIGITAL_ART',customer:customerPayload(),digital_artwork_id:digitalArt.id,total:Number(digitalArt.digital_price)},fd=new FormData();fd.append('order_json',JSON.stringify(data));const r=await fetch('/api/orders',{method:'POST',body:fd}),d=await r.json();if(!r.ok)return stat('#digitalStatus',d.detail||'Erro',false);stat('#digitalStatus',`Pedido ${d.order_number} criado.`,true);await loadOrders()}
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
function handleGangFile(f){
  if(!f.type.startsWith('image/'))return stat('#gangAddStatus','Selecione um arquivo de imagem.',false);
  handleGangImageURL(URL.createObjectURL(f), f.name);
}
function handleGangImageURL(url,name){
  const img=new Image();
  img.crossOrigin='anonymous';
  img.onload=()=>{
    $('#gangWidthCm').dataset.ratio=String(img.height/img.width);
    if(!$('#gangWidthCm').value)$('#gangWidthCm').value=18;
    $('#gangWidthCm').dataset.pendingUrl=url;
    $('#gangWidthCm').dataset.pendingName=name;
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
  $('#gangAiStatus').textContent='Gerando...';
  try{
    const r=await fetch('/api/ai/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'Erro');
    handleGangImageURL(d.url,'Arte IA');
    $('#gangAiStatus').textContent=`Gerada com ${d.model}`;
  }catch(e){ $('#gangAiStatus').textContent=e.message; }
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
  const qty=Math.max(1,Number($('#gangQty').value)||1);
  const newItem={
    id:crypto.randomUUID(), img:gangPendingImg,
    url:$('#gangWidthCm').dataset.pendingUrl,
    name:$('#gangWidthCm').dataset.pendingName||'Sem título',
    wCm, hCm, qty
  };
  gangItems.push(newItem);
  gangPendingImg=null;
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
  for(let i=0;i<it.qty;i++){
    const w=it.wCm, h=it.hCm;
    if(x+w > width-gap){ y+=shelfH+gap; x=gap; shelfH=0; }
    gangPieces.push({itemId:it.id,img:it.img,wCm:w,hCm:h,xCm:x,yCm:y,angle:0,flipX:false,flipY:false});
    x+=w+gap; shelfH=Math.max(shelfH,h);
  }
  recalcGangCanvasHeight();
}
function renderGangList(){
  $('#gangCount').textContent=gangItems.length;
  $('#gangList').innerHTML=gangItems.map(it=>`
    <div class="gang-item" data-item="${it.id}">
      <img src="${it.url}" alt="">
      <div class="gi-info"><b>${esc(it.name)}</b><small>${it.wCm}×${it.hCm} cm</small></div>
      <label style="margin:0"><small>Qtd</small><input type="number" min="1" value="${it.qty}" data-qty="${it.id}"></label>
      <button data-del="${it.id}" title="Remover">🗑</button>
    </div>
  `).join('') || '<p style="font-size:12px;color:var(--muted)">Nenhuma imagem adicionada.</p>';
  $$('[data-qty]').forEach(inp=>inp.onchange=()=>{
    const it=gangItems.find(x=>x.id===inp.dataset.qty);
    if(it){it.qty=Math.max(1,Number(inp.value)||1);autoArrangeGang(true);}
  });
  $$('[data-del]').forEach(b=>b.onclick=()=>{
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
  if(!CONFIG)return 0;
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
$('#gangHandleRotate').onclick=()=>{
  if(!gangSelected)return;
  rotateGangPiece(gangSelected);
  recalcGangCanvasHeight();
  renderGangCanvas();
  updatePrice();
};
$('#gangHandleResize').onpointerdown=ev=>{
  if(!gangSelected)return;
  ev.preventDefault();
  gangResize={piece:gangSelected,pointerId:ev.pointerId};
  gangCanvas.setPointerCapture(ev.pointerId);
};

async function exportGangSheet(){
  if(!gangPieces.length) throw new Error('Adicione ao menos uma imagem à chapa.');
  const width=sheetWidthCm(), dpi=CONFIG.dpi;
  const ow=Math.round(width/2.54*dpi), oh=Math.round(gangHeightCm/2.54*dpi);
  const out=document.createElement('canvas'); out.width=ow; out.height=oh;
  const ox=out.getContext('2d'), sx=ow/width, sy=oh/gangHeightCm;
  gangPieces.forEach(p=>{
    const x=p.xCm*sx, y=p.yCm*sy, w=p.wCm*sx, h=p.hCm*sy;
    const angle=p.angle||0, odd=angle%180===90;
    ox.save(); ox.translate(x+w/2,y+h/2);
    if(angle) ox.rotate(angle*Math.PI/180);
    if(p.flipX) ox.scale(-1,1);
    if(p.flipY) ox.scale(1,-1);
    const dw=odd?h:w, dh=odd?w:h;
    ox.drawImage(p.img,-dw/2,-dh/2,dw,dh);
    ox.restore();
  });
  const blob=await new Promise(r=>out.toBlob(r,'image/png'));
  return {blob, widthCm:width, heightCm:Number(gangHeightCm.toFixed(2)), widthPx:ow, heightPx:oh, dpi};
}
async function createGangOrder(){
  if(!currentCustomer){ openAuthModal(); return stat('#checkoutStatusGang','Entre ou crie uma conta antes de finalizar o pedido.',false); }
  if(!gangItems.length) return stat('#checkoutStatusGang','Adicione ao menos uma imagem.',false);
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
      sources:gangItems.map(it=>({type:'image',origin:'UPLOAD',name:it.name,width_cm:it.wCm,height_cm:it.hCm,qty:it.qty})),
      total:calcGangTotal()
    };
    const fd=new FormData();
    fd.append('order_json',JSON.stringify(data));
    fd.append('design',p.blob,'chapa_producao.png');
    const r=await fetch('/api/orders',{method:'POST',body:fd}), d=await r.json();
    if(!r.ok) throw new Error(d.detail||'Erro');
    stat('#checkoutStatusGang',`Pedido ${d.order_number} criado.`,true);
  }catch(e){ stat('#checkoutStatusGang',e.message,false); }
}

init().catch(e=>{console.error(e);alert('Erro ao iniciar: '+e.message)});
})();
