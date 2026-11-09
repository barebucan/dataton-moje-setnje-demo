(()=>{'use strict';
const root=document.querySelector('#cinematic-demo');
if(!root||!window.maplibregl){window.LaganoCinematicDemo={ready:false};return;}
const $=selector=>root.querySelector(selector),duration=28,reduced=matchMedia('(prefers-reduced-motion: reduce)');
const route=[[45.809683,15.970019],[45.810127,15.970903],[45.810596,15.97222],[45.811223,15.974269],[45.81135,15.976314],[45.811237,15.978099],[45.810379,15.978081],[45.809385,15.977875],[45.808407,15.978008],[45.807486,15.978053],[45.807288,15.978634],[45.807219,15.978635]];
const places=[
 {position:route[0],title:'HNK i Zdenac života',kicker:'Trg Republike Hrvatske · ilustracija',copy:'Zdenac života Ivan Meštrović oblikovao je 1905., a na trg je postavljen 1912. Kompozicija se otkriva tek dok joj prilaziš.',image:'is-hnk'},
 {position:route[5],title:'Meteorološki stup',kicker:'Zrinjevac · ilustracija',copy:'Mramorni stup ovdje stoji od 1884. Pokazivao je temperaturu, tlak i vrijeme prolaznicima prije mobitela.',image:'is-zrinjevac'},
 {position:route[11],title:'Umjetnički paviljon',kicker:'Trg kralja Tomislava · ilustracija',copy:'Željezna jezgra izrađena je za Milenijsku izložbu 1896. U Zagrebu je Paviljon otvoren 15. prosinca 1898.',image:'is-paviljon'}
];
const motion=[{t:0,p:0},{t:1.2,p:0},{t:5.6,p:0},{t:8,p:5},{t:17,p:5},{t:19,p:11},{t:28,p:11}];
const lngLat=point=>[point[1],point[0]],routeLngLat=route.map(lngLat);
const map=new maplibregl.Map({container:$('#cinematic-map'),style:'https://tiles.openfreemap.org/styles/liberty',center:routeLngLat[0],zoom:16.2,pitch:54,bearing:-22,interactive:false,attributionControl:true,antialias:true});
const bounds=routeLngLat.reduce((box,point)=>box.extend(point),new maplibregl.LngLatBounds(routeLngLat[0],routeLngLat[0]));
map.on('load',()=>{map.addSource('demo-route',{type:'geojson',data:{type:'Feature',properties:{},geometry:{type:'LineString',coordinates:routeLngLat}}});map.addLayer({id:'demo-route-shadow',type:'line',source:'demo-route',paint:{'line-color':'#fffaf0','line-width':8,'line-opacity':.78}});map.addLayer({id:'demo-route',type:'line',source:'demo-route',paint:{'line-color':'#a65b3c','line-width':5,'line-opacity':.95,'line-dasharray':[1.3,2]}});if(reduced.matches)map.fitBounds(bounds,{padding:35,duration:0,pitch:0,bearing:0});});
const landmarkMarkers=places.map((place,index)=>{const element=document.createElement('div');element.className='cinematic-landmark-icon';element.innerHTML=`<span data-demo-landmark="${index}">${index+1}</span>`;return new maplibregl.Marker({element,anchor:'center'}).setLngLat(lngLat(place.position)).addTo(map);});
const personElement=document.createElement('div');personElement.className='cinematic-person-icon';personElement.innerHTML='<span class="cinematic-person-photo" role="img" aria-label="Profil korisnika"></span><span class="cinematic-person-pointer"></span><span class="cinematic-person-ring"></span>';
const person=new maplibregl.Marker({element:personElement,anchor:'bottom'}).setLngLat(routeLngLat[0]).addTo(map);
const card=$('.cinematic-landmark-card'),image=$('[data-demo-image]'),title=$('[data-demo-title]'),copy=$('[data-demo-copy]'),kicker=$('[data-demo-kicker]'),user=$('.cinematic-user-bubble'),guide=$('.cinematic-guide-bubble'),street=$('[data-demo-street]');
let elapsed=0,playing=false,last=0,frame=0,lastPan=0,active=-1,resumeOnVisible=false;
function routePositionAt(seconds){let a=motion[0],b=motion[motion.length-1];for(let i=1;i<motion.length;i++){if(seconds<=motion[i].t){a=motion[i-1];b=motion[i];break;}}const share=Math.max(0,Math.min(1,(seconds-a.t)/(b.t-a.t||1))),progress=a.p+(b.p-a.p)*(share*share*(3-2*share)),index=Math.min(route.length-2,Math.floor(progress)),part=progress-index;return[route[index][0]+(route[index+1][0]-route[index][0])*part,route[index][1]+(route[index+1][1]-route[index][1])*part];}
function activePlace(t){if(t>=1.2&&t<5.6)return 0;if(t>=8&&t<12.3)return 1;if(t>=19&&t<23.6)return 2;return-1;}
function isConversation(t){return t>=12.3&&t<17;}
function streetAt(t){if(t<5.7)return'Trg Republike Hrvatske';if(t<8)return'Masarykova ulica';if(t<17)return'Zrinjevac';if(t<19)return'Strossmayerov trg';return'Trg kralja Tomislava';}
function showPlace(index){if(index>=0&&index!==active){const place=places[index];kicker.textContent=place.kicker;title.textContent=place.title;copy.textContent=place.copy;image.className='cinematic-card-image '+place.image;image.setAttribute('aria-label','Ilustracija: '+place.title);active=index;}card.classList.toggle('is-visible',index>=0);card.classList.toggle('is-leaving',index<0&&active>=0);landmarkMarkers.forEach((marker,i)=>marker.getElement()?.classList.toggle('is-current',i===index));}
function cameraFor(point,place,conversation,animate=true){const mobile=root.clientWidth<700,xOffset=mobile&&conversation?105:0,yOffset=conversation?(mobile?105:-55):place>=0?(mobile?135:85):0,options={center:lngLat(point),zoom:16.45,pitch:54,bearing:-22,offset:[xOffset,yOffset],duration:animate&&!reduced.matches?420:0,essential:true};animate&&!reduced.matches?map.easeTo(options):map.jumpTo(options);}
function update(now=0,moveCamera=false){const point=routePositionAt(elapsed),place=activePlace(elapsed),conversation=isConversation(elapsed),asking=elapsed>=12.3&&elapsed<17,answering=elapsed>=14&&elapsed<17;person.setLngLat(lngLat(point));showPlace(place);user?.classList.toggle('is-visible',asking);guide?.classList.toggle('is-visible',answering);if(street)street.textContent=streetAt(elapsed);root.dataset.phase=elapsed>=27.2?'reset':conversation?'conversation':place>=0?'landmark':'walking';if(moveCamera&&now-lastPan>260){cameraFor(point,place,conversation);lastPan=now;}}
function restart(now){elapsed=0;active=-1;last=now;showPlace(-1);map.jumpTo({center:routeLngLat[0],zoom:16.2,pitch:54,bearing:-22});update(now,false);}
function tick(now){if(!playing)return;if(!last)last=now;elapsed+=(now-last)/1000;last=now;if(elapsed>=duration)restart(now);update(now,true);frame=requestAnimationFrame(tick);}
function play(){if(reduced.matches){playing=false;elapsed=9.5;active=-1;showPlace(-1);update(0,false);map.fitBounds(bounds,{padding:35,duration:0,pitch:0,bearing:0});return;}if(playing)return;playing=true;last=0;cancelAnimationFrame(frame);frame=requestAnimationFrame(tick);}
function pause(){playing=false;last=0;cancelAnimationFrame(frame);}
function replay(){pause();elapsed=0;active=-1;showPlace(-1);map.jumpTo({center:routeLngLat[0],zoom:16.2,pitch:54,bearing:-22});update();play();}
function seek(seconds){pause();elapsed=Math.max(0,Math.min(duration-.01,Number(seconds)||0));active=-1;showPlace(-1);update();const point=routePositionAt(elapsed),place=activePlace(elapsed),conversation=isConversation(elapsed);cameraFor(point,place,conversation,false);}
document.addEventListener('visibilitychange',()=>{if(document.hidden){resumeOnVisible=playing;pause();}else if(resumeOnVisible){resumeOnVisible=false;play();}});
new IntersectionObserver(entries=>{if(entries[0]?.isIntersecting){resumeOnVisible=false;play();}else if(playing){resumeOnVisible=true;pause();}},{threshold:.12}).observe(root);
if(reduced.matches){elapsed=9.5;}
update();window.LaganoCinematicDemo={ready:true,play,pause,replay,seek,get currentTime(){return elapsed;},get playing(){return playing;},duration};
})();
