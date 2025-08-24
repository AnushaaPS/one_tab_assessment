(function () {
  // ---- Config ----
  const MAX_VIOLATIONS = 5;
  const VIOLATION_COOLDOWN_MS = 1500; // prevent double-count from blur+visibility
  const totalSec = (window.EXAM_DURATION_MIN || 90) * 60;

  let remaining = totalSec;
  let localViolations = 0;
  let lastViolationAt = 0; // debounce timestamp
  const answers = {};

  // ---- Elements ----
  const timerEl = document.getElementById("timer");
  const vioEl = document.getElementById("viowarn");
  const form = document.getElementById("examForm");
  const hiddenAnswers = document.getElementById("answers_json");
  const goFS = document.getElementById("goFS");

  // ---- Fullscreen ----
  function enterFS() {
    const elem = document.documentElement;
    if (elem.requestFullscreen) elem.requestFullscreen();
    else if (elem.webkitRequestFullscreen) elem.webkitRequestFullscreen();
    else if (elem.msRequestFullscreen) elem.msRequestFullscreen();
  }

  document.addEventListener("DOMContentLoaded", () => {
    setTimeout(enterFS, 300);
    if (vioEl) vioEl.textContent = `Violations: 0 / ${MAX_VIOLATIONS}`;
  });
  if (goFS) goFS.addEventListener("click", enterFS);

  // ---- Timer ----
  function fmt(sec) {
    const h = Math.floor(sec / 3600).toString().padStart(2, "0");
    const m = Math.floor((sec % 3600) / 60).toString().padStart(2, "0");
    const s = (sec % 60).toString().padStart(2, "0");
    return `${h}:${m}:${s}`;
  }
  function updateTimer() {
    if (timerEl) timerEl.textContent = fmt(remaining);
    remaining--;
    if (remaining < 0) finalizeAndSubmit();
  }
  setInterval(updateTimer, 1000);
  updateTimer();

    // ---- Capture answers (with incremental save) ----
document.querySelectorAll('input[type="radio"]').forEach((inp) => {
  inp.addEventListener("change", () => {
    answers[inp.name] = inp.value; // name=QID, value=Option text

    // ✅ Save latest answers to server immediately
    fetch("/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers })
    }).catch(() => {});
  });
});


  // ---- Anti-cheat restrictions ----
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("keydown", function (e) {
    const blocked = ["c", "x", "v", "a", "s", "p", "f", "u", "k", "l", "j"];
    if ((e.ctrlKey || e.metaKey) && blocked.includes(e.key.toLowerCase())) {
      e.preventDefault();
    }
    if (e.key === "PrintScreen" || e.key === "F12") {
      e.preventDefault();
    }
  });
  ["copy", "cut", "paste"].forEach((evt) =>
    document.addEventListener(evt, (e) => e.preventDefault())
  );

  // ---- Debounced violation trigger ----
  function triggerViolation(reason) {
    const now = Date.now();
    if (now - lastViolationAt < VIOLATION_COOLDOWN_MS) return; // ignore duplicates
    lastViolationAt = now;
    sendViolation(reason);
  }

  // ---- Violation handling ----
  async function sendViolation(reason) {
    localViolations++;
    try {
      const res = await fetch("/violation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason })
      });
      // Always try to read JSON even if status is 401/403/etc.
      const data = await res.json().catch(() => ({}));
      const serverCount =
        typeof data.violations === "number" ? data.violations : localViolations;

      if (vioEl) {
        vioEl.textContent = `Warning: ${reason}. Violations: ${serverCount} / ${MAX_VIOLATIONS}`;
      }

      if (serverCount > MAX_VIOLATIONS || data.status === "blocked") {
        alert("❌ Too many violations. You are logged out.");
        window.location.href = "/";
      }
    } catch (e) {
      // If server unreachable, still enforce locally
      if (vioEl) {
        vioEl.textContent = `Warning: ${reason}. Violations: ${localViolations} / ${MAX_VIOLATIONS}`;
      }
      if (localViolations > MAX_VIOLATIONS) {
        window.location.href = "/";
      }
    }
  }

  // ---- Event hooks (use debounced triggerViolation) ----
  document.addEventListener("visibilitychange", () => {
    // Fires when tab becomes hidden (switch tab / lock / app switch)
    if (document.hidden) triggerViolation("Tab switch / Screen lock");
  });

  window.addEventListener("blur", () => {
    // Often fires together with visibilitychange; debounce prevents double count
    triggerViolation("Window blur");
  });

  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) {
      triggerViolation("Exit fullscreen");
    }
  });

  // On unload / sleep (beacon doesn't increment UI, only persists best-effort)
  window.addEventListener("pagehide", () => {
    try {
      const data = new Blob([JSON.stringify({ reason: "pagehide" })], { type: "application/json" });
      navigator.sendBeacon("/violation-beacon", data);
    } catch (e) {}
  });
  window.addEventListener("beforeunload", () => {
    try {
      const data = new Blob([JSON.stringify({ reason: "beforeunload" })], { type: "application/json" });
      navigator.sendBeacon("/violation-beacon", data);
    } catch (e) {}
  });

  // Prevent back nav
  history.pushState(null, document.title, location.href);
  window.addEventListener("popstate", () => {
    history.pushState(null, document.title, location.href);
  });

  // ---- Heartbeat ----
  async function heartbeat() {
    try {
      await fetch("/heartbeat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ remaining })
      });
    } catch (e) {}
  }
  setInterval(heartbeat, 10000);

  // ---- Submit ----
  function finalizeAndSubmit() {
    if (hiddenAnswers) hiddenAnswers.value = JSON.stringify(answers || {});
    if (form) form.submit();
  }
  if (form) {
    form.addEventListener("submit", () => {
      if (hiddenAnswers) hiddenAnswers.value = JSON.stringify(answers || {});
    });
  }
})();
