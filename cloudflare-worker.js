
const SERVICE_NAME = "rozhanglobal-social-deep-backup";

const MAX_DAILY_BROWSER_ACTIONS = 4;
const MIN_ACTION_INTERVAL_MS = 11_000;
const MAX_MARKDOWN_CHARS = 20_000;
const MAX_ITEMS = 3;

const ALLOWED_HOSTS = new Set([
  "linkedin.com",
  "www.linkedin.com",
  "facebook.com",
  "www.facebook.com",
  "m.facebook.com",
  "web.facebook.com",
]);

const PLATFORM_HOSTS = {
  linkedin: new Set(["linkedin.com", "www.linkedin.com"]),
  facebook: new Set([
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "web.facebook.com",
  ]),
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET, POST, OPTIONS",
      "access-control-allow-headers": "Content-Type, Authorization",
    },
  });
}

function normalizePlatform(value) {
  const platform = String(value || "").trim().toLowerCase();

  if (platform === "linkedin" || platform === "facebook") {
    return platform;
  }

  if (platform === "both" || platform === "auto") {
    return "both";
  }

  return null;
}

function validateSocialUrl(rawUrl, requiredPlatform = null) {
  let url;

  try {
    url = new URL(String(rawUrl || ""));
  } catch {
    throw new Error("A valid social profile URL is required.");
  }

  if (url.protocol !== "https:") {
    throw new Error("Only HTTPS URLs are allowed.");
  }

  const hostname = url.hostname.toLowerCase();

  if (!ALLOWED_HOSTS.has(hostname)) {
    throw new Error("Only LinkedIn and Facebook URLs are allowed.");
  }

  if (
    requiredPlatform &&
    !PLATFORM_HOSTS[requiredPlatform]?.has(hostname)
  ) {
    throw new Error(`The URL is not a ${requiredPlatform} URL.`);
  }

  url.hash = "";

  return url.toString();
}

function platformFromUrl(rawUrl) {
  try {
    const hostname = new URL(rawUrl).hostname.toLowerCase();

    if (PLATFORM_HOSTS.linkedin.has(hostname)) return "linkedin";
    if (PLATFORM_HOSTS.facebook.has(hostname)) return "facebook";
  } catch {
    // Ignore malformed URLs.
  }

  return null;
}

function cleanText(value) {
  return String(value || "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/[`*_#>|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function extractUrls(markdown) {
  const urls = new Set();
  const text = String(markdown || "");

  const markdownLinkRegex = /\[[^\]]*\]\((https?:\/\/[^)\s]+)\)/gi;
  const rawUrlRegex = /https?:\/\/[^\s<>"')\]]+/gi;

  for (const match of text.matchAll(markdownLinkRegex)) {
    urls.add(match[1].replace(/[.,;]+$/, ""));
  }

  for (const match of text.matchAll(rawUrlRegex)) {
    urls.add(match[0].replace(/[.,;]+$/, ""));
  }

  return [...urls];
}

function isSearchOrUtilityPath(pathname) {
  const path = pathname.toLowerCase();

  return [
    "/search",
    "/login",
    "/signup",
    "/checkpoint",
    "/authwall",
    "/help",
    "/privacy",
    "/legal",
    "/about",
    "/settings",
    "/notifications",
    "/messaging",
    "/feed",
    "/jobs",
  ].some((item) => path === item || path.startsWith(`${item}/`));
}

function extractCandidateUrls(markdown, limit = MAX_ITEMS) {
  const candidates = new Set();

  for (const rawUrl of extractUrls(markdown)) {
    try {
      const url = new URL(rawUrl);
      const platform = platformFromUrl(url.toString());

      if (!platform) continue;
      if (isSearchOrUtilityPath(url.pathname)) continue;

      if (platform === "linkedin") {
        const validPath = /^\/company\/[^/]+/i.test(url.pathname);

        if (!validPath) continue;
      }

      if (platform === "facebook") {
        const validPath =
          /^\/(profile\.php|pages\/|people\/|[^/]+\/?$)/i.test(
            url.pathname
          );

        if (!validPath) continue;
      }

      url.hash = "";
      url.searchParams.delete("trackingId");
      url.searchParams.delete("trk");

      candidates.add(url.toString());

      if (candidates.size >= limit) break;
    } catch {
      // Skip malformed URLs.
    }
  }

  return [...candidates];
}

