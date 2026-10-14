import type { ReactNode } from "react";

const shapes = {
  user: <><circle cx="12" cy="7" r="4" /><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /></>,
  users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.9" /><circle cx="9" cy="7" r="4" /><path d="M16 3.1a4 4 0 0 1 0 7.8" /></>,
  bot: <><rect x="3" y="8" width="18" height="13" rx="3" /><path d="M12 8V3M9 14h.01M15 14h.01M9 18h6" /></>,
  share: <><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="m8.6 10.5 6.8-4M8.6 13.5l6.8 4" /></>,
  activity: <path d="M22 12h-4l-3 8L9 4l-3 8H2" />,
  sparkles: <><path d="m12 3 2.8 6.2L21 12l-6.2 2.8L12 21l-2.8-6.2L3 12l6.2-2.8Z" /><path d="M21 2v4M19 4h4" /></>,
  alert: <><path d="m12 3 10 18H2Z" /><path d="M12 9v5M12 17h.01" /></>,
  clipboard: <><rect x="5" y="5" width="14" height="16" rx="2" /><rect x="9" y="2" width="6" height="5" rx="1" /><path d="M9 12h6M9 16h4" /></>,
  target: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></>,
  copy: <><rect x="8" y="8" width="13" height="13" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  message: <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5 9 9 0 0 1-4-.9L3 21l1.9-5.5a9 9 0 0 1-.9-4A8.5 8.5 0 0 1 12.5 3 8.5 8.5 0 0 1 21 11.5Z" />,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 6 9 7 9-7" /></>,
  phone: <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z" />,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 11h18" /></>,
  trophy: <><path d="M8 3h8v5a4 4 0 0 1-8 0ZM8 5H4v2a4 4 0 0 0 4 4M16 5h4v2a4 4 0 0 1-4 4M12 12v7M8 21h8M10 19h4" /></>,
  trend: <><path d="m3 17 6-6 4 4 8-10M15 5h6v6" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
  back: <path d="M19 12H5m5-5-5 5 5 5" />,
  chevron: <path d="m8 10 4 4 4-4" />,
  plus: <path d="M12 5v14M5 12h14" />,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
  filter: <path d="M3 4h18l-7 8v7l-4 2v-9Z" />,
  sort: <><path d="M3 6h10M3 12h7M3 18h4M18 5v14m-3-3 3 3 3-3" /></>,
  settings: <><path d="M5 3v6m0 4v8M12 3v10m0 4v4M19 3v2m0 4v12M2 9h6M9 17h6M16 5h6" /></>,
  more: <><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></>,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  monitor: <><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M12 17v4M8 21h8" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2m-3-8 1-1M5 19l-1 1M4 4l1 1m14 14 1 1" /></>,
  moon: <path d="M20.5 13A9 9 0 0 1 11 3.5 9 9 0 1 0 20.5 13Z" />,
  paperclip: <path d="m21 11-9 9a6 6 0 0 1-8.5-8.5L13 2a4 4 0 0 1 5.7 5.7L9 17.4a2 2 0 0 1-2.8-2.8L15 6" />,
  sunrise: <><path d="M3 20h18M5 16a7 7 0 0 1 14 0M12 2v3M3 7l2 2M21 7l-2 2M1 16h2M21 16h2" /></>,
  edit: <><path d="m16 3 5 5-12 12-6 1 1-6ZM14 5l5 5" /></>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></>,
} satisfies Record<string, ReactNode>;

export type CrmIconName = keyof typeof shapes;

/** Shared stroke family for CRM navigation, actions and activity. */
export function CrmIcon({ name, size = 18 }: { name: CrmIconName; size?: number }) {
  return <svg className="kacrm-icon" width={size} height={size} viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true">{shapes[name]}</svg>;
}
