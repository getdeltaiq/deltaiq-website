/**
 * Optional GA4 loader + conversion helpers.
 * Inactive until window.DELTAIQ_GA4_ID is set in site-config.js.
 */
(function () {
  var id = window.DELTAIQ_GA4_ID;
  if (!id || typeof id !== "string" || id.indexOf("G-") !== 0) return;

  var s = document.createElement("script");
  s.async = true;
  s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(id);
  document.head.appendChild(s);

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    window.dataLayer.push(arguments);
  }
  window.gtag = gtag;
  gtag("js", new Date());
  gtag("config", id);

  window.deltaiqTrack = function (eventName, params) {
    if (typeof window.gtag !== "function") return;
    window.gtag("event", eventName, params || {});
  };
})();
