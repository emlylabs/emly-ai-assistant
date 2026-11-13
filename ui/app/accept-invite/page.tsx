"use client";

/**
 * /accept-invite — landing page for invitation emails.
 *
 * Two modes:
 *  1. `?token=…` — the user clicked the email link. We fetch the public
 *     by-token projection so the card can render with the space name +
 *     inviter *before* forcing login. If `/auth/me` is 401, we redirect
 *     into the OIDC login flow with this URL as `return_to`. Once
 *     authenticated, the user clicks Accept or Decline.
 *  2. No `?token=` — picker mode. After post-login redirect for users
 *     with pending invites, we render a list of cards (one per invite)
 *     plus a "stay with default" tile linking to their default space.
 *
 * Token mode is publicly accessible (no login required to view the
 * card) so the inviter info is visible before signing in. Acting on
 * the invite (accept/reject) requires login.
 */

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AdminUser,
  InviteByToken,
  MyInvite,
  api,
  loginUrl,
} from "@/lib/api";

type TokenStatus = InviteByToken["status"];

function terminalMessage(status: TokenStatus): string {
  switch (status) {
    case "accepted":
      return "This invitation has already been accepted.";
    case "rejected":
      return "This invitation has been declined.";
    case "expired":
      return "This invitation has expired.";
    case "revoked":
      return "This invitation has been revoked by the inviter.";
    default:
      return "";
  }
}

