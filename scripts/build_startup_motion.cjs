/* Build-time only. Render the approved SVG into a small PNG atlas with sharp.
 * Usage: node scripts/build_startup_motion.cjs [absolute path to sharp]
 * No image/video decoder or new Python dependency is needed at app startup.
 */
const fs = require('node:fs');
const path = require('node:path');
const sharp = require(process.argv[2] || 'sharp');
const root = path.resolve(__dirname, '..');
const original = fs.readFileSync(path.join(root, 'assets/videocatch.svg'), 'utf8');
const size = 96, columns = 10, intro = 24, loop = 64, fps = 25;
const ease = t => 1 - Math.pow(1 - Math.max(0, Math.min(1, t)), 3);

function svgFrame(index) {
  const entering = index < intro;
  const t = entering ? index / (intro - 1) : (index - intro) / loop;
  const angle = t * Math.PI * 2;
  const rise = entering ? 30 * (1 - ease(t)) : -9 * (1 - Math.cos(angle)) / 2;
  const opacity = entering ? .6 + .4 * ease(t) : 1;
  let svg = original.replace('width="1024" height="1024"', 'width="96" height="96"');
  // Keep the original paths, gradients and layer order, including the dark cut.
  svg = svg.replace(/<circle\b[^>]*\/>/g, node => `<g transform="translate(0 ${rise})" opacity="${opacity}">${node}</g>`);
  svg = svg.replace(/<path d="M175[^>]*\/>/, node => `<g transform="translate(0 ${rise})" opacity="${opacity}">${node}</g>`);
  ['M115', 'M154', 'M198'].forEach((prefix, layer) => {
    const p = entering ? ease((t - layer * .12) / .7) : 1;
    const x = entering ? -52 * (1 - p) : (14 - layer * 3) * Math.sin(angle);
    const y = -x * .166;
    svg = svg.replace(new RegExp(`<path d="${prefix}[^>]*\\/>`),
      node => `<g transform="translate(${x} ${y})" opacity="${.3 + .7 * p}">${node}</g>`);
  });
  return svg;
}

(async () => {
  const count = intro + loop;
  const composites = [];
  for (let i = 0; i < count; i++) {
    const input = await sharp(Buffer.from(svgFrame(i)), {density: 288})
      .resize(size, size).png().toBuffer();
    composites.push({input, left: (i % columns) * size, top: Math.floor(i / columns) * size});
  }
  const output = path.join(root, 'assets/startup-motion.png');
  await sharp({create: {width: columns * size, height: Math.ceil(count / columns) * size,
    channels: 4, background: '#00000000'}}).composite(composites).png().toFile(output);
  console.log(JSON.stringify({output, bytes: fs.statSync(output).size, size, columns, intro, loop, fps}));
})().catch(error => {console.error(error); process.exitCode = 1;});
