// Google Material Symbols (Rounded). The stylesheet asks Google Fonts for only these glyphs, so add a
// name here before using it; an unknown name renders as plain text.
export const ICON_NAMES = [
  "admin_panel_settings",
  "apartment",
  "archive",
  "arrow_back",
  "arrow_forward",
  "backpack",
  "badge",
  "block",
  "calendar_month",
  "cancel",
  "check_circle",
  "chevron_left",
  "chevron_right",
  "close",
  "dashboard",
  "domain_add",
  "domain_disabled",
  "download",
  "edit",
  "edit_note",
  "error",
  "event_repeat",
  "fact_check",
  "filter_alt_off",
  "forward_to_inbox",
  "group",
  "history",
  "how_to_reg",
  "inbox",
  "info",
  "link_off",
  "logout",
  "mail",
  "manage_history",
  "meeting_room",
  "menu",
  "menu_book",
  "pending_actions",
  "person_add",
  "person_off",
  "person_remove",
  "publish",
  "refresh",
  "replay",
  "save",
  "schedule",
  "school",
  "search",
  "shield_person",
  "task_alt",
  "verified_user",
  "visibility",
  "work",
] as const;

export type IconName = (typeof ICON_NAMES)[number];

// Google Fonts requires icon_names in alphabetical order.
export const ICON_FONT_URL =
  "https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" +
  `&icon_names=${[...ICON_NAMES].sort().join(",")}&display=block`;

/** A decorative Material Symbol; give its button or link an accessible name instead. */
export function Icon({
  name,
  size = 20,
  filled = false,
  className = "",
}: {
  name: IconName;
  size?: number;
  filled?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={`icon ${className}`}
      style={{
        fontSize: size,
        width: size,
        height: size,
        fontVariationSettings: `"FILL" ${filled ? 1 : 0}, "wght" 400, "GRAD" 0, "opsz" ${Math.min(48, Math.max(20, size))}`,
      }}
    >
      {name}
    </span>
  );
}
