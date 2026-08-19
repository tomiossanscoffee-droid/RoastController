// 通知表示専用の最小限のService Worker。
// Android Chromeは`new Notification()`を直接サポートしておらず、
// ServiceWorkerRegistration.showNotification()経由でないと通知が表示されない
// (呼び出し自体は例外にならずに黙って何も表示しないことがある)。
// そのためだけに登録している(オフライン対応・fetchの横取り等は行わない)。
self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// サーバー(app/server.pyの_send_push_to_all)からのWeb Pushを受けて表示する。
// タブが閉じていたり画面がロックされていてもブラウザ・OSがこのイベントを
// 起こしてくれるため、スリープ中でも通知が届く。
// 通知は「アラート扱い(振動あり・重要度高)」で出す。これが重要:
// サイレント/低重要度の通知は、Wear OS(Pixel Watch等)が「ペア端末へブリッジ
// しない」ため、スマホには出てもウォッチに届かない。vibrate指定+requireInteraction
// で確実にアラートとして扱わせ、ウォッチにも転送されるようにする。
const NOTIFY_OPTIONS = {
  tag: 'roast-status',       // 焙煎状態の通知は1つにまとめる
  renotify: true,            // 新しい状態が来たら再度アラートする(tag必須)
  requireInteraction: true,  // 自動で消えず、見逃しにくい(ウォッチにも残りやすい)
  vibrate: [300, 120, 300],  // ← サイレント扱いを避けてウォッチへブリッジさせる肝
};

self.addEventListener('push', (event) => {
  let data = { title: 'Roast Studio', body: '' };
  try { if (event.data) data = event.data.json(); } catch (e) { /* 解析できなくても既定文言で表示する */ }
  event.waitUntil(
    self.registration.showNotification(data.title, { body: data.body, ...NOTIFY_OPTIONS })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((list) => {
      if (list.length > 0) return list[0].focus();
      return self.clients.openWindow('/mobile');
    })
  );
});
