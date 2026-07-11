"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { useBot } from "@/components/BotShell";
import { api } from "@/lib/api";

export default function BotSettingsPage() {
  const { bot, currentRole } = useBot();
  const router = useRouter();
  const isOwner = currentRole === "owner";

  const [name, setName] = useState(bot.name);
  const [savingName, setSavingName] = useState(false);
  const [nameMsg, setNameMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function onRename(e: FormEvent) {
    e.preventDefault();
    setNameMsg(null);
    if (!isOwner) return;
    if (name.trim() === bot.name) return;
    setSavingName(true);
    try {
      await api.patchBot(bot.slug, { name: name.trim() });
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

  async function onConfirmDelete() {
    if (typed !== bot.slug) {
      setDeleteError("Slug doesn't match — type the bot's slug exactly to confirm.");
      return;
    }
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteBot(bot.slug);
      router.replace("/bots");
    } catch (err: unknown) {
      setDeleteError(err instanceof Error ? err.message : "Delete failed");
      setDeleting(false);
    }
  }

  return (
    <>
      <div className="header">
        <h1>Settings — {bot.name}</h1>
      </div>

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
            <code style={{ display: "block", padding: "8px 12px", background: "var(--paper)", borderRadius: 6, border: "1px solid var(--panel-border)" }}>
              {bot.slug}
            </code>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Used in admin URLs. Slugs are not currently editable.
            </div>
          </div>
          <div className="field">
            <label htmlFor="bot-name">Display name</label>
            <input
              id="bot-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={!isOwner}
              required
            />
          </div>
          {nameMsg && (
            <div className={nameMsg.ok ? "success" : "error"}>{nameMsg.text}</div>
          )}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="submit" disabled={!isOwner || savingName || name.trim() === bot.name}>
              {savingName ? "Saving…" : "Save name"}
            </button>
          </div>
        </form>
      </div>

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

      {confirmOpen && (
        <div
          role="dialog"
          aria-modal="true"
          onClick={() => !deleting && setConfirmOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
        >
          <div
            className="card narrow"
            style={{ minWidth: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ marginTop: 0, color: "var(--error)" }}>Delete this bot?</h2>
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
                autoFocus
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                disabled={deleting}
              />
            </div>
            {deleteError && <div className="error">{deleteError}</div>}
            <div className="row" style={{ justifyContent: "flex-end" }}>
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
          </div>
        </div>
      )}
    </>
  );
}