function extractProfile(markdown, targetUrl) {
  const content = String(markdown || "").slice(0, MAX_MARKDOWN_CHARS);
  const lines = content
    .split(/\r?\n/)
    .map(cleanText)
    .filter(Boolean);

  const ignoredLines = [
    /^linkedin$/i,
    /^facebook$/i,
    /^log in$/i,
    /^sign in$/i,
    /^sign up$/i,
    /^join now$/i,
    /^see more$/i,
    /^learn more$/i,
    /^home$/i,
    /^search$/i,
  ];

  const usefulLines = lines.filter(
    (line) =>
      line.length > 1 &&
      line.length < 240 &&
      !ignoredLines.some((pattern) => pattern.test(line))
  );

  const emailMatch = content.match(
    /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i
  );

  const phoneMatch = content.match(
    /(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d[\d\s().-]{6,}\d/
  );

  const websiteMatch = content.match(
    /https?:\/\/(?:www\.)?(?!linkedin\.com|facebook\.com)[a-z0-9.-]+\.[a-z]{2,}(?:\/[^\s<>"')\]]*)?/i
  );

  const locationLine = usefulLines.find((line) =>
    /\b(headquarters|based in|located in|location|china|guangdong|shanghai|beijing|jiangsu|zhejiang)\b/i.test(
      line
    )
  );

  const descriptionLine = usefulLines.find(
    (line) =>
      line.length >= 45 &&
      /\b(manufacturer|manufacturing|factory|industrial|lubricant|lubricating|grease|hydraulic|gear oil|compressor oil|producer|company|products|企业|制造|润滑油|润滑脂)\b/i.test(
        line
      )
  );

  const followerLine = usefulLines.find((line) =>
    /\b(followers|connections|employees)\b/i.test(line)
  );

  const verifiedLine = usefulLines.some((line) =>
    /\bverified\b/i.test(line)
  );

  return {
    name: usefulLines[0] || "",
    website: websiteMatch ? websiteMatch[0] : "",
    location: locationLine || "",
    email: emailMatch ? emailMatch[0] : "",
    phone: phoneMatch ? phoneMatch[0].trim() : "",
    whatsapp: "",
    description: descriptionLine || usefulLines.slice(1, 4).join(" "),
    followers: followerLine || "",
    verified: verifiedLine,
    url: targetUrl,
    platform: platformFromUrl(targetUrl) || "",
  };
}

async function runMarkdownAction(env, targetUrl) {
  if (!env.BROWSER || typeof env.BROWSER.quickAction !== "function") {
    throw new Error(
      "Browser Run binding is missing or does not expose quickAction()."
    );
  }

  const result = await env.BROWSER.quickAction("markdown", {
    url: targetUrl,
  });

  let markdown = "";

  if (typeof result === "string") {
    markdown = result;
  } else if (result && typeof result === "object") {
    markdown =
      result.markdown ||
      result.content ||
      result.text ||
      result.result ||
      result.data?.markdown ||
      result.data?.content ||
      "";
  }

  if (!markdown) {
    markdown = JSON.stringify(result ?? {});
  }

  return String(markdown).slice(0, MAX_MARKDOWN_CHARS);
}

function utcDayKey() {
  return new Date().toISOString().slice(0, 10);
}

async function reserveBrowserAction(env) {
  if (!env.SOCIAL_LIMITS) {
    throw new Error("SOCIAL_LIMITS KV binding is missing.");
  }

  const day = utcDayKey();
  const countKey = `browser-count:${day}`;
  const lastKey = "browser-last-action";

  const count = Number((await env.SOCIAL_LIMITS.get(countKey)) || "0");

  if (count >= MAX_DAILY_BROWSER_ACTIONS) {
    const error = new Error("Daily Browser Run action limit reached.");
    error.status = 429;
    throw error;
  }

  const lastAction = Number(
    (await env.SOCIAL_LIMITS.get(lastKey)) || "0"
  );

  const waitMs = Math.max(
    0,
    MIN_ACTION_INTERVAL_MS - (Date.now() - lastAction)
  );

  if (waitMs > 0) {
    await new Promise((resolve) => setTimeout(resolve, waitMs));
  }

  // Recheck after waiting. This is a budget guard, not an atomic lock.
  const latestCount = Number(
    (await env.SOCIAL_LIMITS.get(countKey)) || "0"
  );

  if (latestCount >= MAX_DAILY_BROWSER_ACTIONS) {
    const error = new Error("Daily Browser Run action limit reached.");
    error.status = 429;
    throw error;
  }

  await env.SOCIAL_LIMITS.put(countKey, String(latestCount + 1), {
    expirationTtl: 60 * 60 * 48,
  });

  await env.SOCIAL_LIMITS.put(lastKey, String(Date.now()), {
    expirationTtl: 60 * 60 * 24,
  });

  return {
    usedToday: latestCount + 1,
    dailyLimit: MAX_DAILY_BROWSER_ACTIONS,
  };
}

function tokenIsValid(request, env) {
  const expected = env.SOCIAL_DEEP_BACKUP_TOKEN;

  if (!expected) return false;

  const authorization = request.headers.get("Authorization") || "";

  return authorization === `Bearer ${expected}`;
}

function bindingsReady(env) {
  return Boolean(
    env.BROWSER &&
    typeof env.BROWSER.quickAction === "function" &&
    env.SOCIAL_LIMITS &&
    env.SOCIAL_DEEP_BACKUP_TOKEN
  );
}

async function handleSearch(request, env) {
  let body;

  try {
    body = await request.json();
  } catch {
    return json({ success: false, error: "Request body must be JSON." }, 400);
  }

  const query = String(body.query || "").trim();

  if (!query) {
    return json({ success: false, error: "query is required." }, 400);
  }

  const platform = normalizePlatform(body.platform || "both");

  if (!platform) {
    return json(
      {
        success: false,
        error: "platform must be linkedin, facebook, or both.",
      },
      400
    );
  }

  const platforms =
    platform === "both" ? ["linkedin", "facebook"] : [platform];

  const results = [];

  for (const currentPlatform of platforms) {
    const searchUrl =
      currentPlatform === "linkedin"
        ? `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(query)}`
        : `https://www.facebook.com/search/pages/?q=${encodeURIComponent(query)}`;

    try {
      const quota = await reserveBrowserAction(env);
      const markdown = await runMarkdownAction(env, searchUrl);
      const candidates = extractCandidateUrls(markdown, MAX_ITEMS);

      results.push({
        platform: currentPlatform,
        search_url: searchUrl,
        candidates,
        candidate_count: candidates.length,
        quota,
      });
    } catch (error) {
      results.push({
        platform: currentPlatform,
        search_url: searchUrl,
        candidates: [],
        error: error.message || "Search failed.",
        status: error.status || 500,
      });

      if (error.status === 429) break;
    }
  }

  return json({
    success: results.some((item) => !item.error),
    service: SERVICE_NAME,
    query,
    platforms,
    results,
  });
}

async function handleCollect(request, env) {
  let body;

  try {
    body = await request.json();
  } catch {
    return json({ success: false, error: "Request body must be JSON." }, 400);
  }

  let targetUrl;

  try {
    targetUrl = validateSocialUrl(body.url);
  } catch (error) {
    return json({ success: false, error: error.message }, 400);
  }

  const platform = platformFromUrl(targetUrl);

  if (!platform) {
    return json(
      { success: false, error: "Only LinkedIn and Facebook are supported." },
      400
    );
  }

  try {
    const quota = await reserveBrowserAction(env);
    const markdown = await runMarkdownAction(env, targetUrl);
    const profile = extractProfile(markdown, targetUrl);

    return json({
      success: true,
      service: SERVICE_NAME,
      platform,
      url: targetUrl,
      profile,
      content: markdown,
      quota,
    });
  } catch (error) {
    return json(
      {
        success: false,
        service: SERVICE_NAME,
        platform,
        url: targetUrl,
        error: error.message || "Profile collection failed.",
      },
      error.status || 500
    );
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET, POST, OPTIONS",
          "access-control-allow-headers": "Content-Type, Authorization",
        },
      });
    }

    if (url.pathname === "/health" && request.method === "GET") {
      return json({
        success: true,
        service: SERVICE_NAME,
        status: bindingsReady(env) ? "ready" : "missing_bindings",
        platforms: ["linkedin", "facebook"],
        token_configured: Boolean(env.SOCIAL_DEEP_BACKUP_TOKEN),
        browser_binding_configured: Boolean(
          env.BROWSER &&
          typeof env.BROWSER.quickAction === "function"
        ),
        kv_binding_configured: Boolean(env.SOCIAL_LIMITS),
        daily_browser_action_limit: MAX_DAILY_BROWSER_ACTIONS,
        minimum_interval_ms: MIN_ACTION_INTERVAL_MS,
      });
    }

    if (request.method !== "POST") {
      return json({ success: false, error: "Method not allowed." }, 405);
    }

    if (!tokenIsValid(request, env)) {
      return json(
        { success: false, error: "Unauthorized. Check the Bearer token." },
        401
      );
    }

    if (!bindingsReady(env)) {
      return json(
        {
          success: false,
          error:
            "Required Worker bindings are missing. Check BROWSER, SOCIAL_LIMITS, and SOCIAL_DEEP_BACKUP_TOKEN.",
        },
        503
      );
    }

    if (url.pathname === "/search") {
      return handleSearch(request, env);
    }

    if (url.pathname === "/collect") {
      return handleCollect(request, env);
    }

    return json({ success: false, error: "Not found." }, 404);
  },
};
