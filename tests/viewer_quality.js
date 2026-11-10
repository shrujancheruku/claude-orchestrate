// Runs one of viewer/profile.html's cards (quality by default) against a profile JSON with a stub DOM.
// Usage: node viewer_quality.js <viewer.html> <profile.json> [card function name]
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const profile = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
function node(tag) {
  return { tag, children: [], style: { setProperty() {} }, classList: { add() {}, remove() {} },
    append(...c) { this.children.push(...c); }, replaceChildren(...c) { this.children = c; },
    setAttribute() {}, addEventListener() {}, set textContent(v) { this._t = v; }, get textContent() { return this._t || ""; }, value: "0" };
}
global.document = { createElement: node, createElementNS: (n, t) => node(t), getElementById: () => node("x"), addEventListener() {}, body: node("body") };
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const src = scripts[scripts.length - 1][1].replace(/\nrender\(\);\s*$/, "\n");
const text = (n) => (n._t || "") + (n.children || []).map((x) => (typeof x === "string" ? x : text(x))).join("|");
const card = process.argv[4] || "quality";
const out = new Function(src + "\nreturn " + card + ";")()(profile);
console.log(typeof out === "string" ? out : text(out));
