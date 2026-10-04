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
