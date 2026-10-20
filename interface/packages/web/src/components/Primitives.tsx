import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react';
import { Icon, type IconName } from './Icon';

export function classNames(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'quiet' | 'outline' | 'danger';
  icon?: IconName;
  iconAfter?: IconName;
  children?: ReactNode;
}

export function Button({
  variant = 'quiet',
  icon,
  iconAfter,
  className,
  children,
  type = 'button',
  ...props
}: ButtonProps) {
  const variants = {
    primary: 'accent-button accent-ink',
    quiet: 'border border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink)] hover:border-[var(--line-strong)]',
    outline: 'border border-[var(--line-strong)] bg-transparent text-[var(--ink-muted)] hover:bg-[var(--canvas-raised)] hover:text-[var(--ink)]',
    danger: 'border border-[var(--danger-line)] bg-[var(--danger-soft)] text-[var(--danger)] hover:bg-[var(--danger-hover)]',
  } as const;
  return (
    <button
      className={classNames(
        'instrument-button inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium disabled:opacity-45',
        variants[variant],
        className,
      )}
      type={type}
      {...props}
    >
      {icon ? <Icon name={icon} size={16} /> : null}
      {children ? <span>{children}</span> : null}
      {iconAfter ? <Icon name={iconAfter} size={16} /> : null}
    </button>
  );
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName;
  label: string;
  size?: number;
  variant?: 'quiet' | 'primary' | 'danger';
}

export function IconButton({ icon, label, size = 18, variant = 'quiet', className, type = 'button', ...props }: IconButtonProps) {
  const variants = {
    quiet: 'border border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink-muted)] hover:border-[var(--line-strong)] hover:text-[var(--ink)]',
    primary: 'accent-button accent-ink',
    danger: 'border border-[var(--danger-line)] bg-[var(--danger-soft)] text-[var(--danger)] hover:bg-[var(--danger-hover)]',
  } as const;
  return (
    <button
      aria-label={label}
      className={classNames(
        'instrument-button inline-flex h-10 w-10 items-center justify-center rounded-lg disabled:opacity-45',
        variants[variant],
        className,
      )}
      title={label}
      type={type}
      {...props}
    >
      <Icon name={icon} size={size} />
    </button>
  );
}

export function StatusPill({ status, label }: { status: 'online' | 'offline' | 'busy'; label: string }) {
  const tone = status === 'online' ? 'text-[var(--ok)]' : status === 'busy' ? 'accent-text' : 'text-[var(--ink-faint)]';
  return (
    <span className={classNames('inline-flex items-center gap-2 text-xs font-medium', tone)}>
      <span className={classNames('h-1.5 w-1.5 rounded-full bg-current', status === 'busy' && 'signal-pulse')} />
      <span>{label}</span>
    </span>
  );
}

export function SectionLabel({ children, icon }: { children: ReactNode; icon?: IconName }) {
  return (
    <div className="mb-3 flex items-center gap-2 text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-[var(--ink-faint)]">
      {icon ? <Icon name={icon} size={14} /> : null}
      <span>{children}</span>
    </div>
  );
}

/** A horizontal rule inside a panel, for separating sections that have no heading. */
export function Divider({ className }: { className?: string }) {
  return <div className={classNames('h-px w-full bg-[var(--line)]', className)} role="presentation" />;
}

export function FieldLabel({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-2 flex items-baseline justify-between gap-3">
      <label className="text-xs font-semibold text-[var(--ink-muted)]">{children}</label>
      {hint ? <span className="text-[0.68rem] text-[var(--ink-faint)]">{hint}</span> : null}
    </div>
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={classNames('instrument-well h-11 w-full rounded-lg px-3 text-sm text-[var(--ink)] outline-none transition placeholder:text-[var(--ink-faint)] focus:border-[var(--accent-line)] focus:ring-1 focus:ring-[var(--focus-ring)]', className)}
      {...props}
    />
  );
}

/**
 * A multi-line field, for a list rather than a value.
 *
 * A list of ids is pasted out of wherever it lives — a chat app, a spreadsheet
 * column — and it arrives one per line. An input cannot hold that, and a person
 * whose paste came back as one long entry has a form that looks broken rather
 * than a message about what it wanted.
 */
export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={classNames('instrument-well w-full rounded-lg px-3 py-2 font-mono text-sm text-[var(--ink)] outline-none transition placeholder:font-sans placeholder:text-[var(--ink-faint)] focus:border-[var(--accent-line)] focus:ring-1 focus:ring-[var(--focus-ring)]', className)}
      rows={3}
      {...props}
    />
  );
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {  return (
    <select
      className={classNames('instrument-well h-11 w-full appearance-none rounded-lg px-3 text-sm text-[var(--ink)] outline-none transition focus:border-[var(--accent-line)] focus:ring-1 focus:ring-[var(--focus-ring)]', className)}
      {...props}
    />
  );
}
