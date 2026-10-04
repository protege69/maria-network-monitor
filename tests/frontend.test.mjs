import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

// Small DOM adapter exercises rendering/data changes without an HA installation.
class Node {
  constructor(tag='node') {this.tag=tag;this.children=[];this.attrs={};this.listeners={};this.style={};this.className='';this.ownText='';}
  set textContent(text) {this.ownText=String(text);this.children=[];}
  get textContent() {return this.ownText+this.children.map(child=>child.textContent).join(' ');}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=nodes;this.ownText='';}
  setAttribute(name,value) {this.attrs[name]=String(value);}
  addEventListener(name,fn) {this.listeners[name]=fn;}
  click() {this.clicked=true;}
}
globalThis.document={createElement:tag=>new Node(tag)};
globalThis.HTMLElement=class extends Node {
  constructor(){super();this.isConnected=true;}
  attachShadow(){this.shadowRoot=new Node('shadow');}
  dispatchEvent(event){this.lastEvent=event;return true;}
};
globalThis.CustomEvent=class {constructor(type,options){this.type=type;Object.assign(this,options);}};
globalThis.window={location:{pathname:'/maria-network-monitor/overview'}};
const registry=new Map();
globalThis.customElements={get:name=>registry.get(name),define:(name,cls)=>registry.set(name,cls)};
const source=fs.readFileSync(new URL('../maria_network_monitor/frontend/maria-network-card.js',import.meta.url),'utf8');
const {inventories,devicesFor}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const Card=registry.get('maria-network-card');
const site=()=>({schema:1,maria_role:'site_inventory',site_id:'demo',name:'Demo branch',router_online:true,
  stale:false,total:1,online:1,offline:0,printer_problems:0,devices:[{
    id:'maria_demo',online_unique_id:'maria_demo_online',name:'Demo PC',type:'computer',online:true}]});
const hass=(item=site(),state='1')=>({states:{'sensor.renamed_inventory':{state,attributes:item}},callWS:async()=>[]});
const allNodes=node=>[node,...node.children.flatMap(allNodes)];

test('persistent snapshot becomes stale by timestamp without losing devices',()=>{
  const data=site(); data.observed_at=new Date(Date.now()-3600000).toISOString(); data.freshness_seconds=180;
  assert.equal(inventories(hass(data))[0].monitor_available,false);
  assert.equal(inventories(hass(data))[0].devices.length,1);
});

test('daily report distinguishes measured pages from unknown and unallocated',()=>{
  const data=site(),h=hass(data);
  h.states['sensor.report']={state:'2026-10-01',attributes:{maria_role:'daily_report',site_id:'demo',timezone:'UTC',today:'2026-10-01',
    generated_at:Date.now()/1000,days:[{date:'2026-10-01',pages:42,has_samples:true,rows:[
      {id:'p1',name:'Printer A',pages:42,unallocated:8,flags:['boundary_gap']},
      {id:'p2',name:'Printer B',pages:null,unallocated:0,flags:['no_samples']}]}]}};
  const c=new Card();c.setConfig({mode:'report'});c.hass=h;
  assert.match(c.shadowRoot.textContent,/42/);
  assert.match(c.shadowRoot.textContent,/Нет данных/);
  assert.match(c.shadowRoot.textContent,/8 стр. не распределено/);
  assert.match(c.shadowRoot.textContent,/Скачать CSV/);
});

test('history graph resolves current entity id instead of deriving from names',async()=>{
  const c=new Card(),data=site(),h=hass(data);
  h.callWS=async()=>[{platform:'mqtt',unique_id:'maria_demo_online',entity_id:'binary_sensor.renamed'}];
  let config;
  window.loadCardHelpers=async()=>({createCardElement:cfg=>{config=cfg;return new Node('history-graph');}});
  c.setConfig({mode:'history',site_id:'demo'});c.hass=h;
  const button=allNodes(c.shadowRoot).find(n=>n.tag==='button'&&n.textContent.includes('Показать график'));
  await button.listeners.click();
  assert.deepEqual(config.entities,['binary_sensor.renamed']);
  assert.equal(config.hours_to_show,24);
  assert.equal(c.historyCard.hass,h);
  delete window.loadCardHelpers;
});

test('history API failure is explained in the card',async()=>{
  const c=new Card(),h=hass();h.callWS=async()=>{throw Error('not authorized');};
  c.setConfig({mode:'history',site_id:'demo'});c.hass=h;
  await allNodes(c.shadowRoot).find(n=>n.tag==='button'&&n.textContent.includes('Показать график')).listeners.click();
  assert.match(c.shadowRoot.textContent,/История пока недоступна/);
});

