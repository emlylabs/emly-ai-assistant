"use client";

import { channelLabel } from "@/components/ChannelChip";
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
        const type = c.channel_type ? channelLabel(c.channel_type) : null;
        const label = c.display_name || type || c.channel_id || "?";
        const suffix = c.display_name && type ? ` (${type})` : "";
        return (
          <option key={c.channel_id ?? "?"} value={c.channel_id ?? ""}>
            {label}{suffix} · {c.count.toLocaleString()}
          </option>
        );
      })}
    </select>
  );
}
