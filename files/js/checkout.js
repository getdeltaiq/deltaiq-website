/**
 * Subscribe Now → consent gate (required ToS/disclosures; optional SMS) → Checkout.
 * Monthly is the default; Annual can be selected on the Offers cards.
 */
(function () {
  var API =
    "https://deltaiq-signal-engine-production.up.railway.app/api/stripe/create-checkout-session";

  var modalEl = null;
  var pendingBtn = null;

  function selectedPlan() {
    var checked = document.querySelector('input[name="plan"]:checked');
    return checked ? checked.value : "monthly";
  }

  function setPlan(plan) {
    var input = document.querySelector('input[name="plan"][value="' + plan + '"]');
    if (input) input.checked = true;
    document.querySelectorAll("[data-plan-card]").forEach(function (card) {
      card.classList.toggle(
        "is-selected",
        card.getAttribute("data-plan-card") === plan
      );
    });
    var hint = document.getElementById("planHint");
    if (hint) {
      hint.textContent =
        plan === "annual"
          ? "$690/year (~2 months free vs paying monthly)"
          : "$69/month";
    }
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "className") node.className = attrs[k];
        else if (k === "html") node.innerHTML = attrs[k];
        else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function checkboxRow(id, htmlLabel) {
    var input = el("input", {
      type: "checkbox",
      id: id,
      name: id,
      className: "consent-check",
    });
    // Unchecked by default — ToS requires affirmative unchecked boxes.
    input.checked = false;
    var label = el("label", { className: "consent-label", for: id });
    label.innerHTML = htmlLabel;
    return el("div", { className: "consent-row" }, [input, label]);
  }

  function ensureModal() {
    if (modalEl) return modalEl;
    modalEl = el("div", {
      className: "consent-modal",
      id: "deltaiq-consent-modal",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": "deltaiq-consent-title",
      hidden: "hidden",
    });

    var panel = el("div", { className: "consent-panel" });
    panel.appendChild(
      el("h2", { id: "deltaiq-consent-title", className: "consent-title" }, [
        "Before you subscribe",
      ])
    );
    panel.appendChild(
      el("p", { className: "consent-lede" }, [
        "Two acknowledgments are required (Terms §3). Optional text notifications are separate (Terms §5). All boxes start unchecked.",
      ])
    );

    panel.appendChild(
      checkboxRow(
        "ack_tos_privacy",
        'I have read and agree to the <a href="terms.html" target="_blank" rel="noopener">Terms of Service</a> and <a href="privacy.html" target="_blank" rel="noopener">Privacy Policy</a>, and I confirm I am at least 18 and a U.S. resident.'
      )
    );
    panel.appendChild(
      checkboxRow(
        "ack_disclosures",
        'I have read and understand the <a href="disclosures.html" target="_blank" rel="noopener">Important Disclosures</a> and the “We Are Not” disclosures (including that DeltaIQ is a publisher, not an investment adviser, and alerts are not personalized recommendations).'
      )
    );
    panel.appendChild(
      checkboxRow(
        "ack_sms",
        'Would you also like to receive optional <strong>text notifications</strong> when new subscriber dashboard content is available? Optional. Recurring automated texts prompt you to sign in at <a href="account.html" target="_blank" rel="noopener">My Account</a>; they do not include market or ticker detail. Message frequency varies with publication activity. Message and data rates may apply. Reply <strong>STOP</strong> to opt out, <strong>HELP</strong> for help, or email <a href="mailto:support@getdeltaiq.com">support@getdeltaiq.com</a>. You can finish checkout and use the dashboard without SMS. Opting out of SMS does not by itself cancel billing.'
      )
    );

    var err = el("p", { className: "consent-error", id: "deltaiq-consent-error", hidden: "hidden" });
    panel.appendChild(err);

    var actions = el("div", { className: "consent-actions" });
    actions.appendChild(
      el("button", {
        type: "button",
        className: "btn-ghost consent-cancel",
        onClick: function () {
          closeModal();
        },
      }, ["Cancel"])
    );
    actions.appendChild(
      el("button", {
        type: "button",
        className: "btn-primary consent-continue",
        id: "deltaiq-consent-continue",
        onClick: function () {
          onContinue();
        },
      }, ["Continue to secure checkout"])
    );
    panel.appendChild(actions);

    modalEl.appendChild(panel);
    modalEl.addEventListener("click", function (e) {
      if (e.target === modalEl) closeModal();
    });
    document.body.appendChild(modalEl);
    return modalEl;
  }

  function openModal(btn) {
    pendingBtn = btn;
    var m = ensureModal();
    m.removeAttribute("hidden");
    document.body.classList.add("consent-modal-open");
    var err = document.getElementById("deltaiq-consent-error");
    if (err) {
      err.hidden = true;
      err.textContent = "";
    }
    ["ack_tos_privacy", "ack_disclosures", "ack_sms"].forEach(function (id) {
      var box = document.getElementById(id);
      if (box) box.checked = false;
    });
  }

  function closeModal() {
    if (!modalEl) return;
    modalEl.setAttribute("hidden", "hidden");
    document.body.classList.remove("consent-modal-open");
    pendingBtn = null;
  }

  function readAcks() {
    return {
      ack_tos_privacy: !!(document.getElementById("ack_tos_privacy") || {}).checked,
      ack_disclosures: !!(document.getElementById("ack_disclosures") || {}).checked,
      ack_sms: !!(document.getElementById("ack_sms") || {}).checked,
      ack_age_us: !!(document.getElementById("ack_tos_privacy") || {}).checked,
    };
  }

  function onContinue() {
    var acks = readAcks();
    var err = document.getElementById("deltaiq-consent-error");
    if (!acks.ack_tos_privacy || !acks.ack_disclosures) {
      if (err) {
        err.hidden = false;
        err.textContent =
          "Please check the Terms and Disclosures boxes to continue. SMS is optional.";
      }
      return;
    }
    startCheckout(pendingBtn, acks);
  }

  function startCheckout(btn, acks) {
    var label = btn ? btn.textContent : "";
    var continueBtn = document.getElementById("deltaiq-consent-continue");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Starting checkout…";
    }
    if (continueBtn) {
      continueBtn.disabled = true;
      continueBtn.textContent = "Starting checkout…";
    }
    return fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        plan: selectedPlan(),
        ack_tos_privacy: !!acks.ack_tos_privacy,
        ack_disclosures: !!acks.ack_disclosures,
        ack_sms: !!acks.ack_sms,
        ack_age_us: !!acks.ack_age_us,
      }),
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
        if (continueBtn) {
          continueBtn.disabled = false;
          continueBtn.textContent = "Continue to secure checkout";
        }
      });
  }

  document.addEventListener("click", function (e) {
    var card = e.target.closest("[data-plan-card]");
    if (card) {
      setPlan(card.getAttribute("data-plan-card") || "monthly");
      return;
    }
    var target = e.target.closest("[data-checkout-start]");
    if (!target) return;
    e.preventDefault();
    openModal(target);
  });

  setPlan(selectedPlan());

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modalEl && !modalEl.hasAttribute("hidden")) {
      closeModal();
    }
  });
})();
