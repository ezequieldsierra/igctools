const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = {window: {}, frappe: {utils: {escape_html: String}, ui: {form: {on() {}}}}, URL};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, 'client.js'), 'utf8'), context);
const normalize = context.window.IGCEmployeeIDPhoto.standardizeShirt;
function sample(base) {
  const width = 180, height = 240, data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4;
    let rgb = [255, 255, 255];
    if (y > 150) rgb = base.map((v) => Math.round(v * (0.8 + 0.4 * (x / width))));
    if (x > 60 && x < 120 && y > 145 && y < 185) rgb = [170, 118, 89]; // neck
    if (x > 25 && x < 40 && y > 70 && y < 220) rgb = [19, 18, 17]; // long dark hair
    data.set([...rgb, 255], i);
  }
  return {data, width, height};
}
for (const shade of [[15, 29, 48], [31, 47, 72], [37, 57, 104]]) {
  const image = sample(shade), before = new Uint8ClampedArray(image.data);
  const result = normalize(image, 170);
  assert.deepEqual(Array.from(result.median_rgb), [22, 38, 63]);
  for (const [x, y] of [[90, 165], [32, 205], [90, 30]]) {
    const i = (y * image.width + x) * 4;
    assert.deepEqual(image.data.slice(i, i + 4), before.slice(i, i + 4), 'skin, hair and background must remain unchanged');
  }
  assert.ok(result.garment_coverage > 0.2);
}
assert.throws(() => normalize(sample([120, 25, 25]), 170), /polo azul marino/);
console.log('Color tests passed: three navy variants converge to RGB 22,38,63; skin/hair/background preserved; red shirt rejected.');
