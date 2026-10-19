"use strict";
const config = JSON.parse(document.getElementById("routegis-config").textContent);
document.getElementById("project-title").textContent = config.title;
document.title = config.title + " · RouteGIS 웹 지도";
const map = L.map("map", {preferCanvas: true});
const bounds = L.latLngBounds([config.bounds[1], config.bounds[0]], [config.bounds[3], config.bounds[2]]);
const overlays = new Map();
const controls = new Map();
const statusBox = document.getElementById("load-status");
const messageBox = document.getElementById("load-message");
const progressBox = document.getElementById("load-progress");
const layerBox = document.getElementById("layers");
const groups = new Map();
L.control.scale({imperial: false}).addTo(map);
map.fitBounds(bounds.pad(0.08), {maxZoom: 17});
document.getElementById("fit-map").addEventListener("click", () => map.fitBounds(bounds.pad(0.08), {maxZoom: 17}));

function element(tag, text, parent, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    if (parent) parent.appendChild(node);
    return node;
}

function attribution(info) {
    const text = info.attribution || info.publisher || "";
    try {
        const url = new URL(info.license_url);
        if (url.protocol === "https:" || url.protocol === "http:") {
            const link = element("a", text);
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            return link.outerHTML;
        }
    } catch (_) { /* An absent license link still retains plain attribution. */ }
    return element("span", text).outerHTML;
}

function styleFor(entry, feature) {
    const spec = entry.style;
    if (!spec.field) return spec.default;
    const value = feature.properties[spec.field];
    const key = value == null ? "" : String(value);
    const category = spec.categories.find(item => item.value === key);
    return category ? category.style : spec.default;
}

function popup(entry, feature) {
    const content = element("div", undefined, null, "popup-content");
    element("strong", entry.name, content);
    const table = element("table", undefined, content);
    for (const [key, value] of Object.entries(feature.properties || {})) {
        const row = element("tr", undefined, table);
        element("th", key, row);
        element("td", value == null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value), row);
    }
    if (!table.childNodes.length) element("p", "속성 정보가 없습니다.", content);
    return content;
}

function orderLayers() {
    for (const entry of config.layers.filter(item => item.data).sort((a, b) => a.order - b.order)) {
        const layer = overlays.get(entry.id);
        if (layer && map.hasLayer(layer)) layer.bringToFront();
    }
}

for (const entry of config.layers) {
    if (!groups.has(entry.group)) groups.set(entry.group, element("section", undefined, layerBox));
    const group = groups.get(entry.group);
    if (!group.childNodes.length) element("h3", entry.group, group);
    const row = element("div", undefined, group, "layer-row");
    const label = element("label", undefined, row);
    const checkbox = element("input", undefined, label);
    checkbox.type = "checkbox";
    checkbox.dataset.layer = entry.id;
    checkbox.disabled = true;
    const swatch = element("span", undefined, label, "swatch");
    if (entry.style) swatch.style.backgroundColor = entry.style.default.fillColor || entry.style.default.color;
    element("span", entry.name, label);
    element("small", entry.status === "failed" ? "실패" : entry.count ? entry.count.toLocaleString() + "개" : "해당없음", label, "count");
    controls.set(entry.id, checkbox);
    checkbox.addEventListener("change", () => {
        const layer = overlays.get(entry.id);
        if (checkbox.checked) map.addLayer(layer); else map.removeLayer(layer);
        orderLayers();
    });
    if (entry.error) element("p", entry.error, row, "error");
    if (Object.keys(entry.metadata).length) {
        const details = element("details", undefined, row);
        element("summary", "출처 · 라이선스", details);
        element("p", [entry.metadata.publisher, entry.metadata.attribution, entry.metadata.license_name, entry.metadata.license_url].filter(Boolean).join(" · "), details);
    }
}

let background = null;
document.getElementById("basemap").addEventListener("change", event => {
    if (background) map.removeLayer(background);
    background = null;
    const notice = document.getElementById("basemap-status");
    notice.textContent = "";
    if (event.target.value === "none") return;
    const urls = event.target.value === "hybrid" ? [
        "https://xdworld.vworld.kr/2d/Satellite/service/{z}/{x}/{y}.jpeg",
        "https://xdworld.vworld.kr/2d/Hybrid/service/{z}/{x}/{y}.png"
    ] : ["https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png"];
    const tiles = urls.map((url, index) => {
        const layer = L.tileLayer(url, {maxZoom: 18, zIndex: index, attribution: "VWorld / 국토교통부"});
        layer.on("tileerror", () => { notice.textContent = "배경지도 연결 실패 · 인터넷/지도 서비스 이용 조건을 확인하세요. 내장 공간정보는 계속 볼 수 있습니다."; });
        return layer;
    });
    background = L.layerGroup(tiles).addTo(map);
});

async function loadLayers() {
    const entries = config.layers.filter(item => item.data).sort((a, b) => a.order - b.order);
    const total = entries.reduce((sum, entry) => sum + entry.count, 0);
    let done = 0;
    for (const entry of entries) {
        const dataNode = document.getElementById(entry.data);
        const features = JSON.parse(dataNode.textContent).features;
        dataNode.remove(); // Release serialized geometry after parsing each layer.
        const layer = L.geoJSON(null, {
            attribution: attribution(entry.metadata),
            style: feature => styleFor(entry, feature),
            pointToLayer: (feature, latlng) => L.circleMarker(latlng, styleFor(entry, feature)),
            onEachFeature: (feature, item) => item.bindPopup(() => popup(entry, feature))
        });
        overlays.set(entry.id, layer);
        const checkbox = controls.get(entry.id);
        checkbox.checked = !!entry.count;
        checkbox.disabled = !entry.count;
        if (entry.count) layer.addTo(map);
        for (let start = 0; start < features.length; start += 250) {
            const chunk = features.slice(start, start + 250);
            layer.addData({type: "FeatureCollection", features: chunk});
            done += chunk.length;
            messageBox.textContent = entry.name + " 표시 중 · " + done.toLocaleString() + "/" + total.toLocaleString() + "개";
            progressBox.value = total ? 100 * done / total : 100;
            await new Promise(resolve => setTimeout(resolve, 0));
        }
    }
    messageBox.textContent = "지도 준비 완료 · " + done.toLocaleString() + "개 객체 · 레이어를 켜고 끄거나 객체를 클릭해 보세요.";
    progressBox.value = 100;
    statusBox.dataset.state = "ready";
}

setTimeout(() => loadLayers().catch(error => {
    messageBox.textContent = "지도 표시 실패: " + error.message;
    statusBox.dataset.state = "failed";
    console.error(error);
}), 0);
