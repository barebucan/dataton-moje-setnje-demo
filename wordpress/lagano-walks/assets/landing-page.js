(()=>{'use strict';
const $=selector=>document.querySelector(selector),$$=selector=>Array.from(document.querySelectorAll(selector));
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

$$('[data-dialog]').forEach(button=>button.addEventListener('click',()=>{
 button.closest('.header-menu')?.removeAttribute('open');
 $('#'+button.dataset.dialog)?.showModal();
}));
$$('.dialog-close').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$$('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{
 if(event.target!==dialog)return;
 const rect=dialog.getBoundingClientRect();
 if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();
}));
document.addEventListener('click',event=>{if(!event.target.closest('.header-menu'))$('.header-menu')?.removeAttribute('open');});

const route=[
 [15.978869,45.809354],[15.979092,45.809371],[15.979136,45.808478],[15.97921,45.808465],
 [15.979356,45.808455],[15.979376,45.807689],[15.979435,45.807701],[15.980142,45.807715],
 [15.980143,45.807589],[15.980158,45.807554],[15.981936,45.807597],[15.982033,45.806389],
 [15.982194,45.80639],[15.983854,45.806444],[15.983862,45.806436],[15.983932,45.805656],
 [15.984043,45.805661],[15.983932,45.805656],[15.983897,45.805597],[15.983859,45.805582],
 [15.983814,45.805578],[15.982903,45.805539],[15.982133,45.805495],[15.982089,45.805483],
 [15.980223,45.805383],[15.979464,45.805351],[15.978726,45.805332],[15.978713,45.805516],
 [15.978495,45.805514],[15.978429,45.805535],[15.978377,45.805565],[15.978344,45.8056],
 [15.9783,45.805704],[15.978291,45.805791],[15.978242,45.80579],[15.978149,45.805786],
 [15.978124,45.805785],[15.978123,45.805816],[15.977936,45.805817],[15.977936,45.805797],
 [15.977925,45.805783],[15.977879,45.805774],[15.976865,45.805738],[15.976727,45.80574],
 [15.976709,45.805751],[15.976511,45.805746],[15.976448,45.805727],[15.975432,45.805699],
 [15.975102,45.805701],[15.975063,45.805687],[15.974546,45.805667],[15.974094,45.805658],
 [15.974045,45.805681],[15.973917,45.805677],[15.973899,45.805665],[15.973864,45.806549],
 [15.974001,45.806552],[15.974023,45.806558],[15.97401,45.806773],[15.974129,45.806871],
 [15.974197,45.806912],[15.97418,45.806974],[15.974178,45.807031],[15.974219,45.807114],
 [15.974272,45.807164],[15.97439,45.807216],[15.974441,45.807226],[15.974515,45.807225],
 [15.974612,45.807293],[15.974653,45.807338],[15.974673,45.807388],[15.974785,45.80739],
 [15.974725,45.807875],[15.974533,45.808978],[15.974603,45.808979],[15.976131,45.809123],
 [15.976175,45.809117],[15.976302,45.809126],[15.976338,45.80914],[15.977585,45.80924],
 [15.977623,45.809223],[15.977649,45.809243],[15.977676,45.809231],[15.97788,45.809241],
 [15.977885,45.809272],[15.978869,45.809354]
].map(point=>[point[1],point[0]]);

let exampleMap;
function showExample(){
 const example=$('#route-example'),button=$('#find-example');
 if(!example||!button)return;
 example.hidden=false;
 button.setAttribute('aria-expanded','true');
 if(!exampleMap){
  exampleMap=L.map('example-map',{zoomControl:false,scrollWheelZoom:false,dragging:false,doubleClickZoom:false,boxZoom:false,keyboard:false,tap:false});
  L.maplibreGL({style:'https://tiles.openfreemap.org/styles/liberty',interactive:false}).addTo(exampleMap);
  const line=L.polyline(route,{color:'#a65b3c',weight:6,opacity:.95,lineCap:'round',lineJoin:'round',className:'example-route-line'}).addTo(exampleMap);
  L.marker(route[0],{interactive:false,icon:L.divIcon({className:'example-start-marker',html:'A',iconSize:[34,34],iconAnchor:[17,17]})}).addTo(exampleMap);
  [12,48,72].forEach((index,i)=>L.marker(route[index],{interactive:false,icon:L.divIcon({className:'example-place-marker',html:String(i+1),iconSize:[28,28],iconAnchor:[14,14]})}).addTo(exampleMap));
  exampleMap.fitBounds(line.getBounds(),{padding:[45,45]});
 }
 requestAnimationFrame(()=>{exampleMap.invalidateSize();example.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});});
}
$('#find-example')?.addEventListener('click',showExample);

async function loadSources(){
 try{
  const response=await fetch(window.LAGANO.api+'scene');
  if(!response.ok)return;
  const scene=await response.json(),meta=scene.meta;
  const list=$('#source-list');
  if(!list||!Array.isArray(meta?.sources))return;
  list.innerHTML='<p class="source-date-note">Preuzeto '+esc(meta.snapshot)+'. Datum preuzimanja nije potvrda da je svaka lokacija tada provjerena.</p>'+meta.sources.map(source=>`<article class="source-item"><a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.name)} <span aria-hidden="true">↗</span></a><div class="source-meta">${esc(source.publisher)} · <a href="${esc(source.license_url||source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.license)}</a></div><p>${esc(source.note)}</p></article>`).join('');
 }catch(error){/* Landing ostaje upotrebljiv i bez izvora u dijalogu. */}
}
loadSources();
})();
