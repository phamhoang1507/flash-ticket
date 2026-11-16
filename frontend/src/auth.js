import {
  UserManager,
  WebStorageStateStore,
} from "oidc-client-ts";

export const authProvider =
  import.meta.env.VITE_AUTH_PROVIDER || "jwt";

const tokenKey = "flash-ticket-access-token";
let manager;
let callbackPromise;

function claims(token) {
  try {
    const segment = token.split(".")[1]
      .replace(/-/g, "+")
      .replace(/_/g, "/");

    const padded = segment.padEnd(
      segment.length + ((4 - segment.length % 4) % 4),
      "=",
    );

    const bytes = Uint8Array.from(
      atob(padded),
      (character) => character.charCodeAt(0),
    );

    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return {};
  }
}

export function identityFromToken(token) {
  const value = claims(token);

  if (
    typeof value.iss !== "string" ||
    typeof value.sub !== "string"
  ) {
    return "";
  }

  return JSON.stringify([value.iss, value.sub]);
}

function getManager() {
  if (manager) return manager;

  const authority =
    import.meta.env.VITE_COGNITO_AUTHORITY;
  const clientId =
    import.meta.env.VITE_COGNITO_CLIENT_ID;
  const domain =
    import.meta.env.VITE_COGNITO_DOMAIN;

  if (!authority || !clientId || !domain) {
    throw new Error("Thiếu cấu hình Cognito khi build frontend.");
  }

  const base = domain.replace(/\/+$/, "");

  manager = new UserManager({
    authority,
    client_id: clientId,
    redirect_uri: `${window.location.origin}/auth/callback`,
    post_logout_redirect_uri: `${window.location.origin}/`,
    response_type: "code",
    scope:
      import.meta.env.VITE_OIDC_SCOPE ||
      "openid email profile flash-ticket/bookings:write",
    userStore: new WebStorageStateStore({
      store: window.sessionStorage,
    }),
    stateStore: new WebStorageStateStore({
      store: window.sessionStorage,
    }),
    automaticSilentRenew: true,
    loadUserInfo: false,
    metadata: {
      issuer: authority,
      authorization_endpoint: `${base}/oauth2/authorize`,
      token_endpoint: `${base}/oauth2/token`,
      userinfo_endpoint: `${base}/oauth2/userInfo`,
      jwks_uri: `${authority.replace(/\/+$/, "")}/.well-known/jwks.json`,
    },
  });

  return manager;
}

export async function initialAccessToken() {
  if (authProvider === "jwt") {
    const token = sessionStorage.getItem(tokenKey) || "";
    const expiry = claims(token).exp;

    if (!identityFromToken(token) || expiry * 1000 <= Date.now()) {
      sessionStorage.removeItem(tokenKey);
      return "";
    }

    return token;
  }

  if (authProvider !== "cognito") {
    throw new Error("AUTH_PROVIDER chỉ nhận jwt hoặc cognito.");
  }

  const oidc = getManager();

  if (window.location.pathname === "/auth/callback") {
    callbackPromise ||= oidc.signinRedirectCallback();

    try {
      return (await callbackPromise).access_token;
    } finally {
      window.history.replaceState({}, "", "/");
    }
  }

  const user = await oidc.getUser();

  if (!user) return "";
  if (!user.expired) return user.access_token;

  if (user.refresh_token) {
    return (await oidc.signinSilent())?.access_token || "";
  }

  await oidc.removeUser();
  return "";
}

export function onTokenChanged(callback) {
  if (authProvider !== "cognito") return () => {};

  const oidc = getManager();
  const loaded = (user) => callback(user.access_token);
  const cleared = () => callback("");

  oidc.events.addUserLoaded(loaded);
  oidc.events.addUserUnloaded(cleared);
  oidc.events.addAccessTokenExpired(cleared);

  return () => {
    oidc.events.removeUserLoaded(loaded);
    oidc.events.removeUserUnloaded(cleared);
    oidc.events.removeAccessTokenExpired(cleared);
  };
}

export function saveLocalToken(value) {
  const token = value.trim().replace(/^Bearer\s+/i, "");
  const expiry = claims(token).exp;

  if (
    !identityFromToken(token) ||
    typeof expiry !== "number" ||
    expiry * 1000 <= Date.now()
  ) {
    throw new Error("Token không hợp lệ hoặc đã hết hạn.");
  }

  sessionStorage.setItem(tokenKey, token);
  return token;
}

export async function signIn() {
  await getManager().signinRedirect();
}

export async function signOut() {
  if (authProvider === "jwt") {
    sessionStorage.removeItem(tokenKey);
    return;
  }

  await getManager().removeUser();

  const domain =
    import.meta.env.VITE_COGNITO_DOMAIN.replace(/\/+$/, "");
  const url = new URL(`${domain}/logout`);

  url.searchParams.set(
    "client_id",
    import.meta.env.VITE_COGNITO_CLIENT_ID,
  );
  url.searchParams.set(
    "logout_uri",
    `${window.location.origin}/`,
  );

  window.location.assign(url);
}