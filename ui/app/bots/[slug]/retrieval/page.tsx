"use client";

import Link from "next/link";
import { useBot } from "@/components/BotShell";
import RAGSearchPanel from "@/components/RAGSearchPanel";

/**
 * Dedicated retrieval-debug surface for the bot.
 *
 * §11 T2 — promoted out of the Files tab so admins reach for it during
 * config tuning (not only when poking at files). The panel still appears
 * under the file table for convenience; this route is the canonical
 * landing for "why is the bot answering — or not answering — this query?"
 */
export default function BotRetrievalPage() {
  const { bot } = useBot();
  return (
    <>
      <div className="header">
        <div>
          <h1>Retrieval debug — {bot.name}</h1>
          <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            What the bot would retrieve for a given query
          </div>
        </div>
      </div>

      <div className="banner advisory" style={{ marginBottom: 16 }}>
        Use this surface when the bot is answering incorrectly to inspect the
        chunks the agent saw. The hits below are exactly what the chat
        runtime would feed the LLM as <code>{"{context}"}</code> for this
        query, with the same <code>top_k</code> and threshold the production
        config applies. Tune those values in{" "}
        <Link href={`/bots/${bot.slug}/config`}>Config → RAG</Link>; upload
        more knowledge under <Link href={`/bots/${bot.slug}/files`}>Knowledge</Link>.
      </div>

      <RAGSearchPanel slug={bot.slug} />
    </>
  );
}
