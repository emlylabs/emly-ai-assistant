"use client";

type BotAvatarProps = {
  /** Used to derive a deterministic palette colour. */
  slug: string;
  /** Used to derive the 2-letter monogram. */
  name: string;
  /** Override the size class — the base is 28px (table rows). Pass "lg" for
   * detail headers (44px). */
  size?: "sm" | "md" | "lg";
  className?: string;
};

const PALETTE_COUNT = 10;

/**
 * Hash a slug into a stable integer between 0 and `PALETTE_COUNT - 1`. We
 * pick FNV-1a because it's fast, dependency-free, and gives a much better
 * spread than `slug.length % N` (which would group all 4-letter slugs into
 * the same colour).
 */
function paletteIndex(slug: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < slug.length; i++) {
    hash ^= slug.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return Math.abs(hash) % PALETTE_COUNT;
}

function initials(name: string): string {
  if (!name) return "??";
  const trimmed = name.trim();
  const parts = trimmed.split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return trimmed.slice(0, 2).toUpperCase();
}

const SIZE_PRESETS: Record<NonNullable<BotAvatarProps["size"]>, { px: number; font: number; radius: number }> = {
  sm: { px: 22, font: 10, radius: 5 },
  md: { px: 28, font: 11, radius: 6 },
  lg: { px: 44, font: 14, radius: 8 },
};

export default function BotAvatar({ slug, name, size = "md", className }: BotAvatarProps) {
  const idx = paletteIndex(slug || name || "?") + 1; // ba-1..ba-10
  const preset = SIZE_PRESETS[size];
  return (
    <span
      className={["bot-avatar", `ba-${idx}`, className].filter(Boolean).join(" ")}
      style={{
        width: preset.px,
        height: preset.px,
        fontSize: preset.font,
        borderRadius: preset.radius,
      }}
      aria-label={name}
      title={name}
    >
      {initials(name)}
    </span>
  );
}
