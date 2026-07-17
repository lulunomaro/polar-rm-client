/**
 * POLAR Gestalt Telemetry Relay
 *
 * 数据根源：游戏 parent → hud:attribute_update（与官方 HUD SDK 相同）。
 * 外发：UDP 127.0.0.1:9840 JSON polar.gestalt.attrs.v1
 *
 * 注意：浏览器/CEF 的 JS 通常不能直接 UDP。优先走 fetch HTTP 到本机桥；
 * 若 fetch 失败则 postMessage 给 parent 调试。配套 tools/gestalt_attr_http_bridge.py
 * 收 HTTP 再转 UDP/直接写 client 口。
 *
 * 同时尝试 WebSocket / fetch 到 127.0.0.1:9841/ingest
 */
(function () {
  'use strict';

  var bridge = new GestaltHUD.GestaltHUDBridge();
  var badge = document.getElementById('badge');
  var INGEST = 'http://127.0.0.1:9841/ingest';
  var sent = 0;
  var lastErr = '';
  var lastSend = 0;

  function setBadge(text) {
    if (badge) badge.textContent = text;
  }

  function keysOf(obj) {
    try { return Object.keys(obj || {}).length; } catch (e) { return 0; }
  }

  bridge.onInit(function (msg) {
    setBadge('Relay init team=' + msg.teamId + ' → ' + INGEST);
    bridge.sendReady('POLAR Gestalt Telemetry Relay', '1.0.0');
  });

  bridge.onAttributeUpdate(function (data) {
    var now = performance.now();
    // 限流：约 20Hz，避免塞爆本机桥
    if (now - lastSend < 50) return;
    lastSend = now;

    var payload = {
      protocol: 'polar.gestalt.attrs.v1',
      t: Date.now() / 1000,
      teamId: null,
      battle: data.battle || {},
      global: data.global || {},
      base: data.base || {},
      playerBattle: data.playerBattle || data.player_battle || {},
      player: data.player || {}
    };

    // 数字键保持：JSON 会把键变成 string，client 侧已兼容 str/int
    fetch(INGEST, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      mode: 'cors',
      cache: 'no-store'
    }).then(function () {
      sent += 1;
      lastErr = '';
      if (sent % 30 === 1) {
        setBadge('Relay OK #' + sent
          + ' battle=' + keysOf(payload.battle)
          + ' global=' + keysOf(payload.global)
          + ' base=' + keysOf(payload.base));
      }
    }).catch(function (err) {
      lastErr = String(err && err.message ? err.message : err);
      setBadge('Relay FAIL: ' + lastErr + ' (start gestalt_attr_http_bridge)');
      try {
        bridge.debugLog && bridge.debugLog('relay fail ' + lastErr);
      } catch (e) {}
    });
  });
})();
