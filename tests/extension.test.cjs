const test = require('node:test');
const assert = require('node:assert/strict');
const { classify } = require('../extension/media.js');
const fs = require('node:fs');
const vm = require('node:vm');
test('manifest sniffing includes extensionless URLs and query strings', () => {
  assert.equal(classify('https://cdn.test/movie.m3u8?token=x'), 'hls');
  assert.equal(classify('https://cdn.test/get', 'application/dash+xml; charset=utf-8'), 'dash');
  assert.equal(classify('https://cdn.test/get', 'video/webm'), 'file');
});
test('segments and audio are never listed as complete videos', () => {
  for (const name of ['part.m4s', 'part.ts', 'part.cmfv', 'audio.m4a']) {
    assert.equal(classify('https://cdn.test/' + name, 'video/mp4'), null);
  }
  assert.equal(classify('https://cdn.test/get', 'video/mp2t'), null);
  assert.equal(classify('bad url'), null);
});

function browserFixture(fetchImpl = async () => ({})) {
  const context = { URL, location: new URL('https://www.bilibili.com/video/BV1WuYh6VEaS/'),
    document: {querySelector: () => ({})}, module: {exports: {}},
    XMLHttpRequest: function () {this.listeners = new Map();}, fetch: fetchImpl };
  context.XMLHttpRequest.prototype.open = function () {};
  context.XMLHttpRequest.prototype.addEventListener = function (event, callback) {this.listeners.set(event, callback);};
  context.XMLHttpRequest.prototype.removeEventListener = function (event, callback) {if(this.listeners.get(event) === callback)this.listeners.delete(event);};
  context.window = context;
  context.__INITIAL_STATE__ = {cid: 11, videoData:{bvid:'BV1WuYh6VEaS',pages:[{cid:11},{cid:22}]}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require.resolve('../extension/bili-observer.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require.resolve('../extension/scan.js'), 'utf8'), context);
  return context;
}
test('captures embedded play info before the Bili player clears it', () => {
  const c = browserFixture();
  c.__playinfo__ = {code:0,data:{dash:{video:[{baseUrl:'https://cdn.test/v.m4s',codecs:'avc1.64001f'}],audio:[{baseUrl:'https://cdn.test/a.m4s',codecs:'mp4a.40.2'}]}}};
  c.__playinfo__ = undefined;
  assert.equal(c.__playinfo__, undefined);
  assert.equal(c.scanPage().formats.length, 2);
  assert.equal(c.scanPage().formats[1].role, 'audio');
  c.location = new URL('https://www.bilibili.com/video/BV1Different/');
  assert.equal(c.scanPage().formats.length, 0);
});
test('does not mix different parts, or export protected playback data', () => {
  const c = browserFixture();
  c.__playinfo__ = {code:0,data:{is_drm:true,dash:{video:[{baseUrl:'https://cdn.test/v.m4s'}]}}};
  assert.equal(c.scanPage().formats.length, 0);
  c.__playinfo__ = {code:0,data:{dash:{video:[{baseUrl:'https://cdn.test/v.m4s'}]}}};
  c.location = new URL('https://www.bilibili.com/video/BV1WuYh6VEaS/?p=2');
  assert.equal(c.scanPage().formats.length, 0);
});
test('fetch observer preserves the original response and excludes unrelated data', async () => {
  const data = {code:0,data:{private:'do-not-copy',dash:{video:[{baseUrl:'https://cdn.test/v.m4s'}],audio:[{baseUrl:'https://cdn.test/a.m4s'}]}}};
  const response = {headers:{get:()=>null},clone:()=>({json:async()=>data})};
  const promise = Promise.resolve(response);
  const c = browserFixture(() => promise);
  assert.equal(c.fetch('https://api.bilibili.com/x/player/wbi/playurl?cid=11'), promise);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(c.scanPage().formats.length, 2);
  assert.equal(JSON.stringify(c.__videoCatchPlayback).includes('do-not-copy'), false);
});
test('reused XHR cannot associate another response with a previous video', () => {
  const c = browserFixture();
  const xhr = new c.XMLHttpRequest();
  xhr.open('GET','https://api.bilibili.com/x/player/playurl?cid=11');
  assert.equal(xhr.listeners.size, 1);
  xhr.open('GET','https://api.bilibili.com/x/web-interface/nav');
  assert.equal(xhr.listeners.size, 0);
  xhr.open('GET','https://api.bilibili.com/x/player/playurl?cid=11');
  xhr.responseType='json';
  xhr.response={code:0,data:{dash:{video:[{baseUrl:'https://cdn.test/v.m4s'}],audio:[{baseUrl:'https://cdn.test/a.m4s'}]}}};
  xhr.listeners.get('load')();
  assert.equal(c.scanPage().formats.length, 2);
});

function backgroundFixture(shared = {local:{token:'paired-token'}, session:{}}, protocol = 3) {
  const listeners = {};
  const calls = [];
  const event = name => ({addListener: listener => {listeners[name] = listener;}});
  const store = area => ({
    get: async keys => Object.fromEntries((Array.isArray(keys) ? keys : [keys]).filter(k => k in shared[area]).map(k => [k, shared[area][k]])),
    set: async values => Object.assign(shared[area], values),
  });
  const chrome = {
    storage:{local:store('local'), session:store('session')},
    tabs:{query:async()=>[{id:7,title:'视频',url:'https://example.com/watch',incognito:false},
      {id:8,title:'无痕',url:'https://example.com/private',incognito:true}],get:async()=>({}) ,
      onCreated:event('created'),onRemoved:event('removed'),onUpdated:event('updated')},
    action:{setBadgeText:async()=>{},setBadgeBackgroundColor:async()=>{}},
    alarms:{create:()=>{},onAlarm:event('alarm')},
    runtime:{onInstalled:event('installed'),onStartup:event('startup'),onMessage:event('message')},
    webRequest:{onBeforeSendHeaders:event('beforeHeaders'),onHeadersReceived:event('headers'),
      onCompleted:event('completed'),onErrorOccurred:event('error')},
    scripting:{executeScript:async()=>[]},
  };
  const context = {chrome, navigator:{userAgent:'Chrome'}, crypto:{randomUUID:()=>`session-id-${Math.random().toString(36).slice(2)}-123456789`},
    Date, Map, Promise, AbortController, AbortSignal, importScripts:()=>{}, classify:()=>null, scanPage:()=>{},
    fetch:async (url, options)=>{calls.push({url, body:JSON.parse(options.body)});return {ok:true,status:200,json:async()=>({protocol,watching:[]})};}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require.resolve('../extension/background.js'),'utf8'),context);
  return {context, listeners, calls, shared};
}

test('sync uses fixed loopback, skips incognito, and retains one browser session across worker restarts', async () => {
  const first = backgroundFixture();
  await vm.runInContext('sync()', first.context);
  assert.equal(first.calls[0].url, 'http://127.0.0.1:18796/sync');
  assert.deepEqual(first.calls[0].body.tabs.map(t=>t.id), [7]);
  const prior = first.calls[0].body.session;
  const restartedWorker = backgroundFixture(first.shared);
  await vm.runInContext('sync()', restartedWorker.context);
  assert.equal(restartedWorker.calls[0].body.session, prior);
  restartedWorker.listeners.startup();
  await new Promise(resolve=>setImmediate(resolve));
  assert.notEqual(restartedWorker.shared.session.browserSession, prior);
});

test('pause stops sync requests and resume sends a fresh sync', async () => {
  const fixture = backgroundFixture();
  const message = (type, paused) => new Promise(resolve => fixture.listeners.message({type,paused},{},resolve));
  await message('pause', true);
  await vm.runInContext('sync()', fixture.context);
  assert.equal(fixture.calls.length, 0);
  assert.equal(fixture.shared.local.paused, true);
  await message('pause', false);
  assert.equal(fixture.calls.length, 1);
  assert.equal(fixture.shared.local.paused, false);
});

test('resume without a pairing token reports unpaused state for popup recovery', async () => {
  const fixture = backgroundFixture({local:{}, session:{}});
  const message = paused => new Promise(resolve => fixture.listeners.message({type:'pause',paused},{},resolve));
  assert.equal((await message(true)).paused, true);
  const resumed = await message(false);
  assert.equal(resumed.ok, false);
  assert.equal(resumed.paused, false);
  assert.match(resumed.error, /配对码/);
});

test('protocol mismatch guidance and popup disclose current behavior', async () => {
  const fixture = backgroundFixture(undefined, 2);
  await assert.rejects(vm.runInContext('sync()', fixture.context), /协议 3/);
  const html = fs.readFileSync(require.resolve('../extension/popup.html'),'utf8');
  const install = fs.readFileSync(require.resolve('../extension/安装说明.txt'),'utf8');
  const manifest = require('../extension/manifest.json');
  for (const detail of ['30 秒','非无痕 HTTP(S)','ID、标题和网址','127.0.0.1:18796','暂停连接']) {
    assert.ok(html.includes(detail), detail);
  }
  assert.ok(install.includes(`扩展 ${manifest.version}`));
  assert.ok(install.includes('协议 3'));
});
