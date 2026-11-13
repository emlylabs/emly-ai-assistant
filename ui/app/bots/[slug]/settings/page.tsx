"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useBot } from "@/components/BotShell";
import Modal from "@/components/Modal";
import { ApiError, api } from "@/lib/api";
import { isValidSlug } from "@/lib/slugify";

type SlugCheck =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "available" }
  | { status: "invalid" }
  | { status: "taken" }
  | { status: "error"; message: string };

export default function BotSettingsPage() {
  const { bot, currentRole, refreshBot } = useBot();
  const router = useRouter();
  const isOwner = currentRole === "owner";
  const canWrite = isOwner || currentRole === "admin";

  const [name, setName] = useState(bot.name);
  const [savingName, setSavingName] = useState(false);
  const [nameMsg, setNameMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [slugDraft, setSlugDraft] = useState(bot.slug);
  const [editingSlug, setEditingSlug] = useState(false);
  const [slugCheck, setSlugCheck] = useState<SlugCheck>({ status: "idle" });
  const [savingSlug, setSavingSlug] = useState(false);
  const [slugMsg, setSlugMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [pauseOpen, setPauseOpen] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [pauseError, setPauseError] = useState<string | null>(null);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Re-sync the slug draft when BotShell refreshes the bot row (e.g. after
  // a rename completes and the URL re-routes us back to the new slug).
  useEffect(() => {
    setSlugDraft(bot.slug);
    setEditingSlug(false);
    setSlugCheck({ status: "idle" });
  }, [bot.slug]);

  // Debounced slug availability check while editing.
  useEffect(() => {
    if (!editingSlug) return;
    if (slugDraft === bot.slug) {
      setSlugCheck({ status: "idle" });
      return;
    }
    if (!isValidSlug(slugDraft)) {
      setSlugCheck({ status: "invalid" });
      return;
    }
    setSlugCheck({ status: "checking" });
    let cancelled = false;
    const t = setTimeout(() => {
      api
        .checkBotSlugAvailable(slugDraft)
        .then((res) => {
          if (cancelled) return;
          if (res.available) setSlugCheck({ status: "available" });
          else if (res.reason === "taken") setSlugCheck({ status: "taken" });
          else setSlugCheck({ status: "invalid" });
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setSlugCheck({
            status: "error",
            message: err instanceof Error ? err.message : "Check failed",
          });
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [slugDraft, editingSlug, bot.slug]);

  async function onRename(e: FormEvent) {
    e.preventDefault();
    setNameMsg(null);
    if (!canWrite) return;
    if (name.trim() === bot.name) return;
    setSavingName(true);
    try {
      await api.patchBot(bot.slug, { name: name.trim() });
      await refreshBot();
      setNameMsg({ ok: true, text: "Saved." });
    } catch (err: unknown) {
      setNameMsg({
        ok: false,
        text: err instanceof Error ? err.message : "Save failed",
      });
    } finally {
      setSavingName(false);
    }
  }

  async function onRenameSlug(e: FormEvent) {
    e.preventDefault();
    setSlugMsg(null);
    if (!isOwner) return;
    if (slugDraft === bot.slug) {
      setEditingSlug(false);
      return;
    }
    if (!isValidSlug(slugDraft)) {
      setSlugCheck({ status: "invalid" });
      return;
    }
    setSavingSlug(true);
    try {
      await api.patchBot(bot.slug, { slug: slugDraft });
      // The current URL still has the old slug. Hard-replace so all the
      // sub-pages and the BotShell loader re-key off the new slug.
      router.replace(`/bots/${slugDraft}/settings`);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        setSlugCheck({ status: "taken" });
        setSlugMsg({ ok: false, text: "Slug already in use." });
      } else {
        setSlugMsg({
          ok: false,
          text: err instanceof Error ? err.message : "Rename failed",
        });
      }
      setSavingSlug(false);
    }
  }

  async function onTogglePause() {
    if (!canWrite) return;
    setPauseError(null);
    setPausing(true);
    try {
      await api.patchBot(bot.slug, { is_active: !bot.is_active });
      await refreshBot();
      setPauseOpen(false);
    } catch (err: unknown) {
      setPauseError(err instanceof Error ? err.message : "Pause failed");
    } finally {
      setPausing(false);
    }
  }

  async function onConfirmDelete() {
    if (typed !== bot.slug) {
      setDeleteError("Slug doesn't match — type the bot's slug exactly to confirm.");
      return;
    }
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteBot(bot.slug);
      router.replace(`/bots?undo=${encodeURIComponent(bot.slug)}`);
    } catch (err: unknown) {
      setDeleteError(err instanceof Error ? err.message : "Delete failed");
      setDeleting(false);
    }
  }

  const slugDisabledSave =
    savingSlug ||
    slugDraft === bot.slug ||
    slugCheck.status === "invalid" ||
    slugCheck.status === "taken" ||
    slugCheck.status === "checking";

  const isDefaultSpace = !!bot.is_default;
  const defaultRenameLocked = isDefaultSpace && !!bot.default_renamed;

  return (
    <>
      <div className="header">
        <h1>Settings — {bot.name}</h1>
      </div>

      {isDefaultSpace && !defaultRenameLocked && (
        <div className="banner advisory">
          This is your default workspace. You can rename it (or change its
          slug) <strong>once</strong> — choose carefully, since invitation
          links and bookmarks pointing at the current slug will keep
          working after that.
        </div>
      )}
      {defaultRenameLocked && (
        <div className="banner advisory">
          This default workspace has already been renamed once. The name and
          slug are locked to keep invitation links stable.
        </div>
      )}

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Bot identity</h2>
        <form onSubmit={onRename}>
          <div className="field">
            <label htmlFor="bot-id-display">Bot id</label>
            <code style={{ display: "block", padding: "8px 12px", background: "var(--paper)", borderRadius: 6, border: "1px solid var(--panel-border)" }}>
              {bot.id}
            </code>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Stable identifier — bake this into widget URLs (the Channels tab does).
            </div>
          </div>
          <div className="field">
            <label htmlFor="bot-slug-display">Slug</label>
            {editingSlug ? (
              <form onSubmit={onRenameSlug} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                <div style={{ flex: 1 }}>
                  <input
                    id="bot-slug-display"
                    type="text"
                    value={slugDraft}
                    onChange={(e) => setSlugDraft(e.target.value.toLowerCase())}
                    style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                    autoFocus
                    aria-invalid={slugCheck.status === "invalid" || slugCheck.status === "taken"}
                    disabled={savingSlug}
                  />
                  <SlugCheckMessage check={slugCheck} slug={slugDraft} />
                  {slugMsg && (
                    <div className={slugMsg.ok ? "success" : "error"} style={{ fontSize: 12, marginTop: 4 }}>
                      {slugMsg.text}
                    </div>
                  )}
                </div>
                <button type="submit" disabled={slugDisabledSave}>
                  {savingSlug ? "Renaming…" : "Save slug"}
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setEditingSlug(false);
                    setSlugDraft(bot.slug);
                    setSlugCheck({ status: "idle" });
                    setSlugMsg(null);
                  }}
                  disabled={savingSlug}
                >
                  Cancel
                </button>
              </form>
            ) : (
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <code style={{ flex: 1, padding: "8px 12px", background: "var(--paper)", borderRadius: 6, border: "1px solid var(--panel-border)" }}>
                  {bot.slug}
                </code>
                {isOwner && !defaultRenameLocked && (
                  <button
                    type="button"
                    className="ghost compact"
                    onClick={() => setEditingSlug(true)}
                  >
                    Rename
                  </button>
                )}
              </div>
            )}
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Admin URL identifier. Renaming is safe — embeds use the bot id, not the slug.
            </div>
          </div>
          <div className="field">
            <label htmlFor="bot-name">Display name</label>
            <input
              id="bot-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={!canWrite || defaultRenameLocked}
              required
            />
          </div>
          {nameMsg && (
            <div className={nameMsg.ok ? "success" : "error"}>{nameMsg.text}</div>
          )}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button
              type="submit"
              disabled={
                !canWrite ||
                savingName ||
                name.trim() === bot.name ||
                defaultRenameLocked
              }
            >
              {savingName ? "Saving…" : "Save name"}
            </button>
          </div>
        </form>
      </div>

      {canWrite && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Availability</h2>
          <p className="muted" style={{ marginTop: 4 }}>
            {bot.is_active
              ? "Live — visitors can chat with this bot."
              : "Paused — visitors see the bot but it won't respond."}
          </p>
          <div className="row" style={{ justifyContent: "flex-start" }}>
            <button
              className={bot.is_active ? "danger" : ""}
              onClick={() => setPauseOpen(true)}
            >
              {bot.is_active ? "Pause bot" : "Unpause bot"}
            </button>
          </div>
        </div>
      )}

      {isOwner && (
        <div className="danger-zone">
          <h2>Danger zone</h2>
          <p>
            Soft-delete this bot. Conversations stop immediately. Files and
            vectors remain recoverable for <strong>7 days</strong>; after the
            grace period, the daily purge job runs and the bot is unrecoverable.
          </p>
          <button className="danger" onClick={() => setConfirmOpen(true)}>
            Delete this bot
          </button>
        </div>
      )}

      <Modal
        open={pauseOpen}
        onClose={() => setPauseOpen(false)}
        closeOnBackdrop={!pausing}
        closeOnEsc={!pausing}
        title={bot.is_active ? "Pause this bot?" : "Unpause this bot?"}
        size="sm"
      >
        <div className="modal-body">
          {bot.is_active ? (
            <p>
              Visitors will see the bot but it won&apos;t respond. You can
              unpause anytime.
            </p>
          ) : (
            <p>Visitors will start getting live answers again.</p>
          )}
          {pauseError && <div className="error" role="alert">{pauseError}</div>}
        </div>
        <div className="modal-footer">
          <button className="ghost" onClick={() => setPauseOpen(false)} disabled={pausing}>
            Cancel
          </button>
          <button
            className={bot.is_active ? "danger" : ""}
            onClick={onTogglePause}
            disabled={pausing}
          >
            {pausing
              ? bot.is_active
                ? "Pausing…"
                : "Unpausing…"
              : bot.is_active
                ? "Pause bot"
                : "Unpause bot"}
          </button>
        </div>
      </Modal>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        closeOnBackdrop={!deleting}
        closeOnEsc={!deleting}
        title="Delete this bot?"
        size="sm"
      >
        <div className="modal-body">
          <p>
            Soft-delete <strong>{bot.name}</strong>?
          </p>
          <ul style={{ margin: "4px 0 16px 18px", padding: 0, fontSize: 13 }}>
            <li>Conversations stop <strong>immediately</strong>.</li>
            <li>Files, conversations, and vectors remain recoverable for <strong>7 days</strong>.</li>
            <li>After the grace period, the daily purge job runs and the bot is unrecoverable.</li>
          </ul>
          <div className="field">
            <label htmlFor="confirm-slug">
              Type the bot&apos;s slug (<code>{bot.slug}</code>) to confirm:
            </label>
            <input
              id="confirm-slug"
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              disabled={deleting}
            />
          </div>
          {deleteError && <div className="error" role="alert">{deleteError}</div>}
        </div>
        <div className="modal-footer">
          <button className="ghost" onClick={() => setConfirmOpen(false)} disabled={deleting}>
            Cancel
          </button>
          <button
            className="danger"
            onClick={onConfirmDelete}
            disabled={deleting || typed !== bot.slug}
          >
            {deleting ? "Deleting…" : "Delete bot"}
          </button>
        </div>
      </Modal>
    </>
  );
}

function SlugCheckMessage({ check, slug }: { check: SlugCheck; slug: string }) {
  if (check.status === "checking") {
    return (
      <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
        Checking availability…
      </div>
    );
  }
  if (check.status === "available") {
    return (
      <div className="success" style={{ marginTop: 4, fontSize: 12 }}>
        <code>{slug}</code> is available.
      </div>
    );
  }
  if (check.status === "taken") {
    return (
      <div className="error" style={{ marginTop: 4, fontSize: 12 }}>
        Slug <code>{slug}</code> is already in use.
      </div>
    );
  }
  if (check.status === "invalid") {
    return (
      <div className="error" style={{ marginTop: 4, fontSize: 12 }}>
        Lowercase letters, digits, and hyphens. Must be 2–64 characters and cannot
        start or end with a hyphen.
      </div>
    );
  }
  if (check.status === "error") {
    return (
      <div className="error" style={{ marginTop: 4, fontSize: 12 }}>
        Couldn&apos;t check availability ({check.message}).
      </div>
    );
  }
  return null;
}