function TokenMode({ token }: { token: string }) {
  const router = useRouter();
  const [invite, setInvite] = useState<InviteByToken | null>(null);
  const [me, setMe] = useState<AdminUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState<"accept" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notMyEmail, setNotMyEmail] = useState(false);

  const loadInvite = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getInviteByToken(token);
      setInvite(data);
    } catch (err: any) {
      if (err?.status === 404) {
        setError("This invitation link is no longer valid.");
      } else {
        setError(err?.message ?? "Failed to load invitation");
      }
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    loadInvite();
  }, [loadInvite]);

  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  // Refuse to act if the IdP-asserted email doesn't match the invite.
  useEffect(() => {
    if (invite && me) {
      setNotMyEmail(invite.email.toLowerCase() !== me.email.toLowerCase());
    }
  }, [invite, me]);

  async function onAccept() {
    if (!invite) return;
    if (!me) {
      window.location.href = loginUrl(
        `/accept-invite?token=${encodeURIComponent(token)}`,
      );
      return;
    }
    setSubmitting("accept");
    setError(null);
    try {
      const res = await api.acceptInvite(token);
      if (res.bot_slug) {
        router.replace(`/bots/${encodeURIComponent(res.bot_slug)}/dashboard`);
      } else {
        router.replace("/bots");
      }
    } catch (err: any) {
      setError(err?.message ?? "Failed to accept invitation");
      setSubmitting(null);
      loadInvite();
    }
  }

  async function onReject() {
    if (!invite) return;
    if (!me) {
      window.location.href = loginUrl(
        `/accept-invite?token=${encodeURIComponent(token)}`,
      );
      return;
    }
    if (!confirm("Decline this invitation? You can ask the inviter to send another one if you change your mind.")) {
      return;
    }
    setSubmitting("reject");
    setError(null);
    try {
      await api.rejectInvite(token);
      router.replace("/");
    } catch (err: any) {
      setError(err?.message ?? "Failed to decline invitation");
      setSubmitting(null);
      loadInvite();
    }
  }

  if (loading) {
    return (
      <div className="center">
        <div className="card narrow">
          <p className="muted">Loading invitation…</p>
        </div>
      </div>
    );
  }

  if (error || !invite) {
    return (
      <div className="center">
        <div className="card narrow">
          <h1 style={{ marginTop: 0 }}>Invitation unavailable</h1>
          <p>{error ?? "We couldn't find this invitation."}</p>
          <Link href="/" className="ghost">Go to your workspace</Link>
        </div>
      </div>
    );
  }

  if (invite.status !== "pending") {
    return (
      <div className="center">
        <div className="card narrow">
          <h1 style={{ marginTop: 0 }}>Invitation not active</h1>
          <p>{terminalMessage(invite.status)}</p>
          <Link href="/" className="ghost">Go to your workspace</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="center">
      <div className="card narrow">
        <h1 style={{ marginTop: 0 }}>
          You&apos;ve been invited to{" "}
          <strong>{invite.bot_name ?? "a space"}</strong>
        </h1>
        <p>
          <strong>{invite.inviter_name || invite.inviter_email || "An administrator"}</strong>
          {" "}invited <strong>{invite.email}</strong> to join{" "}
          <strong>{invite.bot_name ?? "this space"}</strong> as{" "}
          <span className="tag">{invite.role}</span>.
        </p>
        {invite.is_superadmin && (
          <p className="muted">
            This invitation also grants <strong>superadmin</strong> access.
          </p>
        )}
        {!me ? (
          <>
            <p className="muted" style={{ marginTop: 16 }}>
              Sign in as <strong>{invite.email}</strong> to accept or decline.
            </p>
            <button
              onClick={() =>
                (window.location.href = loginUrl(
                  `/accept-invite?token=${encodeURIComponent(token)}`,
                ))
              }
            >
              Sign in to continue
            </button>
          </>
        ) : notMyEmail ? (
          <>
            <p className="error" style={{ marginTop: 16 }}>
              You&apos;re signed in as <strong>{me.email}</strong>, but this
              invitation is for <strong>{invite.email}</strong>. Sign out and
              try again with the correct account.
            </p>
            <Link href="/" className="ghost">Back to your workspace</Link>
          </>
        ) : (
          <div className="row" style={{ marginTop: 20, gap: 12 }}>
            <button onClick={onAccept} disabled={submitting !== null}>
              {submitting === "accept" ? "Accepting…" : "Accept & enter space"}
            </button>
            <button
              className="ghost"
              onClick={onReject}
              disabled={submitting !== null}
            >
              {submitting === "reject" ? "Declining…" : "Decline"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function PickerMode() {
  const router = useRouter();
  const [me, setMe] = useState<AdminUser | null>(null);
  const [invites, setInvites] = useState<MyInvite[] | null>(null);
  const [defaultSlug, setDefaultSlug] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyToken, setBusyToken] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const profile = await api.me();
      setMe(profile);
      const mine = await api.listMyInvites();
      setInvites(mine);
      // Find the user's default space (own owner-membership where the
      // bot is flagged is_default). `/me` doesn't carry bot details, so
      // walk `/bots` and pick the first is_default match.
      try {
        const bots = await api.listBots();
        const def = bots.find(
          (b) => b.is_default && b.is_active && !b.is_deleted,
        );
        setDefaultSlug(def?.slug ?? null);
      } catch {
        setDefaultSlug(null);
      }
    } catch (err: any) {
      if (err?.status === 401) {
        window.location.href = loginUrl("/accept-invite");
        return;
      }
      setError(err?.message ?? "Failed to load");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onAccept(token: string) {
    setBusyToken(token);
    setError(null);
    try {
      const res = await api.acceptInvite(token);
      if (res.bot_slug) {
        router.replace(`/bots/${encodeURIComponent(res.bot_slug)}/dashboard`);
      } else {
        router.replace("/bots");
      }
    } catch (err: any) {
      setError(err?.message ?? "Failed to accept");
      setBusyToken(null);
      refresh();
    }
  }

  async function onReject(token: string) {
    setBusyToken(token);
    setError(null);
    try {
      await api.rejectInvite(token);
      await refresh();
    } catch (err: any) {
      setError(err?.message ?? "Failed to decline");
    } finally {
      setBusyToken(null);
    }
  }

  if (!me || invites === null) {
    return (
      <div className="center">
        <div className="card narrow">
          <p className="muted">Loading invitations…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="center">
      <div className="card" style={{ maxWidth: 640 }}>
        <h1 style={{ marginTop: 0 }}>Choose where to go</h1>
        <p className="muted">
          You have pending invitations. Accept any you want to join, decline
          any you don&apos;t, or skip ahead to your default workspace.
        </p>
        {error && <div className="error">{error}</div>}

        <div className="stack" style={{ marginTop: 16 }}>
          {invites.map((inv) => (
            <div
              key={inv.token}
              className="card"
              style={{
                display: "flex",
                gap: 12,
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <div>
                <div style={{ fontWeight: 600 }}>{inv.bot_name ?? "Space"}</div>
                <div className="muted" style={{ fontSize: 14 }}>
                  Invited by{" "}
                  {inv.inviter_name || inv.inviter_email || "an administrator"}
                  {" "}as <span className="tag">{inv.role}</span>
                  {inv.is_superadmin ? " (and superadmin)" : ""}
                </div>
              </div>
              <div className="row" style={{ gap: 6 }}>
                <button
                  onClick={() => onAccept(inv.token)}
                  disabled={busyToken !== null}
                >
                  {busyToken === inv.token ? "…" : "Accept"}
                </button>
                <button
                  className="ghost"
                  onClick={() => onReject(inv.token)}
                  disabled={busyToken !== null}
                >
                  Decline
                </button>
              </div>
            </div>
          ))}

          <div
            className="card"
            style={{
              display: "flex",
              gap: 12,
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div>
              <div style={{ fontWeight: 600 }}>Your default workspace</div>
              <div className="muted" style={{ fontSize: 14 }}>
                Continue without accepting any invitation. You can review them
                later from Profile → My invites.
              </div>
            </div>
            <Link
              href={defaultSlug ? `/bots/${encodeURIComponent(defaultSlug)}/dashboard` : "/"}
              className="ghost"
            >
              Use default
            </Link>
          </div>
        </div>

        {invites.length === 0 && (
          <p className="muted" style={{ marginTop: 16 }}>
            You don&apos;t have any pending invitations right now.
          </p>
        )}
      </div>
    </div>
  );
}

function Inner() {
  const params = useSearchParams();
  const token = params.get("token");
  if (token) {
    return <TokenMode token={token} />;
  }
  return <PickerMode />;
}

export default function AcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <div className="center">
          <p className="muted">Loading…</p>
        </div>
      }
    >
      <Inner />
    </Suspense>
  );
}
