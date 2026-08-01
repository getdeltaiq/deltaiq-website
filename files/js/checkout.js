/**
 * Subscribe pay screen → Railway Stripe Checkout.
 * Collects plan (monthly|annual) + email; server applies trial only for brand-new customers.
 */
(function () {
  var API_BASE =
    "https://deltaiq-signal-engine-production.up.railway.app/api/stripe";

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function selectedPlan() {
    var checked = $('input[name="plan"]:checked');
    return checked ? checked.value : "monthly";
  }

  function setPlan(plan) {
    var input = $('input[name="plan"][value="' + plan + '"]');
    if (input) input.checked = true;
    document.querySelectorAll("[data-plan-card]").forEach(function (card) {
      card.classList.toggle("is-selected", card.getAttribute("data-plan-card") === plan);
    });
    updatePriceHint();
  }

  function updatePriceHint() {
    var hint = $("#planHint");
    if (!hint) return;
    var plan = selectedPlan();
    hint.textContent =
      plan === "annual"
        ? "$690/year (~2 months free vs paying monthly)"
        : "$69/month";
  }

  function updateTrialNote(data) {
    var note = $("#trialNote");
    var foot = $("#trialFootnote");
    if (!note) return;
    if (data && data.trial_days === 0) {
      note.textContent =
        "Welcome back — your subscription begins immediately (no free trial).";
      if (foot) foot.style.display = "none";
    } else {
      note.innerHTML = "Brand New Customers try it free<span aria-hidden=\"true\">*</span>.";
      if (foot) {
        foot.style.display = "block";
        foot.textContent =
          "* Brand new customers are eligible for a free 14 calendar day trial period, prior to the start of the subscription.";
      }
    }
  }

  function checkEligibility() {
    var emailEl = $("#checkoutEmail");
    var email = emailEl ? emailEl.value.trim() : "";
    if (!email || email.indexOf("@") < 0) {
      updateTrialNote({ trial_days: 14 });
      return;
    }
    fetch(API_BASE + "/trial-eligibility?email=" + encodeURIComponent(email))
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (data && data.ok) updateTrialNote(data);
      })
      .catch(function () {
        /* keep default new-customer copy */
      });
  }

  function startCheckout(btn) {
    var emailEl = $("#checkoutEmail");
    var email = emailEl ? emailEl.value.trim() : "";
    var plan = selectedPlan();
    var label = btn ? btn.textContent : "";

    if (!email || email.indexOf("@") < 0) {
      alert("Enter the email you want on this subscription.");
      if (emailEl) emailEl.focus();
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = "Starting checkout…";
    }

    return fetch(API_BASE + "/create-checkout-session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: plan, email: email }),
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
        var msg = (err && err.message) || "";
        if (msg === "email_required") {
          msg = "Enter the email you want on this subscription.";
        }
        alert(msg || "Could not start checkout. Please try again or email support@getdeltaiq.com.");
        if (btn) {
          btn.disabled = false;
          btn.textContent = label;
        }
      });
  }

  document.addEventListener("click", function (e) {
    var card = e.target.closest("[data-plan-card]");
    if (card) {
      e.preventDefault();
      setPlan(card.getAttribute("data-plan-card") || "monthly");
      return;
    }
    var pay = e.target.closest("[data-checkout-start]");
    if (pay) {
      e.preventDefault();
      startCheckout(pay);
    }
  });

  document.addEventListener("change", function (e) {
    if (e.target && e.target.name === "plan") {
      setPlan(e.target.value);
    }
  });

  var emailEl = $("#checkoutEmail");
  if (emailEl) {
    emailEl.addEventListener("blur", checkEligibility);
    emailEl.addEventListener("change", checkEligibility);
  }

  setPlan(selectedPlan());
  updateTrialNote({ trial_days: 14 });
})();
