"use client";

import type { MessageCountByChannel } from "@/lib/api";

type ChannelFilterProps = {
  channels: MessageCountByChannel[] | null;
  value: string | null;
  onChange: (channelId: string | null) => void;
};

/**
 * Compact `<select>` for scoping the analytics page to a single
 * `bot_channel.id`. Sources its options from the channel-mix endpoint
 * (so a channel that hasn't sent any messages in the window simply
 * doesn't appear — keeps the menu honest about what's filterable).
 *
 * The orphan bucket (channel_id null) is omitted as a filter target
 * since the backend filter requires a non-null id; users can still
 * see those messages in the unfiltered view.
 */
export default function ChannelFilter({ channels, value, onChange }: ChannelFilterProps) {
  const options = (channels ?? []).filter((c) => c.channel_id);
  const disabled = options.length === 0;

  return (
    <select
      className="ghost compact"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={disabled}
      title={
        disabled
          ? "No per-channel traffic in the loaded window"
          : "Filter analytics to a single channel"
      }
      style={{
        fontSize: 12.5,
        padding: "4px 8px",
        borderRadius: 4,
        background: value ? "var(--paper)" : undefined,
      }}
    >
      <option value="">All channels</option>
      {options.map((c) => {
        const label = c.display_name || c.channel_type || c.channel_id || "?";
        const suffix =
          c.display_name && c.channel_type ? ` (${c.channel_type})` : "";
        return (
          <option key={c.channel_id ?? "?"} value={c.channel_id ?? ""}>
            {label}{suffix} · {c.count.toLocaleString()}
          </option>
        );
      })}
    </select>
  );
}