test('CSV preserves unknown values and escapes formula-like device names',async()=>{
  const h=hass(),c=new Card();let blob;
  h.states['sensor.report']={state:'2026-10-01',attributes:{maria_role:'daily_report',site_id:'demo',timezone:'UTC',today:'2026-10-01',
    generated_at:Date.now()/1000,days:[{date:'2026-10-01',pages:0,has_samples:false,rows:[
      {id:'p',name:'=SUM(1,2)',pages:null,unallocated:0,flags:['no_samples']}]}]}};
  const original=URL.createObjectURL;
  URL.createObjectURL=value=>{blob=value;return 'blob:demo';};
  try {
    c.setConfig({mode:'report'});c.hass=h;
    allNodes(c.shadowRoot).find(n=>n.tag==='button'&&n.textContent==='Скачать CSV').listeners.click();
    const csv=await blob.text();
    assert.match(csv,/'=SUM\(1,2\)/);
    assert.match(csv,/"";"0"/);
    assert.match(csv,/Нет показаний/);
    assert.match(csv,/Часовой пояс/);
  } finally {URL.createObjectURL=original;}
});

test('inventory selection ignores unrelated HA sensors and respects unavailability',()=>{
  const h=hass();h.states['sensor.other']={state:'1',attributes:{devices:[]}};
  assert.equal(inventories(h).length,1);
  h.states['sensor.renamed_inventory'].state='unavailable';
  assert.equal(inventories(h)[0].monitor_available,false);
});
test('new static lease changes an already rendered card without setConfig/reload',()=>{
  const c=new Card(),data=site(),h=hass(data);
  c.setConfig({mode:'group',site_id:'demo',group:'computers',title:'Computers'});c.hass=h;
  data.devices.push({id:'maria_demo2',online_unique_id:'maria_demo2_online',name:'New PC',type:'computer',online:true});
  c.hass=h;
  assert.match(c.shadowRoot.textContent,/New PC/);
  assert.equal(allNodes(c.shadowRoot).filter(n=>n.tag==='button').length,2);
});
test('repeated updates do not append duplicate device cards',()=>{
  const c=new Card(),h=hass();c.setConfig({mode:'group',site_id:'demo',group:'computers',title:'Computers'});
  for(let i=0;i<10;i++)c.hass=h;
  assert.equal(allNodes(c.shadowRoot).filter(n=>n.tag==='button').length,1);
});
test('lease removal removes the rendered tile',()=>{
  const c=new Card(),data=site(),h=hass(data);c.setConfig({mode:'group',site_id:'demo',group:'computers',title:'Computers'});c.hass=h;
  data.devices=[];c.hass=h;
  assert.equal(allNodes(c.shadowRoot).filter(n=>n.tag==='button').length,0);
});
test('router outage retains list but shows unknown current connection',()=>{
  const c=new Card(),data=site();data.stale=true;
  c.setConfig({mode:'group',site_id:'demo',group:'computers',title:'Computers'});c.hass=hass(data);
  assert.match(c.shadowRoot.textContent,/Demo PC/);
  assert.match(c.shadowRoot.textContent,/Нет актуальных данных/);
});
test('monitor unavailable overrides retained online state',()=>{
  const c=new Card();c.setConfig({mode:'summary',site_id:'demo'});c.hass=hass(site(),'unavailable');
  assert.match(c.shadowRoot.textContent,/Монитор недоступен/);
  assert.match(c.shadowRoot.textContent,/Нет актуального опроса/);
});
test('offline devices sort first within type, and other types are excluded',()=>{
  const data=site();data.devices.push({name:'Offline PC',type:'computer',online:false},{name:'Camera',type:'camera',online:false});
  assert.equal(devicesFor(data,'computers')[0].name,'Offline PC');
  assert.equal(devicesFor(data,'computers').length,2);
});
test('untrusted DHCP names are text, never HTML or markup elements',()=>{
  const c=new Card(),data=site();data.devices[0].name='<img src=x onerror=alert(1)>';
  c.setConfig({mode:'group',site_id:'demo',group:'computers',title:'Computers'});c.hass=hass(data);
  assert.match(c.shadowRoot.textContent,/<img src=x/);
  assert.equal(allNodes(c.shadowRoot).filter(n=>n.tag==='img').length,0);
});
test('history resolves a renamed MQTT entity by unique_id',async()=>{
  const c=new Card();c.setConfig({mode:'summary',site_id:'demo'});
  c.hass={...hass(),callWS:async()=>[{platform:'mqtt',unique_id:'maria_demo_online',entity_id:'binary_sensor.user_renamed'}]};
  await c.moreInfo('maria_demo_online');
  assert.equal(c.lastEvent.type,'hass-more-info');
  assert.equal(c.lastEvent.detail.entityId,'binary_sensor.user_renamed');
});
