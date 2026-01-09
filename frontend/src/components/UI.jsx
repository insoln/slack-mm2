import React from 'react';
import './ui.css';

export const Header = ({ title, subtitle, right }) => (
  <header className="hdr">
    <div className="hdr__brand">
      <div className="hdr__logo" aria-hidden />
      <div>
        <div className="hdr__title">{title}</div>
        {subtitle && <div className="hdr__subtitle">{subtitle}</div>}
      </div>
    </div>
    <div className="hdr__right">{right}</div>
  </header>
);

export const Sidebar = ({ children }) => (
  <aside className="sbar">{children}</aside>
);

export const Main = ({ children }) => (
  <main className="main">{children}</main>
);

export const Card = ({ title, actions, children }) => (
  <section className="card">
    <div className="card__head">
      <h3>{title}</h3>
      <div className="card__actions">{actions}</div>
    </div>
    <div className="card__body">{children}</div>
  </section>
);

export const Button = ({ variant = 'primary', children, ...rest }) => (
  <button className={`btn btn--${variant}`} {...rest}>{children}</button>
);

export const StatusBadge = ({ status }) => {
  const map = {
    ok: { text: 'Online', tone: 'success' },
    pending: { text: 'Pending', tone: 'warning' },
    error: { text: 'Error', tone: 'danger' },
    connected: { text: 'Online', tone: 'success' },
    offline: { text: 'Нет связи', tone: 'danger' },
  };
  const v = map[status] || { text: status, tone: 'neutral' };
  return <span className={`badge badge--${v.tone}`}>{v.text}</span>;
};

export const Modal = ({ open, title, children, actions, width = 520 }) => {
  if (!open) return null;
  return (
    <div className="modal">
      <div className="modal__backdrop" />
      <div className="modal__content" style={{ maxWidth: width }} role="dialog" aria-modal="true">
        <div className="modal__head">
          <h3>{title}</h3>
        </div>
        <div className="modal__body">{children}</div>
        {actions && <div className="modal__actions">{actions}</div>}
      </div>
    </div>
  );
};

// Simple toast stack; consumers render <Toasts items={array} onClose={(id)=>...} />
export const Toasts = ({ items, onClose }) => {
  if (!items || !items.length) return null;
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map(t => (
        <div key={t.id} className={`toast toast--${t.tone || 'info'}`}> 
          <div className="toast__body">
            {t.title && <div className="toast__title">{t.title}</div>}
            {t.message && <div className="toast__msg">{t.message}</div>}
          </div>
          <button className="toast__close" aria-label="Закрыть" onClick={() => onClose(t.id)}>×</button>
        </div>
      ))}
    </div>
  );
};

export const FileButton = ({ children = 'Выбрать файл', accept, onChange, disabled, variant = 'primary', ariaLabel }) => (
  <div className={`btn btn--${variant} filebtn ${disabled ? 'filebtn--disabled' : ''}`} aria-disabled={disabled ? 'true' : undefined}>
    <span className="filebtn__icon" aria-hidden>
      {/* upload icon */}
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 3v12m0-12 4 4m-4-4-4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M20 21H4a2 2 0 0 1-2-2v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    </span>
    <span>{children}</span>
    <input
      type="file"
      className="filebtn__input"
      accept={accept}
      onChange={onChange}
      disabled={disabled}
      aria-label={ariaLabel || (typeof children === 'string' ? children : 'Выбрать файл')}
    />
  </div>
);

/**
 * SegmentedProgressBar - A multi-status progress bar with colored segments
 * @param {Object} props
 * @param {number} props.success - Count of successful items
 * @param {number} props.failed - Count of failed items
 * @param {number} props.skipped - Count of skipped items
 * @param {number} props.pending - Count of pending items
 */
export const SegmentedProgressBar = ({ success = 0, failed = 0, skipped = 0, pending = 0 }) => {
  // Normalize counts into percentages so segments sum to 100
  const total = success + failed + skipped + pending;
  const normalize = (val) => total > 0 ? (val / total) * 100 : 0;
  
  const successPct = normalize(success);
  const failedPct = normalize(failed);
  const skippedPct = normalize(skipped);
  const pendingPct = normalize(pending);
  
  return (
    <div 
      style={{
        display: 'flex',
        height: 8,
        borderRadius: 9999,
        overflow: 'hidden',
        background: '#0b1223',
        border: '1px solid var(--border)'
      }}
      role="progressbar"
      // aria-valuenow intentionally excludes "pending" items, which represent unprocessed (incomplete) work
      aria-valuenow={Math.round(successPct + failedPct + skippedPct)}
      aria-valuemin="0"
      aria-valuemax="100"
    >
      {successPct > 0 && (
        <div 
          style={{ width: `${successPct}%`, background: '#22c55e' }}
          title={`Успешно: ${success} (${successPct.toFixed(1)}%)`}
        />
      )}
      {failedPct > 0 && (
        <div 
          style={{ width: `${failedPct}%`, background: '#f87171' }}
          title={`Ошибки: ${failed} (${failedPct.toFixed(1)}%)`}
        />
      )}
      {skippedPct > 0 && (
        <div 
          style={{ width: `${skippedPct}%`, background: '#6b7280' }}
          title={`Пропущено: ${skipped} (${skippedPct.toFixed(1)}%)`}
        />
      )}
      {pendingPct > 0 && (
        <div 
          style={{ width: `${pendingPct}%`, background: '#3b82f6' }}
          title={`Ожидает: ${pending} (${pendingPct.toFixed(1)}%)`}
        />
      )}
    </div>
  );
};
