export const API_URL = "/api/proxy";

export async function cloudoFetch(url: string, options: RequestInit = {}) {
  let sessionToken = null;
  if (typeof window !== "undefined") {
    const expiresAt = localStorage.getItem("cloudo_expires_at");
    const auth = localStorage.getItem("cloudo_auth");
    sessionToken = localStorage.getItem("cloudo_token");

    if (auth === "true" && !expiresAt) {
      localStorage.removeItem("cloudo_auth");
      localStorage.removeItem("cloudo_user");
      localStorage.removeItem("cloudo_expires_at");
      localStorage.removeItem("cloudo_token");
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
      throw new Error("Security protocol violated: Session expiration missing");
    }

    if (expiresAt) {
      const now = new Date();
      const expirationDate = new Date(expiresAt);
      if (now >= expirationDate) {
        // Session expired
        localStorage.removeItem("cloudo_auth");
        localStorage.removeItem("cloudo_user");
        localStorage.removeItem("cloudo_expires_at");
        localStorage.removeItem("cloudo_token");
        // Force redirect to login if not already there
        if (window.location.pathname !== "/login") {
          window.location.href = "/login";
        }
        throw new Error("Session expired");
      }
    }
  }

  const headers: Record<string, string> = {
    ...Object.fromEntries(Object.entries(options.headers || {})),
  };

  const storedUser =
    typeof window !== "undefined" ? localStorage.getItem("cloudo_user") : null;
  const showAllTeams =
    typeof window !== "undefined" &&
    localStorage.getItem("cloudo_include_all_teams") === "true" &&
    (() => {
      try {
        return JSON.parse(storedUser || "null")?.role === "ADMIN";
      } catch {
        return false;
      }
    })();

  if (sessionToken && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${sessionToken}`;
  }

  const urlObj = new URL(url, "http://localhost");
  const targetPath = urlObj.pathname;
  const proxyUrl = `${API_URL}?path=${encodeURIComponent(targetPath)}`;

  let finalProxyUrl = proxyUrl;
  const queryString = urlObj.search.startsWith("?")
    ? urlObj.search.substring(1)
    : urlObj.search;
  if (queryString) {
    finalProxyUrl += `&${queryString}`;
  }
  if (showAllTeams) {
    finalProxyUrl += "&includeAllTeams=true";
  }

  return fetch(finalProxyUrl, {
    ...options,
    headers,
  });
}
