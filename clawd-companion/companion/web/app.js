/* clawd-companion page logic.
   Reads state over Server-Sent Events, falls back to polling /state.
   Talks only to its own origin (127.0.0.1); it never sends anything anywhere. */
(function () {
  "use strict";

  var ROTATE_MS = 4000;
  var READY_MS = 5000;
  // Same list and order as READY_PHRASES in scripts/clawd_common.py.
  var READY_PHRASES = [
    "ready for next task",
    "tests green, mood green",
    "no bugs, only features",
    "coffee loaded, brain compiling",
    'git commit -m "progress"',
    "it works on my machine",
    "ship it, then polish",
    "one bug at a time",
    "refactor later, maybe",
    "semicolons in place"
  ];
  var idleSince = Date.now();
  var WELCOME_TEXT = "hey! welcome";
  var POLL_MS = 1000;

  var card = document.getElementById("card");
  var pillLabel = document.getElementById("pill-label");
  var capEyebrow = document.getElementById("cap-eyebrow");
  var capHeadline = document.getElementById("cap-headline");
  var capSub = document.getElementById("cap-sub");
  var bubbleTopic = document.getElementById("bubble-topic");
  var tlineA = document.getElementById("tline-a");
  var tlineB = document.getElementById("tline-b");
  var footProject = document.getElementById("foot-project");
  var soundBtn = document.getElementById("sound-btn");

  var current = null;
  var rotation = 0;
  var showingA = true;
  var soundOn = false;
  var lastConfirmAt = 0;

  // Inline the SVG so CSS can animate its parts, while keeping clawd.svg a
  // separate file the user can swap for their own art.
  fetch("./clawd.svg", { credentials: "omit" })
    .then(function (r) { return r.text(); })
    .then(function (svg) {
      document.getElementById("clawd-wrap").innerHTML = svg;
    })
    .catch(function () { /* the card still works without art */ });

  var COPY = {
    idle: { pill: "Idle", eyebrow: "Idle", headline: READY_PHRASES[0], sub: "Waiting for your next prompt." },
    thinking: { pill: "Thinking", eyebrow: "Thinking", headline: "Working on it.", sub: "Clawd reads the topic locally. Your prompt is never stored." },
    confirm: { pill: "Needs you", eyebrow: "Needs you", headline: "Claude needs your OK.", sub: "permission request." },
    hydrate: { pill: "Hydrate", eyebrow: "Hydration", headline: "Time to Drink water!", sub: "One glass. Then back to it." },
    done: { pill: "Done", eyebrow: "Done", headline: "Finished.", sub: "Ready for the next one." }
  };

  function text(el, value) {
    if (el && el.textContent !== value) { el.textContent = value; }
  }

  function render(s) {
    if (!s) { return; }
    var state = s.state || "idle";
    var copy = COPY[state] || COPY.idle;

    if (card.getAttribute("data-state") !== state) {
      card.setAttribute("data-state", state);
      idleSince = Date.now();
      rotation = 0;
      showingA = true;
    }

    text(pillLabel, copy.pill);
    text(capHeadline, copy.headline);

    if (state === "thinking") {
      var topic = s.topic || "Working";
      text(capEyebrow, "Thinking · " + topic);
      text(bubbleTopic, topic);
      text(capSub, copy.sub);
      paintThoughts(s);
    } else if (state === "confirm") {
      text(capEyebrow, copy.eyebrow);
      text(capSub, (s.project ? s.project + " · " : "") + copy.sub);
      maybeAlert(s);
    } else if (state === "hydrate") {
      var h = s.hydration || {};
      text(capEyebrow, copy.eyebrow);
      text(capHeadline, h.headline || copy.headline);
      text(capSub, h.sub || copy.sub);
    } else if (state === "idle") {
      text(capEyebrow, copy.eyebrow);
      paintIdle(s);
      text(capSub, copy.sub);
    } else {
      text(capEyebrow, copy.eyebrow);
      text(capSub, copy.sub);
    }

    text(footProject, s.project || "no session");

    if (s.sound_enabled && soundBtn.hasAttribute("hidden")) {
      soundBtn.removeAttribute("hidden");
    }
    current = s;
  }

  function paintThoughts(s) {
    var lines = s.thoughts || [];
    if (!lines.length) { return; }
    var idx = ((s.seed || 0) + rotation) % lines.length;
    var next = lines[idx];
    var target = showingA ? tlineA : tlineB;
    var other = showingA ? tlineB : tlineA;
    if (target.textContent !== next) { target.textContent = next; }
    target.classList.add("on");
    other.classList.remove("on");
  }

  // The server's welcome_until is in seconds on this machine's clock.
  function welcomeEndsMs(s) {
    var w = s && s.welcome_until;
    return typeof w === "number" ? w * 1000 : 0;
  }

  function readyPhrase(s) {
    var start = Math.max(idleSince, welcomeEndsMs(s));
    var step = Math.floor(Math.max(0, Date.now() - start) / READY_MS);
    return READY_PHRASES[step % READY_PHRASES.length];
  }

  function paintIdle(s) {
    var hello = Date.now() < welcomeEndsMs(s);
    card.classList.toggle("welcome", hello);
    var hi = s && s.name ? "hey " + s.name + ", welcome!" : WELCOME_TEXT;
    text(capHeadline, hello ? hi : readyPhrase(s));
  }

  // The server may send nothing while idle, so the phrase rotates on its own.
  setInterval(function () {
    if (current && (current.state || "idle") === "idle") {
      paintIdle(current);
    } else {
      card.classList.remove("welcome");
    }
  }, 1000);

  setInterval(function () {
    if (current && current.state === "thinking") {
      rotation += 1;
      showingA = !showingA;
      paintThoughts(current);
    }
  }, ROTATE_MS);

  /* --- optional, off by default: chime and notification on CONFIRM -------- */

  soundBtn.addEventListener("click", function () {
    soundOn = true;
    soundBtn.setAttribute("hidden", "hidden");
    try {
      if (window.Notification && Notification.permission === "default") {
        Notification.requestPermission();
      }
    } catch (e) { /* ignore */ }
    chime();
  });

  function chime() {
    if (!soundOn) { return; }
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) { return; }
      var ctx = new Ctx();
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = 660;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.45);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.5);
      setTimeout(function () { try { ctx.close(); } catch (e) {} }, 800);
    } catch (e) { /* ignore */ }
  }

  function maybeAlert(s) {
    var since = s.state_since || 0;
    if (since === lastConfirmAt) { return; }
    lastConfirmAt = since;
    chime();
    try {
      if (soundOn && window.Notification && Notification.permission === "granted") {
        new Notification("Claude needs your OK", {
          body: (s.project || "") + " · permission request"
        });
      }
    } catch (e) { /* ignore */ }
  }

  /* --- transport ---------------------------------------------------------- */

  var polling = null;

  function startPolling() {
    if (polling) { return; }
    polling = setInterval(function () {
      fetch("./state", { credentials: "omit" })
        .then(function (r) { return r.json(); })
        .then(render)
        .catch(function () { /* server down: keep the last frame */ });
    }, POLL_MS);
  }

  function connect() {
    if (!window.EventSource) { return startPolling(); }
    var es;
    try {
      es = new EventSource("./events");
    } catch (e) {
      return startPolling();
    }
    es.onmessage = function (ev) {
      try { render(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
    };
    es.onerror = function () {
      // EventSource retries on its own; polling covers the gap.
      startPolling();
    };
  }

  connect();
  fetch("./state", { credentials: "omit" })
    .then(function (r) { return r.json(); })
    .then(render)
    .catch(function () {});
})();
