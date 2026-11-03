/* For You card. Loaded after the inline dashboard script. */
(function () {
  var css = document.createElement("style");
  css.textContent = [
    "#forYou{padding:16px 18px}",
    "#forYou p{margin:0 0 10px;color:var(--muted);font-size:14px;line-height:1.4}",
    "#forYou label{display:block;margin:8px 0 6px;color:var(--muted);font-size:13px}",
    "#forYou textarea{width:100%;background:#1f171d;border:1px solid var(--line);color:var(--ink);",
    "border-radius:10px;padding:10px 12px;font:14px/1.4 var(--mono);resize:vertical}",
    "#forYou textarea:focus{outline:none;border-color:var(--amber)}",
    "#forYou .fy-check{display:flex;align-items:center;gap:8px;margin:10px 0}",
    "#forYou .fy-actions{display:flex;gap:8px;margin-bottom:10px}",
    "#forYou button{background:var(--amber);color:#1a1420;border:none;border-radius:10px;",
    "padding:10px 14px;font:800 12px var(--mono);letter-spacing:.06em;cursor:pointer}",
    "#forYou button.quiet{background:#1f171d;color:var(--ink);border:1px solid var(--line)}",
    "#forYou button:disabled{background:#4a3a4a;color:#8a7a70;cursor:wait}",
    "#forYou .fy-item{display:flex;gap:10px;align-items:center;padding:8px 0;border-top:1px solid #ffffff10}",
    "#forYou .fy-item b{display:block;font-size:14px}",
    "#forYou .fy-item span{color:var(--muted);font-size:12px}",
    "#forYou .fy-item a{color:var(--amber)}",
    "#forYou .err{color:var(--rose)}"
  ].join("");
  document.head.appendChild(css);

  var msg = document.getElementById("forYouMsg");
  var tag = document.getElementById("forYouTag");
  var auth = document.getElementById("forYouAuth");
  var box = document.getElementById("forYouTopics");
  var summary = document.getElementById("forYouSummary");
  var list = document.getElementById("forYouList");
  var bar = document.getElementById("forYouBar");
  var previewBtn = document.getElementById("forYouPreview");
  var goBtn = document.getElementById("forYouGo");
  if (!msg || !box) return;

  function lines() {
    return box.value.split(/\n+/).map(function (line) { return line.trim(); }).filter(Boolean).slice(0, 4);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }

  function setBusy(on) {
    previewBtn.disabled = on;
    goBtn.disabled = on;
  }

  function paintStatus(data) {
    msg.textContent = data.message || "";
    tag.textContent = data.connection || "topics";
    if (data.grok) tag.textContent += " · grok";
    var html = "";
    if (data.connection && data.connection !== "disconnected" && data.connection !== "needs_reauth") {
      html = "<button type='button' class='quiet' id='forYouDisconnect'>" +
        (data.username ? "Disconnect @" + esc(data.username) : "Disconnect X") + "</button>";
    } else if (data.x_configured) {
      html = "<a href='/api/for-you/connect'>Connect X</a>";
    } else {
      html = "<span>X sign-in needs X_CLIENT_ID and X_CLIENT_SECRET. Typed topics still work.</span>";
    }
    auth.innerHTML = html;
    var disconnect = document.getElementById("forYouDisconnect");
    if (disconnect) disconnect.onclick = function () {
      fetch("/api/for-you/disconnect", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"})
        .then(function (r) { return r.json(); })
        .then(paintStatus)
        .catch(function () { msg.textContent = "Could not disconnect."; });
    };
    if (document.activeElement !== box && Array.isArray(data.topics)) box.value = data.topics.join("\n");
  }

  function loadStatus() {
    return fetch("/api/for-you", {cache: "no-store"}).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(paintStatus).catch(function () {
      msg.textContent = "For You needs the control room API. Start ./run.sh.";
    });
  }

  function saveTopics() {
    return fetch("/api/for-you/topics", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Agent-Id": "dashboard"},
      body: JSON.stringify({topics: lines()})
    }).then(function (r) { return r.json().then(function (j) { return {ok: r.ok, body: j}; }); });
  }

  function paintItems(items) {
    list.innerHTML = (items || []).map(function (item) {
      var story = item.story_title
        ? "<span>" + esc(item.story_title) + (item.story_url ? " · <a href='" + esc(item.story_url) + "' target='_blank' rel='noopener'>story</a>" : "") + "</span>"
        : "<span>" + esc(item.because || item.state || "") + "</span>";
      var play = item.track_url
        ? "<button type='button' data-play='" + esc(item.track_url) + "' data-title='" + esc(item.title || item.topic) + "'>Play</button>"
        : "";
      var err = item.error ? "<span class='err'>" + esc(item.error) + "</span>" : story;
      return "<div class='fy-item'><div style='flex:1'><b>" + esc(item.topic) + "</b>" + err + "</div>" + play + "</div>";
    }).join("");
    list.querySelectorAll("[data-play]").forEach(function (button) {
      button.onclick = function () { playItem(button.getAttribute("data-play"), button.getAttribute("data-title")); };
    });
  }

  function playItem(url, title) {
    if (typeof ensureAudio === "function") ensureAudio();
    if (typeof selectTrack === "function") selectTrack(url, {title: title, file: url}, false);
    var audio = document.getElementById("audio");
    var pending = audio && audio.play && audio.play();
    if (pending && pending.catch) pending.catch(function () {
      if (msg.textContent.indexOf("press play") < 0) msg.textContent += " (press play)";
    });
  }

  function poll(id) {
    bar.style.width = "8%";
    return (async function wait() {
      var job = await nextJob(id);
      bar.style.width = (job.progress || 8) + "%";
      if (job.message) msg.textContent = job.message;
      if (job.rank_note) msg.textContent = job.message + " " + job.rank_note;
      paintItems(job.items || []);
      if (job.state === "error") {
        msg.innerHTML = "<span class='err'>" + esc(job.error || "The batch failed.") + "</span>";
        return;
      }
      if (job.state === "done") {
        bar.style.width = "100%";
        if (typeof loadEpisode === "function") await loadEpisode();
        return;
      }
      await new Promise(function (ok) { setTimeout(ok, 400); });
      return wait();
    })();
  }

  previewBtn.onclick = function () {
    setBusy(true);
    saveTopics().then(function () {
      return fetch("/api/for-you/preview", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Agent-Id": "dashboard"},
        body: JSON.stringify({topics: lines()})
      });
    }).then(function (r) { return r.json().then(function (j) { return {ok: r.ok, body: j}; }); })
      .then(function (res) {
        if (!res.ok) {
          msg.textContent = res.body.error || "Could not preview.";
          return;
        }
        msg.textContent = res.body.message || "";
        if (res.body.rank_note) msg.textContent += " " + res.body.rank_note;
        paintItems(res.body.interests || []);
      })
      .catch(function () { msg.textContent = "Could not preview."; })
      .then(function () { setBusy(false); });
  };

  goBtn.onclick = function () {
    if (typeof ensureAudio === "function") ensureAudio();
    setBusy(true);
    bar.style.width = "4%";
    fetch("/api/for-you/batch", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Agent-Id": "dashboard"},
      body: JSON.stringify({topics: lines(), summary: summary.checked, agent_id: "dashboard"})
    }).then(function (r) { return r.json().then(function (j) { return {ok: r.ok, status: r.status, body: j}; }); })
      .then(function (res) {
        if (!res.ok && res.status !== 202) {
          msg.textContent = res.body.error || "Could not start the batch.";
          setBusy(false);
          return;
        }
        msg.textContent = res.body.joined ? "Joined the batch already running." : "Making the batch…";
        return poll(res.body.job_id).then(function () { setBusy(false); loadStatus(); });
      })
      .catch(function () {
        msg.textContent = "Could not start the batch. Is ./run.sh running?";
        setBusy(false);
      });
  };

  loadStatus();
  if (location.hash === "#for-you") {
    var card = document.getElementById("forYou");
    if (card && card.scrollIntoView) card.scrollIntoView();
  }
})();
