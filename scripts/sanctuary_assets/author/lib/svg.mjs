/**
 * Constrained SVG builder + linter for the Sanctuary art pipeline.
 *
 * The builder is the ONLY way the generators emit markup, and it enforces
 * the ADR 0012 Skia allowlist at construction time -- an element or
 * attribute outside the table below throws before a byte is written, so
 * the generator is structurally incapable of producing `filter`, `mask`,
 * CSS, `text`, or literal colors. validate.mjs re-lints the committed
 * files with lintSvgSource() so hand-edits can't sneak past either.
 */

import { PALETTE_SLOTS } from "./tokens.mjs";

/** ADR 0012 allowlist: element -> permitted attributes. */
const TAG_ATTRS = {
  svg: ["viewBox", "xmlns"],
  path: ["d", "fill", "fill-opacity", "opacity", "transform"],
  rect: ["x", "y", "width", "height", "rx", "ry", "fill", "fill-opacity", "opacity", "transform"],
  circle: ["cx", "cy", "r", "fill", "fill-opacity", "opacity", "transform"],
  ellipse: ["cx", "cy", "rx", "ry", "fill", "fill-opacity", "opacity", "transform"],
  g: ["fill", "fill-opacity", "opacity", "transform"],
  defs: [],
  linearGradient: ["id", "x1", "y1", "x2", "y2", "gradientUnits"],
  radialGradient: ["id", "cx", "cy", "r", "fx", "fy", "gradientUnits"],
  stop: ["offset", "stop-color", "stop-opacity"],
  use: ["href", "x", "y", "fill", "opacity", "transform"],
};

