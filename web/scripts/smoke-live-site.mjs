const baseUrl = (process.env.LANDING_BASE_URL || "https://dragonfly-app.net").replace(/\/+$/, "");

const routes = [
  { path: "/", title: "Dragonfly", canonical: "https://dragonfly-app.net/" },
  { path: "/privacy", title: "Privacy", canonical: "https://dragonfly-app.net/privacy" },
  { path: "/terms", title: "Terms", canonical: "https://dragonfly-app.net/terms" },
  { path: "/support", title: "Support", canonical: "https://dragonfly-app.net/support" },
  { path: "/contact", title: "Contact", canonical: "https://dragonfly-app.net/contact" },
];

const failures = [];
const pages = new Map();
const pilotMailtoSubject = "mailto:support@dragonfly-app.net?subject=Dragonfly%20pilot%20access%20request";
const pilotMailtoFields = [
  "Parent%2Fguardian%20name%3A",
  "Email%3A",
  "Number%20of%20kids%3A",
  "Kids%27%20age%20range%3A",
  "Android%20phone%20available%3F%3A",
  "Are%20you%20willing%20to%20test%20with%20your%20child%20present%3F%3A",
  "Anything%20we%20should%20know%3F%3A",
];

function visibleText(html) {
  return html
    .replace(/<head\b[\s\S]*?<\/head>/gi, " ")
    .replace(/<script\b[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[\s\S]*?<\/style>/gi, " ")
    .replace(/<svg\b[\s\S]*?<\/svg>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function expectIncludes(path, html, text) {
  if (!html.includes(text)) {
    failures.push(`${path} is missing: ${text}`);
  }
}

function expectVisibleAbsent(path, html, pattern, label) {
  if (pattern.test(visibleText(html))) {
    failures.push(`${path} visible text contains ${label}`);
  }
}

async function fetchPage(route) {
  const url = `${baseUrl}${route.path}`;
  let response = await fetch(url, {
    headers: {
      "Cache-Control": "no-cache",
      "User-Agent": "DragonflyLandingSmoke/1.0",
    },
  });

  if (response.status === 404 && route.path !== "/") {
    const fallbackUrl = `${baseUrl}${route.path}.html`;
    response = await fetch(fallbackUrl, {
      headers: {
        "Cache-Control": "no-cache",
        "User-Agent": "DragonflyLandingSmoke/1.0",
      },
    });
  }

  if (response.status !== 200) {
    failures.push(`${url} returned HTTP ${response.status}`);
  }

  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("text/html")) {
    failures.push(`${url} returned unexpected content-type: ${contentType || "(none)"}`);
  }

  const html = await response.text();
  pages.set(route.path, html);
}

await Promise.all(routes.map(fetchPage));

for (const route of routes) {
  const html = pages.get(route.path) || "";
  expectIncludes(route.path, html, "<title>");
  expectIncludes(route.path, html, route.title);
  expectIncludes(route.path, html, `<link rel="canonical" href="${route.canonical}">`);
  expectIncludes(route.path, html, '<meta name="description"');
  expectIncludes(route.path, html, '<meta property="og:image" content="https://dragonfly-app.net/social-card.png">');
  expectVisibleAbsent(route.path, html, /\bTODO\b/i, "TODO");
  expectVisibleAbsent(route.path, html, /\bFIXME\b/i, "FIXME");
  expectVisibleAbsent(route.path, html, /\bPLACEHOLDER\b/i, "PLACEHOLDER");
}

const home = pages.get("/") || "";
expectIncludes("/", home, "Turn backyard curiosity into real science.");
expectIncludes("/", home, '"@type": "Organization"');
expectIncludes("/", home, '"url": "https://dragonfly-app.net/"');
expectIncludes("/", home, pilotMailtoSubject);
expectIncludes("/", home, "Please do not include your child&rsquo;s full name in this request.");
expectIncludes("/", home, "Known families only during this Internal Testing phase.");

for (const field of pilotMailtoFields) {
  expectIncludes("/", home, field);
}

for (const [path, email] of [
  ["/", "support@dragonfly-app.net"],
  ["/", "privacy@dragonfly-app.net"],
  ["/privacy", "privacy@dragonfly-app.net"],
  ["/support", "support@dragonfly-app.net"],
  ["/contact", "support@dragonfly-app.net"],
  ["/contact", "privacy@dragonfly-app.net"],
]) {
  expectIncludes(path, pages.get(path) || "", email);
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log(`Landing live smoke passed for ${baseUrl}`);
