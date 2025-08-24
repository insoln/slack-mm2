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