const NUMBER_RE = /^-?\d+(\.\d+)?$/;
const PAINT_RE = /^(none|\{\{[a-z_]+\}\}|url\(#[A-Za-z0-9_-]+\))$/;
const TOKEN_RE = /^\{\{[a-z_]+\}\}$/;
const PATH_RE = /^[MLHVCSQTAZmlhvcsqtaz0-9,. -]+$/;
const TRANSFORM_RE = /^(translate|scale|rotate)\(-?[0-9., -]*\)( (translate|scale|rotate)\(-?[0-9., -]*\))*$/;
const ID_RE = /^[A-Za-z][A-Za-z0-9_-]*$/;

function slotOfToken(value) {
  const match = /^\{\{([a-z_]+)\}\}$/.exec(value);
  return match ? match[1] : null;
}

function checkAttrValue(tag, name, value, problem) {
  switch (name) {
    case "fill":
      if (!PAINT_RE.test(value)) problem(`fill '${value}' is not none/{{slot}}/url(#id)`);
      break;
    case "stop-color":
      if (!TOKEN_RE.test(value)) problem(`stop-color '${value}' must be a {{slot}} token`);
      break;
    case "d":
      if (!PATH_RE.test(value)) problem(`path data contains characters outside the safe set`);
      break;
    case "transform":
      if (!TRANSFORM_RE.test(value)) problem(`transform '${value}' outside translate/scale/rotate`);
      break;
    case "opacity":
    case "fill-opacity":
    case "stop-opacity":
    case "offset": {
      if (!NUMBER_RE.test(value) || Number(value) < 0 || Number(value) > 1) {
        problem(`${name} '${value}' must be a number in 0..1`);
      }
      break;
    }
    case "id":
      if (!ID_RE.test(value)) problem(`id '${value}' is not a plain identifier`);
      break;
    case "href":
      if (!/^#[A-Za-z][A-Za-z0-9_-]*$/.test(value)) problem(`href '${value}' must be #local`);
      break;
    case "viewBox":
      if (!/^0 0 \d+ \d+$/.test(value)) problem(`viewBox '${value}' must be '0 0 W H'`);
      break;
    case "xmlns":
      if (value !== "http://www.w3.org/2000/svg") problem(`unexpected xmlns '${value}'`);
      break;
    case "gradientUnits":
      if (value !== "userSpaceOnUse") problem(`gradientUnits must be userSpaceOnUse`);
      break;
    default:
      if (!NUMBER_RE.test(value)) problem(`${name} '${value}' must be numeric`);
  }
  const slot = slotOfToken(value);
  if (slot && !PALETTE_SLOTS.includes(slot)) {
    problem(`token '${slot}' is not in the palette-slot vocabulary`);
  }
}

/** Format a coordinate: <= 2 decimals, no trailing zeros, no "-0". */
export function fmt(n) {
  const rounded = Math.round(n * 100) / 100;
  const clean = Object.is(rounded, -0) ? 0 : rounded;
  return String(clean);
}

/** Allowlist-checked element node. Throws on any off-vocabulary markup. */
export function el(tag, attrs = {}, children = []) {
  const allowed = TAG_ATTRS[tag];
  if (!allowed) throw new Error(`element <${tag}> is outside the ADR 0012 allowlist`);
  for (const [name, value] of Object.entries(attrs)) {
    if (!allowed.includes(name)) {
      throw new Error(`<${tag}> attribute '${name}' is outside the allowlist`);
    }
    checkAttrValue(tag, name, String(value), (msg) => {
      throw new Error(`<${tag} ${name}>: ${msg}`);
    });
  }
  return { tag, attrs, children };
}

function renderNode(node) {
  const attrs = Object.entries(node.attrs)
    .map(([name, value]) => ` ${name}="${value}"`)
    .join("");
  if (node.children.length === 0) return `<${node.tag}${attrs}/>`;
  const inner = node.children.map(renderNode).join("");
  return `<${node.tag}${attrs}>${inner}</${node.tag}>`;
}

/**
 * A single SVG document under construction. Gradients are registered
 * through the doc so ids get a per-asset prefix (safe to compose several
 * assets into one preview file) and identical gradients dedupe.
 */
export function createDoc({ width, height, idPrefix }) {
  const defs = [];
  const body = [];
  const gradCache = new Map();

  function registerGradient(node, cacheKey) {
    if (gradCache.has(cacheKey)) return gradCache.get(cacheKey);
    const id = `${idPrefix}g${defs.length}`;
    defs.push({ ...node, attrs: { ...node.attrs, id } });
    const ref = `url(#${id})`;
    gradCache.set(cacheKey, ref);
    return ref;
  }

  return {
    width,
    height,
    add(...nodes) {
      body.push(...nodes);
    },
    /** Vertical linear gradient over [y1, y2]; stops = [slotToken, opacity][]. */
    vGradient(y1, y2, stops) {
      const node = el(
        "linearGradient",
        { x1: 0, y1: fmt(y1), x2: 0, y2: fmt(y2), gradientUnits: "userSpaceOnUse" },
        stops.map(([color, opacity], i) =>
          el("stop", {
            offset: fmt(i / (stops.length - 1)),
            "stop-color": color,
            ...(opacity === undefined ? {} : { "stop-opacity": fmt(opacity) }),
          }),
        ),
      );
      return registerGradient(node, JSON.stringify(["v", y1, y2, stops]));
    },
    /** Radial gradient centered (cx, cy) radius r; stops as vGradient. */
    rGradient(cx, cy, r, stops) {
      const node = el(
        "radialGradient",
        { cx: fmt(cx), cy: fmt(cy), r: fmt(r), gradientUnits: "userSpaceOnUse" },
        stops.map(([color, opacity], i) =>
          el("stop", {
            offset: fmt(i / (stops.length - 1)),
            "stop-color": color,
            ...(opacity === undefined ? {} : { "stop-opacity": fmt(opacity) }),
          }),
        ),
      );
      return registerGradient(node, JSON.stringify(["r", cx, cy, r, stops]));
    },
    render() {
      const root = el("svg", {
        viewBox: `0 0 ${width} ${height}`,
        xmlns: "http://www.w3.org/2000/svg",
      });
      const parts = [];
      if (defs.length > 0) parts.push(renderNode({ tag: "defs", attrs: {}, children: defs }));
      parts.push(...body.map(renderNode));
      const attrs = Object.entries(root.attrs)
        .map(([name, value]) => ` ${name}="${value}"`)
        .join("");
      return `<svg${attrs}>\n${parts.join("\n")}\n</svg>\n`;
    },
  };
}

// ---------------------------------------------------------------------------
// Shared shape vocabulary
// ---------------------------------------------------------------------------

/** Closed Catmull-Rom-smoothed path through the points. */
export function smoothClosed(pts) {
  const n = pts.length;
  let d = `M${fmt(pts[0][0])} ${fmt(pts[0][1])}`;
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n];
    const p1 = pts[i];
    const p2 = pts[(i + 1) % n];
    const p3 = pts[(i + 2) % n];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += `C${fmt(c1[0])} ${fmt(c1[1])} ${fmt(c2[0])} ${fmt(c2[1])} ${fmt(p2[0])} ${fmt(p2[1])}`;
  }
  return `${d}Z`;
}

/** Open smoothed path through the points (clamped ends). */
export function smoothOpen(pts) {
  const n = pts.length;
  let d = `M${fmt(pts[0][0])} ${fmt(pts[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const p0 = pts[Math.max(i - 1, 0)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(i + 2, n - 1)];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += `C${fmt(c1[0])} ${fmt(c1[1])} ${fmt(c2[0])} ${fmt(c2[1])} ${fmt(p2[0])} ${fmt(p2[1])}`;
  }
  return d;
}

/**
 * Organic blob: an ellipse whose ring of control points is wobbled by the
 * seeded rng -- the core painterly primitive for canopies, rocks, bodies.
 */
export function blobPoints(rng, cx, cy, rx, ry, { points = 8, wobble = 0.12, phase = 0 } = {}) {
  const pts = [];
  for (let i = 0; i < points; i++) {
    const a = phase + (i / points) * Math.PI * 2;
    const wr = 1 + (rng() * 2 - 1) * wobble;
    pts.push([cx + Math.cos(a) * rx * wr, cy + Math.sin(a) * ry * wr]);
  }
  return pts;
}

export function blobPath(rng, cx, cy, rx, ry, opts = {}) {
  return smoothClosed(blobPoints(rng, cx, cy, rx, ry, opts));
}

// ---------------------------------------------------------------------------
// Linter (validate.mjs re-checks committed files with this)
// ---------------------------------------------------------------------------

const BANNED_RE = /<\s*(style|filter|mask|text|script|image|foreignObject|clipPath|pattern|symbol|marker|animate|animateTransform|feGaussianBlur)\b/i;

/** Lint an on-disk SVG source against the allowlist. Returns error strings. */
export function lintSvgSource(source, { expectViewBox } = {}) {
  const errors = [];
  const problem = (msg) => errors.push(msg);

  if (BANNED_RE.test(source)) problem("contains a banned element (filter/mask/CSS/text family)");
  if (source.includes("<!--")) problem("contains a comment (generated art must be comment-free)");
  if (/style\s*=/.test(source)) problem("contains a style attribute (no CSS)");

  const ids = new Set();
  const tagRe = /<([A-Za-z][A-Za-z0-9]*)((?:\s+[A-Za-z_][A-Za-z0-9_:-]*="[^"]*")*)\s*\/?>/g;
  let match;
  let sawSvg = false;
  while ((match = tagRe.exec(source)) !== null) {
    const [, tag, attrText] = match;
    const allowed = TAG_ATTRS[tag];
    if (!allowed) {
      problem(`element <${tag}> is outside the ADR 0012 allowlist`);
      continue;
    }
    if (tag === "svg") sawSvg = true;
    const attrRe = /([A-Za-z_][A-Za-z0-9_:-]*)="([^"]*)"/g;
    let attr;
    while ((attr = attrRe.exec(attrText)) !== null) {
      const [, name, value] = attr;
      if (!allowed.includes(name)) {
        problem(`<${tag}> attribute '${name}' is outside the allowlist`);
        continue;
      }
      checkAttrValue(tag, name, value, (msg) => problem(`<${tag} ${name}>: ${msg}`));
      if (name === "id") ids.add(value);
      if (tag === "svg" && name === "viewBox" && expectViewBox && value !== expectViewBox) {
        problem(`viewBox '${value}' != expected '${expectViewBox}'`);
      }
    }
  }
  if (!sawSvg) problem("no <svg> root element found");

  // Any '<' that is not a recognized open or close tag is off-vocabulary.
  const stripped = source
    .replace(/<([A-Za-z][A-Za-z0-9]*)((?:\s+[A-Za-z_][A-Za-z0-9_:-]*="[^"]*")*)\s*\/?>/g, "")
    .replace(/<\/[A-Za-z][A-Za-z0-9]*>/g, "");
  if (stripped.includes("<")) problem("contains markup outside the recognized tag grammar");

  for (const tokenMatch of source.matchAll(/\{\{([a-z_]+)\}\}/g)) {
    if (!PALETTE_SLOTS.includes(tokenMatch[1])) {
      problem(`token '${tokenMatch[1]}' is not in the palette-slot vocabulary`);
    }
  }
  for (const urlMatch of source.matchAll(/url\(#([A-Za-z0-9_-]+)\)/g)) {
    if (!ids.has(urlMatch[1])) problem(`url(#${urlMatch[1]}) has no matching gradient id`);
  }
  return errors;
}
