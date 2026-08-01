/**
 * Subscribe Now → Railway creates a Stripe Checkout Session and redirects.
 * Monthly is the session default; Annual is offered on Stripe Checkout via
 * the Dashboard subscription upsell on the monthly price.
 */
(function () {
  var API =
    "https://deltaiq-signal-engine-production.up.railway.app/api/stripe/create-checkout-session";

  function startCheckout(btn) {
    var label = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Starting checkout…";
    }
    return fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: "monthly" }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok || !data.ok || !data.url) {
            throw new Error((data && data.error) || "Checkout unavailable");
          }
          window.location.href = data.url;
        });
      })
      .catch(function (err) {
        alert(
          (err && err.message) ||
            "Could not start checkout. Please try again or email support@getdeltaiq.com."
        );
        if (btn) {
          btn.disabled = false;
          btn.textContent = label || "Subscribe Now";
        }
      });
  }

  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-checkout-start]");
    if (!el) return;
    e.preventDefault();
    startCheckout(el);
  });
})();
