import { useEffect, useState } from 'react';
import './App.css';
import { Header, Sidebar, Main, Card, Button, StatusBadge, Modal, FileButton, Toasts } from './components/UI';
import './components/ui.css';

function App() {
  const [status, setStatus] = useState('pending');
  const [error, setError] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [exportStatus, setExportStatus] = useState(null);
  const [exportError, setExportError] = useState(null);
  const [plugin, setPlugin] = useState({loading: false, data: null, error: null});
  const [fixingPlugin, setFixingPlugin] = useState(false);
  const [stats, setStats] = useState({loading: false, data: null, error: null});
  const [liveStats, setLiveStats] = useState(null);
  const [jobs, setJobs] = useState({ loading: false, data: [], error: null });
  const [jobStats, setJobStats] = useState({}); // job_id -> { loading, data, error }
  const [expandedJobs, setExpandedJobs] = useState(() => new Set());
  const [toasts, setToasts] = useState([]);
  const [lastEnsureSuccessTs, setLastEnsureSuccessTs] = useState(null);

  // Deduplicating toast push (tone+title+message within last 5s)
  const pushToast = (t) => {
    const now = Date.now();
    setToasts((arr) => {
      const exists = arr.some(x => x.tone === (t.tone||'info') && x.title===t.title && x.message===t.message && (now - x._ts) < 5000);
      if (exists) return arr;
      const id = now + Math.random();
      const toast = { id, tone: 'info', timeout: 4000, _ts: now, ...t };
      if (toast.timeout) {
        setTimeout(() => {
          setToasts((cur) => cur.filter((x) => x.id !== id));
        }, toast.timeout);
      }
      return [...arr, toast];
    });
  };
  const closeToast = (id) => setToasts((arr) => arr.filter((t) => t.id !== id));

  useEffect(() => {
    fetch('/api/healthcheck')
      .then((res) => {
        if (!res.ok) throw new Error('Network response was not ok');
        return res.json();
      })
      .then((data) => setStatus(data.status))
      .catch((err) => setError(err.message));
  }, []);

  const refreshPluginStatus = async () => {
    setPlugin((s) => ({...s, loading: true, error: null}));
    try {
  const res = await fetch('/api/plugin/status');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to get plugin status');
      setPlugin({loading: false, data, error: null});
    } catch (e) {
      setPlugin({loading: false, data: null, error: e.message});
    }
  };

  useEffect(() => { refreshPluginStatus(); }, []);

  const needsPluginFix = !!(plugin?.data && (!plugin.data.installed || plugin.data.needs_update || !plugin.data.enabled));
  // Removed auto-install & auto-reload logic for clarity and predictability

  const refreshStats = async () => {
    setStats((s) => ({...s, loading: true, error: null}));
    try {
      const res = await fetch('/api/stats/mappings');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Не удалось получить статистику');
      setStats({loading: false, data, error: null});
    } catch (e) {
      setStats({loading: false, data: null, error: e.message});
    }
  };

  useEffect(() => { refreshStats(); }, []);

  // Poll jobs list periodically
  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        setJobs((s) => ({ ...s, loading: true }));
  const res = await fetch('/api/jobs');
        const data = await res.json();
        if (!mounted) return;
  if (!res.ok) throw new Error(data.error || 'Не удалось получить список задач');
        setJobs({ loading: false, data: data.jobs || [], error: null });
      } catch (e) {
        if (!mounted) return;
        setJobs({ loading: false, data: [], error: e.message });
      }
    };
    load();
    const t = setInterval(load, 3000);
    return () => { mounted = false; clearInterval(t); };
  }, []);

  // Subscribe to live progress via SSE
  useEffect(() => {
  const es = new EventSource('/api/progress/stream');
    es.addEventListener('stats', (e) => {
      try { setLiveStats(JSON.parse(e.data)); } catch { /* ignore parse error */ }
    });
    es.onerror = () => { /* ignore; browser will retry due to retry header */ };
    return () => es.close();
  }, []);

  // Fetch per-job mapping stats (filtered) for active/in-progress jobs
  useEffect(() => {
    const active = (jobs.data || []).filter(j => !['success','failed'].includes(j.status));
    if (active.length === 0) return;
    let cancelled = false;
    const fetchStatsFor = async (job) => {
      setJobStats(prev => ({ ...prev, [job.id]: { ...(prev[job.id]||{}), updating: true, error: null } }));
      try {
        const res = await fetch(`/api/stats/mappings?job_id=${job.id}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'stat error');
        if (!cancelled) setJobStats(prev => ({ ...prev, [job.id]: { updating: false, data, error: null } }));
      } catch (e) {
        if (!cancelled) setJobStats(prev => ({ ...prev, [job.id]: { ...(prev[job.id]||{}), updating: false, error: e.message } }));
      }
    };
    active.forEach(j => fetchStatsFor(j));
    return () => { cancelled = true; };
  }, [jobs]);

  const exportOrder = ['user','custom_emoji','attachment','channel','message','reaction'];
  const labelMap = { user: 'user', custom_emoji: 'custom_emoji', attachment: 'attachment', channel: 'channel', message: 'message', reaction: 'reaction' };


  const handleFileChange = (e) => {
    setUploadResult(null);
    setUploadError(null);
    const file = e.target.files[0] || null;
    setSelectedFile(file);
    if (file) {
      // Auto-start upload on selection
      doUpload(file);
      // Reset input so the same file can be re-selected later
      e.target.value = '';
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!selectedFile) { setUploadError('Файл не выбран'); return; }
    await doUpload(selectedFile);
  };

  const doUpload = async (file) => {
    setUploadResult(null);
    setUploadError(null);
    setUploadProgress(0);
    const formData = new FormData();
    formData.append('file', file);
    try {
      const xhr = new window.XMLHttpRequest();
  xhr.open('POST', '/api/upload');
      xhr.upload.onprogress = (event) => { if (event.lengthComputable) setUploadProgress(Math.round((event.loaded / event.total) * 100)); };
      xhr.onload = () => {
        const status = xhr.status;
        const text = xhr.responseText || '';
        let parsed = null;
        try { parsed = text ? JSON.parse(text) : null; } catch {/* swallow */}
        if (parsed) {
          if (parsed.error) { setUploadError(parsed.error); setUploadResult(null); }
          else { setUploadResult(parsed); }
        } else {
          if (status >= 200 && status < 300) {
            // Unexpected non-JSON success
            setUploadResult({ filename: file.name, size: file.size, raw: text || null, note: 'Non-JSON response' });
          } else {
            setUploadError(`Сервер вернул ${status}${text ? ': ' + text.slice(0,200) : ''}`);
          }
        }
        setUploadProgress(null);
      };
      xhr.onerror = () => { setUploadError('Ошибка сети'); setUploadProgress(null); };
      xhr.send(formData);
    } catch (err) {
      setUploadError(err.message);
      setUploadProgress(null);
    }
  };

  const handleExport = async () => {
    setExportStatus(null);
    setExportError(null);
    try {
  const response = await fetch('/api/export', { method: 'POST' });
      const data = await response.json();
      if (response.ok) setExportStatus(data.message); else setExportError(data.error || 'Ошибка запуска экспорта');
    } catch (err) { setExportError(err?.message || 'Ошибка сети'); }
  };

  const handleEnsurePlugin = async () => {
    setFixingPlugin(true);
    try {
      const res = await fetch('/api/plugin/ensure', { method: 'POST' });
      const data = await res.json();
      if (!res.ok || data.status === 'needs_bundle') {
        let msg = data.error || 'Ensure failed';
        if (data.status === 'needs_bundle') {
          msg = `Нужен bundle. ${data.hint || ''} ${data.expected_path ? 'Ожидаемый путь: ' + data.expected_path : ''}`;
        }
        pushToast({ tone: 'error', title: 'Ensure', message: msg.trim() });
        throw new Error(msg.trim());
      }
      pushToast({ tone: 'success', title: 'Ensure', message: 'Плагин установлен и включен' });
      setLastEnsureSuccessTs(Date.now());
    } catch (e) {
      setPlugin((s) => ({ ...s, error: e.message }));
    } finally {
      setFixingPlugin(false);
      await refreshPluginStatus();
    }
  };

  // Compute whether modal should be open (close shortly after a success if now healthy)
  const healthy = !!(plugin?.data && plugin.data.installed && plugin.data.enabled && !plugin.data.needs_update);
  const modalShouldBeOpen = (() => {
    if (!plugin.data) return false;
    if (!healthy) return needsPluginFix; // still needs fix
    if (lastEnsureSuccessTs && Date.now() - lastEnsureSuccessTs < 1200) return false; // auto-close after ~1.2s
    return needsPluginFix;
  })();

  return (
    <div className="app-shell">
      <Header title="Slack → Mattermost Importer" subtitle="Корпоративная панель управления" right={<StatusBadge status={error ? 'error' : status === 'ok' ? 'ok' : 'pending'} />} />
      <div className="layout">
        <Sidebar>
          <nav>
            <a href="#upload">Загрузка бэкапа</a>
            <a href="#stats">Статистика</a>
            <a href="#plugin" style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>
              <span>Плагин MM-Importer</span>
              {plugin.data && (
                (() => {
                  const bad = !plugin.data.installed || !plugin.data.enabled;
                  const warn = plugin.data.installed && plugin.data.enabled && (plugin.data.needs_update || !plugin.data.bundle_exists);
                  const txt = bad ? '✖' : warn ? '⚠' : 'OK';
                  const color = bad ? '#f87171' : warn ? '#f59e0b' : '#34d399';
                  return <span style={{fontSize:11, fontWeight:600, color, border:'1px solid var(--border)', padding:'2px 6px', borderRadius:6, background:'rgba(255,255,255,.04)'}}>{txt}</span>;
                })()
              )}
            </a>
            <a href="#export">Экспорт</a>
          </nav>
        </Sidebar>
        <Main>
          <div className="grid">
            <div id="upload" className="col" style={{gridColumn: 'span 7'}}>
              <Card title="Загрузка бэкапа Slack" actions={null}>
                <form onSubmit={handleSubmit} className="form-row">
                  <FileButton accept=".zip" onChange={handleFileChange} disabled={uploadProgress !== null}>
                    Выбрать архив .zip
                  </FileButton>
                </form>
                {uploadProgress !== null && (
                  <div style={{marginTop: 12, maxWidth: 360}}>
                    <div style={{height: 10, background: '#0b1223', border: '1px solid var(--border)', borderRadius: 9999, overflow: 'hidden'}}>
                      <div style={{width: `${uploadProgress}%`, height: '100%', background: 'linear-gradient(90deg, var(--primary), var(--primary-600))', transition: 'width 0.2s'}} />
                    </div>
                    <div className="small" style={{marginTop: 4}}>{uploadProgress}%</div>
                  </div>
                )}
                {uploadResult && <div style={{color: '#34d399', marginTop: 8}}>Файл {uploadResult.filename} загружен ({uploadResult.size} байт)</div>}
                {uploadError && <div style={{color: '#f87171', marginTop: 8}}>Ошибка загрузки: {uploadError}</div>}
              </Card>
            </div>
            <div id="plugin" className="col" style={{gridColumn: 'span 5'}}>
              <Card title="Статус плагина MM-Importer" actions={<div className="form-row"><Button onClick={refreshPluginStatus} disabled={plugin.loading} variant="secondary">Обновить</Button></div>}>
                {plugin.loading && <div>Загрузка статуса…</div>}
                {plugin.error && <div style={{color:'#f87171'}}>Ошибка: {plugin.error}</div>}
                {plugin.data && (
                  <div className="small" style={{lineHeight: 1.8}}>
                    <div>Plugin ID: <b>{plugin.data.plugin_id}</b></div>
                    <div>Ожидаемая версия: <b>{plugin.data.expected_version || 'n/a'}</b></div>
                    <div>Установлен: <b>{plugin.data.installed ? 'да' : 'нет'}</b></div>
                    <div>Включен: <b style={{color: plugin.data.enabled ? '#34d399' : '#f59e0b'}}>{plugin.data.enabled ? 'да' : 'нет'}</b></div>
                    <div>Текущая версия: <b>{plugin.data.installed_version || 'n/a'}</b></div>
                    <div>Нужен апдейт: <b style={{color: plugin.data.needs_update ? '#f59e0b' : undefined}}>{plugin.data.needs_update ? 'да' : 'нет'}</b></div>
                    <div>Локальный bundle: <b style={{color: plugin.data.bundle_exists ? '#34d399' : '#f87171'}}>{plugin.data.bundle_exists ? 'есть' : 'нет'}</b></div>
                    {plugin.data.bundle_exists && (
                      <div>
                        Hash: <span title={plugin.data.bundle_sha256 || ''}>{(plugin.data.bundle_sha256 || '').slice(0,12) || '—'}</span>
                        {' '}• возраст: {plugin.data.bundle_mtime ? Math.max(0, Math.round((Date.now()/1000 - plugin.data.bundle_mtime)/60)) : '—'} мин
                      </div>
                    )}
                  </div>
                )}
              </Card>
            </div>
            <div id="stats" className="col" style={{gridColumn: 'span 12'}}>
              <Card title="Статистика маппингов" actions={<Button onClick={refreshStats} variant="secondary">Обновить</Button>}>
                {/* Jobs list (all running/finished) */}
                <div style={{marginBottom: 12}}>
                  <div className="small" style={{marginBottom: 6, color:'#9ca3af'}}>Активные и последние задачи</div>
                  {jobs.error && <div style={{color:'#f87171'}}>Ошибка: {jobs.error}</div>}
                  {(!jobs.data || jobs.data.length === 0) && !jobs.loading && (
                    <div className="small" style={{color:'#9ca3af'}}>Задач нет</div>
                  )}
                  {jobs.data && jobs.data.length > 0 && (
                    <div style={{display:'grid', gap:8}}>
                      {jobs.data.map((j) => {
                        const meta = j.meta || {};
                        const singlePass = !!meta.single_pass;
                        // Fallback totals: if API did not provide, derive from SSE by_type snapshot
                        const fallbackTotals = liveStats?.by_type ? {
                          messages: liveStats.by_type.message || 0,
                          reactions: liveStats.by_type.reaction || 0,
                          attachments: liveStats.by_type.attachment || 0,
                          emojis: liveStats.by_type.custom_emoji || 0,
                        } : {};
                        const totals = meta.totals || fallbackTotals;
                        const processed = {
                          messages: meta.messages_processed || 0,
                          emojis: meta.emojis_processed || 0,
                          reactions: meta.reactions_processed || 0,
                          attachments: meta.attachments_processed || 0,
                        };
                        // New: parsed vs processed for reactions (backend may provide reactions_parsed)
                        // Import-stage file-based progress
                        const jsonTotal = Number(meta.json_files_total) || 0;
                        const jsonDone = Number(meta.json_files_processed) || 0;
                        const importStages = singlePass
                          ? ['extracting','users','channels','messages']
                          : ['extracting','users','channels','messages','emojis','reactions','attachments'];
                        const inImport = importStages.includes(j.current_stage);

                        // Per-element weighting across all mapping items for exporting/done
                        const keys = ['attachments','messages','reactions','emojis'];
                        const totalsSum = keys.reduce((acc, k) => acc + (Number(totals[k]) || 0), 0);
                        const processedSum = keys.reduce((acc, k) => {
                          const t = Number(totals[k]) || 0;
                          const p = Number(processed[k]) || 0;
                          return acc + Math.min(p, t);
                        }, 0);

                        let pct = 0;
                        if (inImport) {
                          if (singlePass) {
                            // In single-pass mode we weight by logical stages: users (1), channels (1), messages (bulk)
                            // Use file progress for coarse feedback until messages known.
                            if (j.current_stage === 'extracting') pct = 5; // small seed
                            else if (j.current_stage === 'users') pct = 15;
                            else if (j.current_stage === 'channels') pct = 25;
                            else if (j.current_stage === 'messages') {
                              if ((totals.messages || 0) > 0) {
                                const msgPct = Math.min(1, (processed.messages || 0) / (totals.messages || 1));
                                pct = 25 + Math.round(msgPct * 75);
                              } else if (jsonTotal > 0) {
                                // fallback to file fraction during early stream
                                pct = 25 + Math.round(Math.min(1, jsonDone / jsonTotal) * 75);
                              } else {
                                pct = 30; // early messages unknown
                              }
                            }
                          } else {
                            if (jsonTotal > 0) {
                              pct = Math.max(1, Math.min(100, Math.round((jsonDone / jsonTotal) * 100)));
                            } else if ((totals.messages || 0) > 0) {
                              pct = Math.max(1, Math.min(100, Math.round(((processed.messages || 0) / (totals.messages || 1)) * 100)));
                            } else {
                              pct = 1;
                            }
                          }
                        } else {
                          pct = totalsSum > 0 ? Math.round((processedSum / totalsSum) * 100) : 0;
                          if (totalsSum === 0 && j.current_stage === 'exporting') pct = 1;
                        }
                        // Choose bar color: green for import stages, themed primary for export/done
                        const barBg = inImport
                          ? 'linear-gradient(90deg, #22c55e, #16a34a)'
                          : 'linear-gradient(90deg, var(--primary), var(--primary-600))';
                        const expanded = expandedJobs.has(j.id);
                        const toggle = () => setExpandedJobs(prev => {
                          const next = new Set([...prev]);
                          if (next.has(j.id)) next.delete(j.id); else next.add(j.id);
                          return next;
                        });
                        return (
                          <div key={j.id} style={{border:'1px solid var(--border)', borderRadius:8, padding:8, transition:'background .2s'}}>
                            <div className="small" style={{display:'flex', justifyContent:'space-between', marginBottom:6}}>
                              <span style={{display:'flex', gap:8, alignItems:'center'}}>
                                <button
                                  onClick={toggle}
                                  style={{
                                    background:'none', border:'1px solid var(--border)', width:20, height:20, borderRadius:4,
                                    display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', color:'#9ca3af'
                                  }}
                                  title={expanded ? 'Свернуть' : 'Развернуть'}
                                >{expanded ? '−' : '+'}</button>
                                Задача #{j.id} — {j.current_stage || '—'} • {j.status}
                              </span>
                              <span>{new Date(j.created_at || Date.now()).toLocaleString()}</span>
                            </div>
                            <div style={{height: 8, background: '#0b1223', border: '1px solid var(--border)', borderRadius: 9999, overflow: 'hidden'}}>
                              <div style={{width: `${pct}%`, height: '100%', background: barBg, transition: 'width 0.3s'}} />
                            </div>
                            <div className="small" style={{marginTop: 4, color:'#9ca3af'}}>
                              {inImport ? (
                                jsonTotal > 0
                                  ? (<span>import files {jsonDone}/{jsonTotal}</span>)
                                  : ((totals.messages || 0) > 0
                                      ? (<span>import msgs {processed.messages}/{totals.messages || 0}</span>)
                                      : (<span>import scanning…</span>))
                              ) : (
                                <span>
                                  files {processed.attachments}/{totals.attachments || 0}, msgs {processed.messages}/{totals.messages || 0}, reactions {processed.reactions}/{totals.reactions || 0}
                                </span>
                              )}
                            </div>
                            {expanded && (
                              <div style={{marginTop:10}}>
                                {jobStats[j.id]?.error && <div className="tiny" style={{color:'#f87171'}}>Ошибка статистики: {jobStats[j.id].error}</div>}
                                {jobStats[j.id]?.data && (
                                  <div style={{overflowX:'auto', position:'relative'}}>
                                    {jobStats[j.id]?.updating && (
                                      <div style={{position:'absolute', top:2, right:4, fontSize:10, color:'#6b7280'}}>upd…</div>
                                    )}
                                    <table className="table tiny" style={{fontSize:11, transition:'opacity .15s'}}>
                                      <thead>
                                        <tr>
                                          <th style={{textAlign:'left'}}>Тип</th>
                                          {jobStats[j.id].data.statuses.map(s => <th key={s}>{s}</th>)}
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {exportOrder.filter(t => (jobStats[j.id].data.types||[]).includes(t)).map(t => {
                                          const row = jobStats[j.id].data.matrix[t] || {};
                                          return (
                                            <tr key={t}>
                                              <td style={{textAlign:'left'}}>{labelMap[t] || t}</td>
                                              {jobStats[j.id].data.statuses.map(s => <td key={s}>{row[s] || 0}</td>)}
                                            </tr>
                                          );
                                        })}
                                      </tbody>
                                    </table>
                                  </div>
                                )}
                                {!jobStats[j.id]?.data && !jobStats[j.id]?.error && (
                                  <div className="tiny" style={{color:'#9ca3af'}}>Загрузка…</div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
                {/* Live SSE summary hidden to reduce confusion; SSE kept for fallback totals */}
                {stats.loading && <div>Загрузка…</div>}
                {stats.error && <div style={{color:'#f87171'}}>Ошибка: {stats.error}</div>}
                {stats.data && (
                  <div style={{overflowX:'auto'}}>
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Тип</th>
                          {stats.data.statuses.map((s) => (<th key={s}>{s}</th>))}
                        </tr>
                      </thead>
                      <tbody>
                        {stats.data.types.map((t) => {
                          const row = stats.data.matrix[t] || {};
                          return (
                            <tr key={t}>
                              <td>{t}</td>
                              {stats.data.statuses.map((s) => (<td key={s}>{row[s] || 0}</td>))}
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>
            <div id="export" className="col" style={{gridColumn: 'span 12'}}>
              <Card title="Экспорт в Mattermost" actions={<Button onClick={handleExport}>Запустить экспорт</Button>}>
                {exportStatus && <div style={{color: '#34d399'}}>{exportStatus}</div>}
                {exportError && <div style={{color: '#f87171'}}>Ошибка экспорта: {exportError}</div>}
              </Card>
            </div>
          </div>
        </Main>
      </div>
      {/* Modal for plugin maintenance (manual actions only) */}
      <Modal
        open={modalShouldBeOpen}
        title="Плагин MM-Importer"
        width={640}
        actions={
          !healthy && (
            <Button onClick={handleEnsurePlugin} disabled={fixingPlugin}>
              {fixingPlugin ? <><span className="spinner" style={{marginRight:6}} />Ensure…</> : 'Ensure'}
            </Button>
          )
        }
      >
        <div className="small" style={{lineHeight: 1.8}}>
          <p>Плагин необходим для экспорта. Текущее состояние:</p>
          <ul style={{margin:'6px 0 12px 16px'}}>
            <li>Установлен: <b style={{color: plugin?.data?.installed ? '#34d399' : '#f87171'}}>{plugin?.data?.installed ? 'да' : 'нет'}</b></li>
            <li>Включен: <b style={{color: plugin?.data?.enabled ? '#34d399' : '#f59e0b'}}>{plugin?.data?.enabled ? 'да' : 'нет'}</b></li>
            <li>Нужен апдейт: <b style={{color: plugin?.data?.needs_update ? '#f59e0b' : '#9ca3af'}}>{plugin?.data?.needs_update ? 'да' : 'нет'}</b></li>
            <li>Bundle локально: <b style={{color: plugin?.data?.bundle_exists ? '#34d399' : '#f87171'}}>{plugin?.data?.bundle_exists ? 'есть' : 'нет'}</b></li>
            {plugin?.data?.bundle_exists && (
              <li>Hash: <span title={plugin.data.bundle_sha256 || ''}>{(plugin.data.bundle_sha256 || '').slice(0,12) || '—'}</span> • размер: {plugin.data.bundle_size ? (Math.round(plugin.data.bundle_size/1024)) + ' KB' : '—'}</li>
            )}
          </ul>
          {!plugin?.data?.bundle_exists && (
            <div style={{color:'#f87171'}}>
              <p style={{margin:'0 0 6px'}}>Bundle отсутствует. Соберите и повторите:</p>
              <code style={{fontSize:12, userSelect:'all', display:'block', background:'#0e1a33', padding:'6px 8px', borderRadius:6, border:'1px solid var(--border)'}}>docker compose -f infra/docker-compose.dev.yml up --build mm-plugin-build</code>
              <div style={{marginTop:8}}>
                <Button
                  variant="secondary"
                  onClick={() => {
                    const cmd = 'docker compose -f infra/docker-compose.dev.yml up --build mm-plugin-build';
                    navigator.clipboard.writeText(cmd).then(() => {
                      pushToast({ tone: 'info', title: 'Команда скопирована', message: 'Вставьте её в терминал для сборки bundle.' });
                    }).catch(() => {
                      pushToast({ tone: 'error', title: 'Не удалось скопировать', message: 'Скопируйте вручную.' });
                    });
                  }}
                  style={{fontSize:12}}
                >Скопировать команду</Button>
              </div>
            </div>
          )}
          <p style={{marginTop:8}}>Кнопка Ensure выполнит установку / обновление / включение при наличии bundle, иначе покажет подсказку.</p>
          {plugin.error && <p style={{color:'#f87171'}}>Ошибка: {plugin.error}</p>}
        </div>
      </Modal>
      <Toasts items={toasts} onClose={closeToast} />
    </div>
  );
}

export default App;
