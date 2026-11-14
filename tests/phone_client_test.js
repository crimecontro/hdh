// Runs the phone page's real client code (the CORE block of phone.html) against the real relay + agent.
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '../server/phone.html'), 'utf8');
const core = html.split('/*CORE-START*/')[1].split('/*CORE-END*/')[0];
const PcgClient = new Function(core + '; return PcgClient;')();

const ROOM = '0123456789abcdef'.repeat(2);
const URL = 'ws://127.0.0.1:18080/?role=phone&room=' + ROOM;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (extra !== undefined ? '  [' + extra + ']' : ''));
  if (!cond) fails++;
}
async function online(c) { for (let i = 0; i < 50 && !c.agentOnline; i++) await sleep(100); return c.agentOnline; }

(async () => {
  // ---- a client with the WRONG pin (also receives the broadcast frames but must not be able to read them)
  const bad = { frames: 0, badFrames: 0 };
  const wrong = new PcgClient(URL, ROOM, '000000', { onFrame: () => bad.frames++, onBadFrame: () => bad.badFrames++ });
  await wrong.init(); wrong.connect();
  check('agent reachable through relay', await online(wrong));
  let r = await wrong.request('lock');
  check('wrong PIN is rejected', r.ok === false && r.msg === 'wrong PIN', r.msg);

  // ---- the right client
  const got = { frames: [], states: [], ended: null, badFrames: 0 };
  const c = new PcgClient(URL, ROOM, '123456', {
    onFrame: (f) => got.frames.push(f), onState: (s) => got.states.push(s),
    onSessionEnd: (m) => (got.ended = m), onBadFrame: () => got.badFrames++,
  });
  await c.init(); c.connect();
  check('good client online', await online(c));
  r = await c.request('lock');   check('lock accepted', r.ok === true && r.locked === true);
  r = await c.request('unlock'); check('unlock accepted', r.ok === true && r.locked === false);

  let s = await c.request('session_start', { view: true, w: 640, fps: 5, q: 40 });
  check('session starts', s.ok === true && typeof s.sid === 'string', s.sid);
  check('session reports volume', s.vol === 50 && s.mute === false, s.vol);
  await sleep(2000);
  check('encrypted frames decrypt on phone', got.frames.length >= 3, got.frames.length);
  const f0 = got.frames[0] || new Uint8Array(0);
  check('frame is a JPEG', f0[0] === 0xff && f0[1] === 0xd8 && f0[2] === 0xff, f0.length + ' bytes');
  check('no decrypt errors for correct PIN', got.badFrames === 0);
  check('wrong-PIN client cannot read frames', bad.frames === 0 && bad.badFrames > 0, `readable=${bad.frames} unreadable=${bad.badFrames}`);

  await c.act({ a: 'move', x: 0.5, y: 0.25 });
  await c.act({ a: 'click', b: 'left', x: 0.1, y: 0.9 });
  await c.act({ a: 'click', b: 'right', x: 0.5, y: 0.5 });
  await c.act({ a: 'scroll', dy: -3 });
  await c.act({ a: 'key', k: 'enter' });
  await c.act({ a: 'key', k: 'format_c' });                 // not whitelisted -> must be ignored
  await c.act({ a: 'text', t: 'héllo 👍' });
  await c.act({ a: 'vol', v: 30 });
  await c.act({ a: 'volstep', d: -10 });
  await c.act({ a: 'mute', m: true });
  await c.act({ a: 'view', on: true, w: 480, fps: 3, q: 30 });
  await sleep(500);
  let last = got.states[got.states.length - 1] || {};
  check('volume / mute state returned', last.vol === 20 && last.mute === true, JSON.stringify(last));

  // ---- replay + forgery protection
  let captured = null; const origSend = c._send.bind(c);
  c._send = (o) => { if (o.type === 's') captured = o; origSend(o); };
  await c.act({ a: 'vol', v: 77 }); await sleep(200);
  c._send = origSend;
  await c.act({ a: 'vol', v: 10 }); await sleep(200);
  c.ws.send(JSON.stringify(captured));                      // replay of the old "vol 77" message
  c.ws.send(JSON.stringify({ type: 's', sid: c.sid, n: c.n + 5, d: '{"a":"vol","v":1}', mac: '00'.repeat(32) })); // forged
  await sleep(200);
  await c.act({ a: 'ping' }); await sleep(300);
  last = got.states[got.states.length - 1] || {};
  check('replayed and forged messages are ignored', last.vol === 10, 'vol=' + last.vol);

  // ---- idle timeout (agent test config: 5 s)
  const before = got.frames.length;
  await sleep(7500);
  check('idle session is closed by the agent', got.ended && got.ended.why === 'idle', JSON.stringify(got.ended));
  const after = got.frames.length; await sleep(1200);
  check('frames stop after session ends', got.frames.length === after && after >= before);
  check('actions after end are not sent', (await c.act({ a: 'vol', v: 99 })) === false);

  // ---- explicit end
  got.ended = null;
  s = await c.request('session_start', { view: false });
  check('second session starts (no screen)', s.ok === true);
  const n0 = got.frames.length; await sleep(1200);
  check('no frames when screen is off', got.frames.length === n0);
  await c.endSession(); await sleep(300);
  check('session end acknowledged', got.ended && got.ended.why === 'ended');

  r = await c.request('shutdown'); check('shutdown command accepted', r.ok === true);

  c.close(); wrong.close();
  console.log(fails ? `\n${fails} CHECK(S) FAILED` : '\nALL CHECKS PASSED');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('TEST CRASHED', e); process.exit(2); });
