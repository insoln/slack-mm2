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
