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
    quiet: 'border border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink)] hover:border-[var(--line-strong)] hover:bg-[var(--canvas-well)]',
    outline: 'border border-[var(--line-strong)] bg-transparent text-[var(--ink-muted)] hover:bg-[var(--canvas-raised)] hover:text-[var(--ink)]',
    danger: 'border border-[var(--danger-line)] bg-[var(--danger-soft)] text-[var(--danger)] hover:bg-[var(--danger-hover)]',
  } as const;
  return (
    <button
      className={classNames(
        'instrument-button inline-flex min-h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold disabled:opacity-45',
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
        'instrument-button inline-flex h-10 w-10 items-center justify-center rounded-xl disabled:opacity-45',
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
  const tone = status === 'online' ? 'text-[var(--ok)] border-[var(--ok-line)] bg-[var(--ok-soft)]' : status === 'busy' ? 'accent-text border-[var(--accent-line)] bg-[var(--accent-soft)]' : 'text-[var(--ink-faint)] border-[var(--line)] bg-[var(--canvas-well)]';
  return (
    <span className={classNames('inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold', tone)}>
      <span className={classNames('h-1.5 w-1.5 rounded-full bg-current', status === 'busy' && 'signal-pulse')} />
      <span>{label}</span>
    </span>
  );
}

export function SectionLabel({ children, icon }: { children: ReactNode; icon?: IconName }) {
  return (
    <div className="mb-3 flex items-center gap-2 text-[0.68rem] font-bold uppercase tracking-[0.18em] text-[var(--ink-faint)]">
      {icon ? <Icon name={icon} size={14} /> : null}
      <span>{children}</span>
    </div>
  );
}

export function Divider({ className }: { className?: string }) {
  return <div className={classNames('h-px w-full bg-[var(--line)]', className)} role="presentation" />;
}

export function FieldLabel({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-2 flex items-baseline justify-between gap-3">
      <label className="text-xs font-bold tracking-wide text-[var(--ink-muted)]">{children}</label>
      {hint ? <span className="text-[0.68rem] leading-relaxed text-[var(--ink-faint)]">{hint}</span> : null}
    </div>
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={classNames('instrument-well h-11 w-full rounded-xl px-3.5 text-sm text-[var(--ink)] outline-none transition placeholder:text-[var(--ink-faint)] focus:border-[var(--accent-line)] focus:ring-2 focus:ring-[var(--focus-ring)] disabled:opacity-50', className)}
      {...props}
    />
  );
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={classNames('instrument-well w-full rounded-xl px-3.5 py-2.5 font-mono text-sm text-[var(--ink)] outline-none transition placeholder:font-sans placeholder:text-[var(--ink-faint)] focus:border-[var(--accent-line)] focus:ring-2 focus:ring-[var(--focus-ring)] disabled:opacity-50', className)}
      rows={3}
      {...props}
    />
  );
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={classNames('instrument-well h-11 w-full appearance-none rounded-xl px-3.5 text-sm text-[var(--ink)] outline-none transition focus:border-[var(--accent-line)] focus:ring-2 focus:ring-[var(--focus-ring)] disabled:opacity-50', className)}
      {...props}
    />
  );
}

/**
 * Slide switch for binary ON/OFF settings.
 * Rendered as a real <input type="checkbox" role="switch"> so keyboard and
 * screen readers announce it as a switch, not a button styled like one.
 * The knob slides; the track fills with accent when on.
 */
export function Switch({
  checked,
  onChange,
  label,
  hint,
  disabled,
  id,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <label
      className={classNames(
        'flex cursor-pointer items-center justify-between gap-4 rounded-2xl border border-[var(--line)] bg-[var(--canvas-well)] px-4 py-3.5 transition hover:border-[var(--line-strong)]',
        disabled && 'cursor-not-allowed opacity-55',
      )}
      htmlFor={id}
    >
      <span className="min-w-0">
        <span className="block text-sm font-semibold leading-snug text-[var(--ink)]">{label}</span>
        {hint ? <span className="mt-1 block text-[0.72rem] leading-relaxed text-[var(--ink-faint)]">{hint}</span> : null}
      </span>
      <span className="flex shrink-0 items-center gap-2.5">
        <span className={classNames('text-[0.68rem] font-bold uppercase tracking-[0.12em]', checked ? 'accent-text' : 'text-[var(--ink-faint)]')}>
          {checked ? 'ON' : 'OFF'}
        </span>
        <span
          className={classNames('switch-track', checked && 'switch-track-on')}
          aria-hidden="true"
        >
          <span className="switch-knob" />
        </span>
      </span>
      <input
        aria-checked={checked}
        checked={checked}
        className="sr-only"
        disabled={disabled}
        id={id}
        onChange={(e) => onChange(e.target.checked)}
        role="switch"
        type="checkbox"
      />
    </label>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={classNames('instrument-panel rounded-2xl p-5', className)}>
      {children}
    </div>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: Array<{ value: T; label: ReactNode }>;
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0,1fr))` }}>
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            aria-pressed={active}
            type="button"
            onClick={() => onChange(opt.value)}
            className={classNames(
              'rounded-xl border px-3 py-2.5 text-xs font-bold transition',
              active
                ? 'accent-border bg-[var(--accent-soft)] accent-text shadow-sm'
                : 'border-[var(--line)] bg-[var(--canvas-well)] text-[var(--ink-muted)] hover:border-[var(--line-strong)]',
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
