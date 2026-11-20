import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "./api";
import {
  authProvider,
  identityFromToken,
  initialAccessToken,
  onTokenChanged,
  saveLocalToken,
  signIn,
  signOut,
} from "./auth";

const pendingKey = "flash-ticket-pending";
const bookingKey = "flash-ticket-last-booking";

function readStored(key) {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null");
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

export default function App() {
  const [token, setToken] = useState("");
  const [tokenDraft, setTokenDraft] = useState("");
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [eventsError, setEventsError] = useState("");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  const [selected, setSelected] = useState(null);
  const [quantity, setQuantity] = useState(1);
  const [email, setEmail] = useState("");

  const [attempt, setAttempt] = useState(() => readStored(pendingKey));
  const [booking, setBooking] = useState(() => readStored(bookingKey));
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const busyRef = useRef(false);

  const identity = identityFromToken(token);
  const ownBooking =
    booking?.owner === identity ? booking.data : null;
  const trackedId = ownBooking?.id;
  const trackingComplete =
    ownBooking?.status === "ISSUED" &&
    ownBooking?.email_status === "SENT";

  useEffect(() => {
    let active = true;
    let unsubscribe = () => {};

    try {
      unsubscribe = onTokenChanged((value) => {
        if (active) setToken(value);
      });

      initialAccessToken()
        .then((value) => {
          if (active) setToken(value);
        })
        .catch((failure) => {
          if (active) setError(failure.message);
        });
    } catch (failure) {
      setError(failure.message);
    }

    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    let active = true;
    let timer;

    async function refresh() {
      if (document.visibilityState !== "hidden") {
        setLoading(true);

        try {
          const result = await api("/events");

          if (!Array.isArray(result)) {
            throw new Error("Invalid event list response");
          }

          if (active) {
            setEvents(result);
            setEventsError("");
          }
        } catch {
          if (active) {
            setEventsError("Chưa tải được sự kiện. Hãy thử làm mới.");
          }
        } finally {
          if (active) setLoading(false);
        }
      }

      if (active) {
        timer = setTimeout(refresh, 15000 + Math.random() * 2000);
      }
    }

    refresh();

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [revision]);

  useEffect(() => {
    if (!trackedId || trackingComplete || !token || !identity) return;

    let active = true;
    let timer;
    let polls = 0;

    async function refresh() {
      if (!active || polls >= 60) return;

      if (document.visibilityState === "hidden") {
        timer = setTimeout(refresh, 5000);
        return;
      }

      polls += 1;

      try {
        const data = await api(
          `/bookings/${encodeURIComponent(trackedId)}`,
          { token },
        );

        if (!active) return;

        const next = { owner: identity, data };
        sessionStorage.setItem(bookingKey, JSON.stringify(next));
        setBooking(next);
      } catch (failure) {
        if (!active) return;

        if (failure.status === 401) {
          setToken("");
          setError("Phiên đăng nhập hết hạn. Hãy đăng nhập lại.");
          return;
        }

        if (failure.status === 403 || failure.status === 404) {
          setError(failure.message);
          return;
        }

        setError("Chưa cập nhật được trạng thái vé.");
      }

      if (active) {
        const delay = Math.min(10000, 2000 * 2 ** Math.floor(polls / 5));
        timer = setTimeout(refresh, delay + Math.random() * 1000);
      }
    }

    timer = setTimeout(refresh, 1000);

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [trackedId, trackingComplete, token, identity]);

  function loginLocal(event) {
    event.preventDefault();

    try {
      setToken(saveLocalToken(tokenDraft));
      setTokenDraft("");
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function logout() {
    try {
      await signOut();
      setToken("");
      setSelected(null);
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function submitBooking(existing = null) {
    if (busyRef.current || !identity) return;
    if (!existing && !selected) return;

    const current = existing || {
      key: crypto.randomUUID(),
      owner: identity,
      payload: {
        event_id: selected.id,
        quantity: Number(quantity),
        email: email.trim(),
      },
    };

    if (current.owner !== identity) {
      setError("Hãy đăng nhập đúng tài khoản đã tạo yêu cầu.");
      return;
    }

    try {
      sessionStorage.setItem(pendingKey, JSON.stringify(current));
    } catch {
      setError("Cần cho phép sessionStorage trước khi đặt vé.");
      return;
    }

    setAttempt(current);
    setError("");
    busyRef.current = true;
    setBusy(true);

    try {
      const data = await api("/bookings", {
        method: "POST",
        token,
        body: current.payload,
        idempotencyKey: current.key,
      });

      if (typeof data.id !== "string" || !data.id) {
        throw new Error("Invalid booking response");
      }

      const next = { owner: current.owner, data };

      sessionStorage.setItem(bookingKey, JSON.stringify(next));
      sessionStorage.removeItem(pendingKey);

      setBooking(next);
      setAttempt(null);
      setSelected(null);
      setRevision((value) => value + 1);
    } catch (failure) {
      if (
        failure instanceof ApiError &&
        [400, 403, 404, 409, 422].includes(failure.status)
      ) {
        sessionStorage.removeItem(pendingKey);
        setAttempt(null);
        setError(failure.message);
      } else {
        setError(
          "Chưa xác nhận được kết quả. Hãy thử lại yêu cầu đang chờ.",
        );

        if (failure.status === 401) setToken("");
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function refreshBooking() {
    if (!ownBooking) return;

    try {
      const data = await api(`/bookings/${ownBooking.id}`, { token });
      const next = { owner: identity, data };

      sessionStorage.setItem(bookingKey, JSON.stringify(next));
      setBooking(next);
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function downloadTicket() {
    if (!ownBooking || downloading) return;

    setDownloading(true);
    setError("");

    try {
      const result = await api(
        `/bookings/${ownBooking.id}/ticket`,
        { token },
      );

      window.location.assign(result.url);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <main className="container">
      <header className="header">
        <a className="brand" href="/">FLASH<span>/</span>TICKET</a>
        <span className="badge">DEMO</span>
      </header>

      <section className="hero">
        <p className="eyebrow">LIVE EVENTS</p>
        <h1>Chọn sự kiện.<br />Giữ chỗ cho khoảnh khắc.</h1>
        <p>Đặt vé và nhận vé điện tử có QR ngay trên giao diện.</p>
      </section>

      <section className="panel auth-panel">
        {identity ? (
          <div className="row">
            <span>Đã đăng nhập</span>
            <button className="secondary" onClick={logout}>Đăng xuất</button>
          </div>
        ) : authProvider === "jwt" ? (
          <form className="login-form" onSubmit={loginLocal}>
            <label htmlFor="token">Đăng nhập local bằng access token</label>
            <div className="row">
              <input
                id="token"
                type="password"
                autoComplete="off"
                value={tokenDraft}
                onChange={(event) => setTokenDraft(event.target.value)}
                placeholder="Dán token được cấp từ CLI"
                required
              />
              <button type="submit">Đăng nhập</button>
            </div>
          </form>
        ) : (
          <button
            onClick={async () => {
              try {
                await signIn();
              } catch (failure) {
                setError(failure.message);
              }
            }}
          >
            Đăng nhập với Cognito
          </button>
        )}
      </section>

      {error && <p className="alert" role="alert">{error}</p>}

      <div className="section-heading">
        <h2>Sự kiện đang mở bán</h2>
        <button
          className="secondary"
          disabled={loading}
          onClick={() => setRevision((value) => value + 1)}
        >
          {loading ? "Đang tải..." : "Làm mới"}
        </button>
      </div>

      {eventsError && <p className="alert">{eventsError}</p>}
      {!loading && events.length === 0 && !eventsError && (
        <p>Chưa có sự kiện đang mở bán.</p>
      )}

      <section className="event-grid">
        {events.map((event) => (
          <article className="event-card" key={event.id}>
            <img
              src={event.banner_url}
              alt={event.title}
              loading="lazy"
              decoding="async"
            />
            <div className="event-body">
              <h3>{event.title}</h3>
              <p>{event.description}</p>
              <div className="row">
                <span className="inventory">
                  {event.remaining === null
                    ? "Số vé đang cập nhật"
                    : `Còn ${event.remaining} vé`}
                </span>
                <button
                  disabled={
                    !identity ||
                    event.remaining === 0 ||
                    Boolean(attempt) ||
                    busy
                  }
                  onClick={() => {
                    setSelected(event);
                    setQuantity(1);
                    setError("");
                  }}
                >
                  {event.remaining === 0 ? "Hết vé" : "Đặt vé ngay"}
                </button>
              </div>
            </div>
          </article>
        ))}
      </section>

      {selected && !attempt && (
        <section className="panel">
          <h2>Đặt vé: {selected.title}</h2>
          <form
            className="booking-form"
            onSubmit={(event) => {
              event.preventDefault();
              submitBooking();
            }}
          >
            <label>
              Số lượng
              <input
                type="number"
                min="1"
                max="5"
                value={quantity}
                onChange={(event) => setQuantity(event.target.value)}
                required
              />
            </label>
            <label>
              Email nhận vé
              <input
                type="email"
                autoComplete="email"
                aria-describedby="recipient-help"
                placeholder="Địa chỉ email nhận vé của bạn"
                maxLength="254"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </label>
            <button disabled={busy || !identity} type="submit">
              {busy ? "Đang xử lý..." : "Xác nhận đặt vé"}
            </button>
          </form>
          <p id="recipient-help">
            Vé sẽ được gửi đến địa chỉ bạn nhập ở trên.
            Email đăng nhập không tự thay đổi địa chỉ nhận vé.
          </p>
        </section>
      )}

      {attempt && (
        <section className="panel">
          <h2>Yêu cầu đang chờ xác nhận</h2>
          {attempt.owner === identity ? (
            <>
              <p>Email nhận vé: {attempt.payload.email}</p>
              <p>
                Thử lại sẽ kiểm tra yêu cầu cũ, không tạo đơn mới.
              </p>
              <button
                disabled={busy || !identity}
                onClick={() => submitBooking(attempt)}
              >
                {busy ? "Đang kiểm tra..." : "Thử lại yêu cầu"}
              </button>
            </>
          ) : (
            <p>
              Đăng nhập đúng tài khoản đã tạo yêu cầu để tiếp tục.
            </p>
          )}
        </section>
      )}

      {ownBooking && (
        <section className="panel ticket-panel">
          <p className="eyebrow">ĐƠN GẦN NHẤT TRONG TAB</p>
          <h2>
            {ownBooking.status === "ISSUED"
              ? "Vé của bạn đã sẵn sàng"
              : "Đang phát hành vé"}
          </h2>
          <p>Mã booking: <code>{ownBooking.id}</code></p>
          {ownBooking.email && <p>Email nhận vé: {ownBooking.email}</p>}
          <p>
            Số lượng: {ownBooking.quantity}
            {" · "}
            Email: {ownBooking.email_status === "SENT"
              ? "dịch vụ email đã tiếp nhận"
              : "đang xử lý"}
          </p>
          <div className="row">
            <button
              className="secondary"
              onClick={refreshBooking}
            >
              Cập nhật trạng thái
            </button>
            {ownBooking.status === "ISSUED" && (
              <button
                disabled={downloading}
                onClick={downloadTicket}
              >
                {downloading ? "Đang lấy vé..." : "Tải vé PDF"}
              </button>
            )}
          </div>
        </section>
      )}

      <footer>
        Số vé hiển thị có thể cập nhật chậm. Kết quả đặt vé được
        xác nhận khi hệ thống tạo booking.
      </footer>
    </main>
  );
}
