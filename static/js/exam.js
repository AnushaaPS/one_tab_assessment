(function () {
  // ---- Config ----
  const MAX_VIOLATIONS = 5;
  const VIOLATION_COOLDOWN_MS = 1500;
  const totalSec = (window.EXAM_DURATION_MIN || 90) * 60;

  // ---- Restore state from localStorage (for refresh / app switch) ----
  let remaining = parseInt(localStorage.getItem("remainingTime") || totalSec);
  let answers = JSON.parse(localStorage.getItem("answers") || "{}");
  let localViolations = parseInt(localStorage.getItem("violations") || 0);
  let lastViolationAt = 0;

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
    else if (elem.webkitRequestFullscreen) elem.webkitRequestFullscreen(); // iOS Safari
    else if (elem.msRequestFullscreen) elem.msRequestFullscreen(); // IE/Edge
    else {
      // fallback on mobiles that don’t support fullscreen
      alert("⚠️ Please keep this page open. Leaving it will count as a violation.");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    setTimeout(enterFS, 500);
    if (vioEl) vioEl.textContent = `Violations: ${localViolations} / ${MAX_VIOLATIONS}`;
    // Restore checked answers after refresh
    for (const [qid, val] of Object.entries(answers)) {
      const el = document.querySelector(`input[name="${qid}"][value="${val}"]`);
      if (el) el.checked = true;
    }
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
    localStorage.setItem("remainingTime", remaining);
    if (remaining < 0) finalizeAndSubmit();
  }
  setInterval(updateTimer, 1000);
  updateTimer();

  // ---- Capture answers (autosave) ----
  document.querySelectorAll('input[type="radio"]').forEach((inp) => {
    inp.addEventListener("change", () => {
      answers[inp.name] = inp.value;
      localStorage.setItem("answers", JSON.stringify(answers));
      fetch("/heartbeat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers, remaining })
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
    if (["PrintScreen", "F12"].includes(e.key)) e.preventDefault();
  });
  ["copy", "cut", "paste"].forEach((evt) =>
    document.addEventListener(evt, (e) => e.preventDefault())
  );

  // ---- Debounced violation trigger ----
  function triggerViolation(reason) {
    const now = Date.now();
    if (now - lastViolationAt < VIOLATION_COOLDOWN_MS) return;
    lastViolationAt = now;
    sendViolation(reason);
  }

  // ---- Violation handling ----
  async function sendViolation(reason) {
    localViolations++;
    localStorage.setItem("violations", localViolations);

    try {
      const res = await fetch("/violation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason })
      });
      const data = await res.json().catch(() => ({}));
      const serverCount =
        typeof data.violations === "number" ? data.violations : localViolations;

      if (vioEl) {
        vioEl.textContent = `Warning: ${reason}. Violations: ${serverCount} / ${MAX_VIOLATIONS}`;
      }

      if (serverCount > MAX_VIOLATIONS || data.status === "blocked") {
        finalizeAndSubmit();
      }
    } catch {
      if (vioEl) {
        vioEl.textContent = `Warning: ${reason}. Violations: ${localViolations} / ${MAX_VIOLATIONS}`;
      }
      if (localViolations > MAX_VIOLATIONS) finalizeAndSubmit();
    }
  }

  // ---- Event hooks ----
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) triggerViolation("Tab/App switch / Screen lock");
  });
  window.addEventListener("blur", () => triggerViolation("Window blur"));
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) triggerViolation("Exit fullscreen");
  });

  // Mobile-safe page exit tracking
  ["pagehide", "beforeunload"].forEach((evt) => {
    window.addEventListener(evt, () => {
      triggerViolation("Page reload / exit");
      try {
        const data = new Blob([JSON.stringify({ reason: evt })], {
          type: "application/json",
        });
        navigator.sendBeacon("/violation-beacon", data);
      } catch {}
    });
  });

  // ---- Prevent back nav ----
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
        body: JSON.stringify({ answers, remaining })
      });
    } catch {}
  }
  setInterval(heartbeat, 10000);

  // ---- Submit ----
  function finalizeAndSubmit() {
    if (hiddenAnswers) hiddenAnswers.value = JSON.stringify(answers || {});
    localStorage.removeItem("answers");
    localStorage.removeItem("remainingTime");
    localStorage.removeItem("violations");
    if (form) form.submit();
  }
  if (form) {
    form.addEventListener("submit", () => {
      if (hiddenAnswers) hiddenAnswers.value = JSON.stringify(answers || {});
      localStorage.removeItem("answers");
      localStorage.removeItem("remainingTime");
      localStorage.removeItem("violations");
    });
  }
})();
