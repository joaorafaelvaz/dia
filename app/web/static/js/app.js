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
})();
