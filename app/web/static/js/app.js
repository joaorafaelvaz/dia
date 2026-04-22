// DIA dashboard client bootstrap.
// Leaflet map with owner-group color pins and HTMX partial refresh.

(function () {
  function pinColor(ownerGroup) {
    switch ((ownerGroup || "").toLowerCase()) {
      case "gerdau":
        return "#60a5fa";
      case "kinross":
        return "#f59e0b";
      default:
        return "#94a3b8";
    }
  }

  function buildIcon(dam) {
    const color = pinColor(dam.owner);
    return L.divIcon({
      className: "",
      html:
        '<span class="dia-pin" style="display:inline-block;width:14px;height:14px;background:' +
        color +
        ';"></span>',
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    });
  }

  function init() {
    const el = document.getElementById("map");
    if (!el || !window.DIA_DAMS) return;
    if (el._diaInitialized) return;
    el._diaInitialized = true;

    const dams = window.DIA_DAMS.filter(
      (d) => typeof d.lat === "number" && typeof d.lon === "number"
    );

    const map = L.map(el, { zoomControl: true, scrollWheelZoom: false }).setView(
      [-18, -48],
      5
    );

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "© OpenStreetMap",
    }).addTo(map);

    if (dams.length === 0) return;

    const group = L.featureGroup();
    dams.forEach((dam) => {
      const marker = L.marker([dam.lat, dam.lon], { icon: buildIcon(dam) });
      const popup =
        "<div class='text-sm text-slate-900'>" +
        "<div class='font-semibold'>" +
        dam.name +
        "</div>" +
        "<div class='text-xs'>" +
        dam.owner +
        " — " +
        dam.municipality +
        "/" +
        dam.state +
        "</div>" +
        "<a class='text-xs text-sky-600 underline' href='/dams/" +
        dam.id +
        "'>Abrir detalhe</a>" +
        "</div>";
      marker.bindPopup(popup);
      marker.addTo(group);
    });
    group.addTo(map);
    map.fitBounds(group.getBounds().pad(0.2));
  }

  window.initDiaMap = init;
  document.addEventListener("DOMContentLoaded", init);

  // ---------- HTMX toast feedback ----------
  // Fornece retorno visual para botões com hx-post (ex.: "Coletar agora",
  // "Atualizar clima"). Sem isso, hx-swap="none" faz parecer que nada rolou.
  function toast(message, kind) {
    const host = document.getElementById("dia-toasts");
    if (!host) return;
    const palette = {
      ok: "bg-emerald-600 border-emerald-400",
      err: "bg-rose-600 border-rose-400",
      info: "bg-sky-600 border-sky-400",
    };
    const el = document.createElement("div");
    el.className =
      "rounded border px-3 py-2 text-sm text-white shadow transition-opacity duration-300 " +
      (palette[kind] || palette.info);
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 400);
    }, 3500);
  }

  document.body.addEventListener("htmx:afterRequest", function (evt) {
    const xhr = evt.detail.xhr;
    const req = evt.detail.requestConfig || {};
    // Ignorar GETs (partials/counters, alerts) — só interessa POST explícito.
    if ((req.verb || "").toLowerCase() !== "post") return;

    const path = req.path || "";
    if (xhr.status >= 200 && xhr.status < 300) {
      let label = "Tarefa enfileirada";
      try {
        const data = JSON.parse(xhr.responseText || "{}");
        if (data.task && data.task_id) {
          label = data.task + " · id " + String(data.task_id).slice(0, 8);
        }
      } catch (_) {}
      toast("✓ " + label, "ok");
    } else {
      toast(
        "✗ Erro " + xhr.status + " em " + path +
          (xhr.responseText ? " — " + xhr.responseText.slice(0, 120) : ""),
        "err"
      );
    }
  });

  document.body.addEventListener("htmx:sendError", function () {
    toast("✗ Falha de rede", "err");
  });

  document.body.addEventListener("htmx:responseError", function (evt) {
    const xhr = evt.detail.xhr;
    toast("✗ Erro HTTP " + xhr.status, "err");
  });
})();
