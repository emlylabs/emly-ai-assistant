"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownBubbleProps = {
  /** Raw markdown text. Empty string is fine; renders nothing. */
  children: string;
};

/** Open every link in a new tab and disable opener — assistant replies often
 * cite external help-center URLs that we don't want stealing focus. */
const renderers: Components = {
  a: ({ href, children, ...rest }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
      {children}
    </a>
  ),
  // Inline `code` and fenced ``` blocks reuse the global `<code>` and
  // `<pre>` styles in globals.css; we only override the block container so
  // fenced blocks pick up the `.snippet` look. ReactMarkdown 10 wraps
  // fenced code in `<pre><code>…</code></pre>`, so we just style the `pre`.
  pre: ({ children, ...rest }) => (
    <pre className="snippet" {...rest}>
      {children}
    </pre>
  ),
  // Constrain images so a stray bot reply linking a giant screenshot
  // doesn't blow the bubble's width. Lazy-load to keep the thread fast
  // when many sessions are scrolled.
  img: ({ src, alt, ...rest }) => (
    <img
      src={typeof src === "string" ? src : undefined}
      alt={alt ?? ""}
      loading="lazy"
      style={{ maxWidth: "100%", height: "auto", borderRadius: 6 }}
      {...rest}
    />
  ),
};

/**
 * Renders a bot message body as markdown. Used for assistant bubbles in
 * the conversation thread; user-typed messages are rendered as plain text
 * since they're rarely intended as markdown and we don't want their `*`
 * characters to silently bold things.
 *
 * GFM plugin gives us tables, strikethrough, autolinks, and task lists.
 */
export default function MarkdownBubble({ children }: MarkdownBubbleProps) {
  if (!children) return null;
  return (
    <div className="msg-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={renderers}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
