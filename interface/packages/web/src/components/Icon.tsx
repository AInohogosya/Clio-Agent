import type { ReactNode } from 'react';

export type IconName =
  | 'activity'
  | 'arrow'
  | 'arrowDown'
  | 'check'
  | 'chevron'
  | 'close'
  | 'command'
  | 'cpu'
  | 'database'
  | 'edit'
  | 'globe'
  | 'home'
  | 'key'
  | 'link'
  | 'lock'
  | 'moon'
  | 'palette'
  | 'pause'
  | 'play'
  | 'search'
  | 'send'
  | 'server'
  | 'settings'
  | 'shield'
  | 'spark'
  | 'sun'
  | 'terminal'
  | 'trash';

interface IconProps {
  name: IconName;
  size?: number;
  strokeWidth?: number;
  className?: string;
}

const paths: Record<IconName, ReactNode> = {
  activity: <><path d="M3 12h4l2.2-7 4.1 14L16 12h5" /></>,
  arrow: <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
  arrowDown: <><path d="M12 4v15" /><path d="m5 13 7 7 7-7" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  chevron: <path d="m9 18 6-6-6-6" />,
  close: <><path d="M6 6l12 12" /><path d="M18 6 6 18" /></>,
  command: <><path d="M18 3a3 3 0 1 0 0 6h-3V6a3 3 0 1 0-3 3h3v6a3 3 0 1 0 3-3v-3h3a3 3 0 1 0 0-6h-3V3Z" /><path d="M9 9h6v6H9z" /></>,
  cpu: <><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 9h6v6H9z" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></>,
  database: <><ellipse cx="12" cy="5" rx="7" ry="3" /><path d="M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5" /><path d="M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7" /></>,
  edit: <><path d="m4 16-.8 4.8L8 20l11.5-11.5a2.1 2.1 0 0 0-3-3L5 17" /><path d="m14.5 7.5 3 3" /></>,
  globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.2 2.5 3.3 5.5 3.3 9s-1.1 6.5-3.3 9c-2.2-2.5-3.3-5.5-3.3-9S9.8 5.5 12 3Z" /></>,
  home: <><path d="m3 10 9-7 9 7" /><path d="M5 9v11h14V9M9 20v-6h6v6" /></>,
  key: <><circle cx="8" cy="15" r="4" /><path d="m11 12 8-8M16 5l3 3M14 7l3 3" /></>,
  link: <><path d="M10 13.5a4 4 0 0 0 5.7.4l2.6-2.6a4 4 0 0 0-5.7-5.7l-1.5 1.5" /><path d="M14 10.5a4 4 0 0 0-5.7-.4l-2.6 2.6a4 4 0 0 0 5.7 5.7l1.5-1.5" /></>,
  lock: <><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></>,
  moon: <path d="M20.5 15.5A8.5 8.5 0 0 1 8.5 3.5 8.5 8.5 0 1 0 20.5 15.5Z" />,
  palette: <><path d="M12 3a9 9 0 0 0 0 18h1.2a1.8 1.8 0 0 0 0-3.6H12a1.8 1.8 0 0 1 0-3.6h4.2A4.8 4.8 0 0 0 21 9.1 6.1 6.1 0 0 0 18.9 3H12Z" /><circle cx="7.5" cy="10" r=".8" fill="currentColor" stroke="none" /><circle cx="9" cy="6.8" r=".8" fill="currentColor" stroke="none" /><circle cx="13" cy="6" r=".8" fill="currentColor" stroke="none" /><circle cx="16.2" cy="7.2" r=".8" fill="currentColor" stroke="none" /></>,
  pause: <><path d="M8 5v14M16 5v14" /></>,
  play: <path d="m8 5 11 7-11 7V5Z" />,
  search: <><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 5 5" /></>,
  send: <><path d="m21 3-7.2 18-3.7-7.1L3 10.2 21 3Z" /><path d="M10.1 13.9 21 3" /></>,
  server: <><rect x="3" y="4" width="18" height="6" rx="2" /><rect x="3" y="14" width="18" height="6" rx="2" /><path d="M7 7h.01M7 17h.01M11 7h6M11 17h6" /></>,
  settings: <><circle cx="12" cy="12" r="3.2" /><path d="M20.7 10.2a1.8 1.8 0 0 1 0 3.6 1.8 1.8 0 0 0-1.275 3.079 1.8 1.8 0 0 1-2.546 2.546 1.8 1.8 0 0 0-3.079 1.275 1.8 1.8 0 0 1-3.6 0 1.8 1.8 0 0 0-3.079-1.275 1.8 1.8 0 0 1-2.546-2.546 1.8 1.8 0 0 0-1.275-3.079 1.8 1.8 0 0 1 0-3.6 1.8 1.8 0 0 0 1.275-3.079 1.8 1.8 0 0 1 2.546-2.546 1.8 1.8 0 0 0 3.079-1.275 1.8 1.8 0 0 1 3.6 0 1.8 1.8 0 0 0 3.079 1.275 1.8 1.8 0 0 1 2.546 2.546 1.8 1.8 0 0 0 1.275 3.079Z" /></>,
  shield: <><path d="M12 3 19 6v5c0 4.6-2.9 8.2-7 10-4.1-1.8-7-5.4-7-10V6l7-3Z" /><path d="m9 12 2 2 4-4" /></>,
  spark: <><path d="m12 2 1.5 6.5L20 10l-6.5 1.5L12 18l-1.5-6.5L4 10l6.5-1.5L12 2Z" /><path d="m19 16 .6 2.4L22 19l-2.4.6L19 22l-.6-2.4L16 19l2.4-.6L19 16Z" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  terminal: <><path d="m4 5 6 7-6 7M12 19h8" /></>,
  trash: <><path d="M4 7h16M10 11v5M14 11v5" /><path d="m6 7 1 13h10l1-13M9 7V4h6v3" /></>,
};

export function Icon({ name, size = 18, strokeWidth = 1.8, className }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
    >
      {paths[name]}
    </svg>
  );
}
