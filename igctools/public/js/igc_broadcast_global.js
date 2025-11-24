(function () {
  if (window.__IGC_BCAST_INIT__) return;
  window.__IGC_BCAST_INIT__ = true;

  var state = {
    overlay: null,
    open: false,
    cssInjected: false,
    currentRowName: null,
    pollTimer: null
  };

  function inject_css_once() {
    if (state.cssInjected) return;
    state.cssInjected = true;

    var style = document.createElement("style");
    style.type = "text/css";
    style.innerHTML = [
      ".igc-bcast-overlay{position:fixed;inset:0;z-index:999999;background:rgba(2,15,32,0.96);display:flex;align-items:center;justify-content:center;font-family:system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;}",
      ".igc-bcast-backdrop{position:absolute;inset:0;}",
      ".igc-bcast-card{position:relative;max-width:780px;width:100%;margin:0 18px;background:#0b1120;border-radius:20px;padding:28px 28px 24px 28px;box-shadow:0 26px 80px rgba(0,0,0,0.7);border:1px solid rgba(148,163,184,0.32);color:#e5e7eb;display:flex;flex-direction:column;gap:16px;}",
      ".igc-bcast-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:4px;}",
      ".igc-bcast-title{font-size:24px;font-weight:600;letter-spacing:0.03em;color:#f9fafb;}",
      ".igc-bcast-pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;background:rgba(56,189,248,0.15);color:#7dd3fc;font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:0.14em;}",
      ".igc-bcast-dot{width:8px;height:8px;border-radius:999px;background:#22c55e;box-shadow:0 0 0 6px rgba(34,197,94,0.22);}",
      ".igc-bcast-close{margin-left:auto;border:none;background:transparent;color:#9ca3af;font-size:22px;line-height:1;cursor:pointer;border-radius:999px;padding:4px 10px;transition:background 0.15s ease,color 0.15s ease,transform 0.15s ease;}",
      ".igc-bcast-close:hover{background:rgba(148,163,184,0.18);color:#f9fafb;transform:translateY(-1px);}",
      ".igc-bcast-body{font-size:15px;line-height:1.6;color:#e5e7eb;max-height:60vh;overflow:auto;padding-right:4px;}",
      ".igc-bcast-body h1,.igc-bcast-body h2,.igc-bcast-body h3{color:#f9fafb;margin-top:0;margin-bottom:8px;}",
      ".igc-bcast-body p{margin:0 0 8px;}",
      ".igc-bcast-footer{display:flex;justify-content:flex-end;margin-top:4px;gap:8px;}",
      ".igc-bcast-ack{border:none;outline:none;border-radius:999px;padding:9px 20px;font-size:13px;font-weight:500;cursor:pointer;background:#f97316;color:#111827;box-shadow:0 12px 36px rgba(248,113,22,0.45);transition:transform 0.12s ease,box-shadow 0.12s ease,background 0.15s ease;}",
      ".igc-bcast-ack:hover{transform:translateY(-1px);box-shadow:0 16px 50px rgba(248,113,22,0.6);background:#fb923c;}",
      ".igc-bcast-ack:active{transform:translateY(0);box-shadow:0 10px 28px rgba(248,113,22,0.45);}",
      ".igc-bcast-body::-webkit-scrollbar{width:6px;}",
      ".igc-bcast-body::-webkit-scrollbar-track{background:transparent;}",
      ".igc-bcast-body::-webkit-scrollbar-thumb{background:rgba(148,163,184,0.75);border-radius:999px;}"
    ].join("");
    document.head.appendChild(style);
  }

  function play_alert_sound() {
    try {
      var audio = new Audio("https://actions.google.com/sounds/v1/alarms/alarm_clock.ogg");
      audio.play().catch(function () {});
    } catch (e) {}
  }

  function close_overlay() {
    if (!state.overlay) return;
    state.overlay.remove();
    state.overlay = null;
    state.open = false;
    state.currentRowName = null;
  }

  function mark_read_and_close() {
    var rowname = state.currentRowName;
    close_overlay();
    if (!rowname) return;

    frappe.call({
      method: "igc_mark_broadcast_read",
      args: { rowname: rowname }
    });
  }

  function show_overlay(data) {
    inject_css_once();
    close_overlay();

    var overlay = document.createElement("div");
    overlay.className = "igc-bcast-overlay";
    overlay.innerHTML = [
      '<div class="igc-bcast-backdrop"></div>',
      '<div class="igc-bcast-card">',
        '<div class="igc-bcast-header">',
          '<div>',
            '<div class="igc-bcast-pill">',
              '<span class="igc-bcast-dot"></span>',
              '<span>Broadcast</span>',
            '</div>',
            '<div class="igc-bcast-title">' + frappe.utils.escape_html(data.title || "Mensaje") + '</div>',
          '</div>',
          '<button type="button" class="igc-bcast-close" aria-label="Cerrar">&times;</button>',
        '</div>',
        '<div class="igc-bcast-body">' + (data.html || "") + '</div>',
        '<div class="igc-bcast-footer">',
          '<button type="button" class="igc-bcast-ack">Entendido</button>',
        '</div>',
      '</div>'
    ].join("");

    document.body.appendChild(overlay);
    state.overlay = overlay;
    state.open = true;
    state.currentRowName = data.rowname || null;

    var closeBtn = overlay.querySelector(".igc-bcast-close");
    var ackBtn = overlay.querySelector(".igc-bcast-ack");
    var backdrop = overlay.querySelector(".igc-bcast-backdrop");

    function on_close() {
      mark_read_and_close();
    }

    if (closeBtn) closeBtn.addEventListener("click", on_close);
    if (ackBtn) addEventListener("click", on_close);
    if (backdrop) backdrop.addEventListener("click", on_close);

    document.addEventListener("keydown", function escHandler(e) {
      if (e.key === "Escape" && state.open) {
        e.preventDefault();
        document.removeEventListener("keydown", escHandler);
        on_close();
      }
    });

    play_alert_sound();
  }

  function poll_broadcast_once() {
    if (state.open) return;

    frappe.call({
      method: "igc_broadcast_pull",
      callback: function (r) {
        if (!r || !r.message) return;
        show_overlay(r.message);
      }
    });
  }

  if (!state.pollTimer) {
    poll_broadcast_once();
    state.pollTimer = setInterval(function () {
      poll_broadcast_once();
    }, 5000);
  }
})();
