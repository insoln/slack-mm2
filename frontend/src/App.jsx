import { useEffect, useState, useRef } from 'react';
import './App.css';
import { Header, Sidebar, Main, Card, Button, StatusBadge, Modal, FileButton, Toasts } from './components/UI';
import './components/ui.css';

function App() {
  const [status, setStatus] = useState('pending');
  const [error, setError] = useState(null);
  const [isOnline, setIsOnline] = useState(true);
  const [plugin, setPlugin] = useState({ loading: false, data: null, error: null });
  const [uploadProgress, setUploadProgress] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [exportStatus, setExportStatus] = useState(null);
  const [exportError, setExportError] = useState(null);
  const [stats, setStats] = useState({ loading:false, data:null, error:null });
  const [jobs, setJobs] = useState({ loading:false, data:[], error:null });
  const [jobStats, setJobStats] = useState({});
  const [expandedJobs, setExpandedJobs] = useState(()=>new Set());
  const [toasts, setToasts] = useState([]);
  const [fixingPlugin, setFixingPlugin] = useState(false);
  const [lastEnsureSuccessTs, setLastEnsureSuccessTs] = useState(null);
  const [liveStats, setLiveStats] = useState(null);

  // Export matrix ordering (kept consistent with backend exporter)
  const exportOrder = ['user','custom_emoji','attachment','channel','message','reaction'];
  const labelMap = { user:'user', custom_emoji:'custom_emoji', attachment:'attachment', channel:'channel', message:'message', reaction:'reaction' };

  // Toast helpers (deduplicated by tone+title+message within 5s)
  const pushToast = (t) => {
    const now = Date.now();
    setToasts(arr => {
      const exists = arr.some(x => x.tone === (t.tone||'info') && x.title===t.title && x.message===t.message && (now - x._ts) < 5000);
      if (exists) return arr;
      const id = now + Math.random();
      const toast = { id, tone: 'info', timeout: 4000, _ts: now, ...t };
      if (toast.timeout) setTimeout(()=> setToasts(cur => cur.filter(x => x.id !== id)), toast.timeout);
      return [...arr, toast];
    });
  };
  const closeToast = id => setToasts(arr => arr.filter(t => t.id !== id));

  // Network error handler - checks if error indicates connectivity issues
  const handleNetworkError = (error, context = '') => {
    const errorMessage = error.message || String(error);
    const isNetworkError = errorMessage.includes('Failed to fetch') || 
                          errorMessage.includes('NetworkError') ||
                          errorMessage.includes('fetch') ||
                          errorMessage === 'Failed to get plugin status' ||
                          errorMessage === 'Bad JSON plugin status response';
    
    if (isNetworkError) {
      setIsOnline(false);
      // Return a user-friendly error message
      return `Потеряна связь с сервером${context ? ` (${context})` : ''}`;
    }
    
    // For non-network errors, assume connection is working
    setIsOnline(true);
    return errorMessage;
  };

  // Network success handler - marks connection as working
  const handleNetworkSuccess = () => {
    if (!isOnline) {
      setIsOnline(true);
      // Show a toast when connection is restored
      pushToast({ tone: 'success', title: 'Соединение восстановлено', message: 'Связь с сервером восстановлена' });
    }
  };

  // Initial health check
  useEffect(() => {
    fetch('/api/healthcheck')
      .then(r => r.json())
      .then(d => {
        setStatus(d.status);
        handleNetworkSuccess();
      })
      .catch(e => {
        const friendlyError = handleNetworkError(e, 'проверка состояния');
        setError(friendlyError);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Plugin status
  const refreshPluginStatus = async () => {
    setPlugin(s => ({...s, loading: true, error: null}));
    try {
      const res = await fetch('/api/plugin/status');
      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { /* swallow parse error */ }
      if (!res.ok) {
        const errMsg = (data && data.error) || text.slice(0,200) || 'Failed to get plugin status';
        throw new Error(errMsg);
      }
      if (!data) {
        throw new Error('Bad JSON plugin status response');
      }
      setPlugin({loading: false, data, error: null});
      handleNetworkSuccess();
    } catch (e) {
      const friendlyError = handleNetworkError(e, 'статус плагина');
      setPlugin({ loading: false, data: null, error: friendlyError });
    }
  };
  useEffect(() => { refreshPluginStatus(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Mapping stats snapshot (manual refresh + initial auto)
  const refreshStats = async () => {
    setStats(s => ({...s, loading: true, error: null}));
    try {
      const r = await fetch('/api/stats/mappings');
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'stats');
      setStats({ loading: false, data: d, error: null });
      handleNetworkSuccess();
    } catch (e) {
      const friendlyError = handleNetworkError(e, 'статистика');
      setStats({ loading: false, data: null, error: friendlyError });
    }
  };
  useEffect(() => { refreshStats(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Jobs polling
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        setJobs(s => ({...s, loading: true}));
        const r = await fetch('/api/jobs');
        const d = await r.json();
        if (!active) return;
        if (!r.ok) throw new Error(d.error || 'jobs');
        setJobs({ loading: false, data: d.jobs || [], error: null });
        handleNetworkSuccess();
      } catch (e) {
        if (active) {
          const friendlyError = handleNetworkError(e, 'задачи');
          setJobs({ loading: false, data: [], error: friendlyError });
        }
      }
    };
    load();
    const t = setInterval(load, 3000);
    return () => { active = false; clearInterval(t); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // SSE progress (stats events only)
  useEffect(() => {
    const es = new EventSource('/api/progress/stream');
    es.addEventListener('stats', e => { try { setLiveStats(JSON.parse(e.data)); } catch { /* ignore */ } });
    return () => es.close();
  }, []);

  // Throttled per-job mapping stats polling.
  // Strategy:
  //  - Maintain lastFetch ts per job.
  //  - Running jobs: at most every 5s.
  //  - Success jobs: one final fetch after success, then continue only if pending remain, capped every 10s.
  // Interval-based scheduler for per-job stats (prevents render-triggered flooding)
  const jobsRef = useRef([]);
  const jobStatsRef = useRef({});
  const lastFetchRef = useRef({}); // job_id -> timestamp
  useEffect(() => { jobsRef.current = jobs.data || []; }, [jobs]);
  useEffect(() => { jobStatsRef.current = jobStats; }, [jobStats]);
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      const now = Date.now();
      const list = jobsRef.current;
      if (!list.length) return;
      // Build candidate list with throttling rules
      const candidates = [];
      for (const j of list) {
        if (j.status === 'failed') continue;
        const last = lastFetchRef.current[j.id] || 0;
        const age = now - last;
  const snap = jobStatsRef.current[j.id]?.data;
        const matrix = snap?.matrix || {};
        const hasPending = Object.values(matrix).some(row => (row?.pending||0) > 0);
        if (j.status === 'running') {
          if (age >= 5000) candidates.push(j);
        } else if (j.status === 'success') {
          if (!snap) candidates.push(j); // first post-success snapshot
          else if (hasPending && age >= 12000) candidates.push(j); // slower backoff
        }
      }
      if (!candidates.length) return;
      // Limit parallel fetches per tick to avoid spikes
      const batch = candidates.slice(0, 2);
      await Promise.all(batch.map(async (job) => {
        try {
          setJobStats(prev => ({ ...prev, [job.id]: { ...(prev[job.id]||{}), updating: true, error: null } }));
          const res = await fetch(`/api/stats/mappings?job_id=${job.id}`);
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || 'stat error');
          if (!cancelled) {
            setJobStats(prev => ({ ...prev, [job.id]: { updating: false, data, error: null } }));
            handleNetworkSuccess();
          }
        } catch (e) {
          if (!cancelled) {
            const friendlyError = handleNetworkError(e, 'статистика задачи');
            setJobStats(prev => ({ ...prev, [job.id]: { ...(prev[job.id]||{}), updating: false, error: friendlyError } }));
          }
        } finally {
          lastFetchRef.current[job.id] = Date.now();
        }
      }));
    };
    const interval = setInterval(tick, 2000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const healthy = !!(plugin?.data && plugin.data.installed && plugin.data.enabled && !plugin.data.needs_update);
  const needsPluginFix = !!(plugin?.data && (!plugin.data.installed || plugin.data.needs_update || !plugin.data.enabled));
  const modalShouldBeOpen = (() => {
    if (!plugin.data) return false;
    if (!healthy) return needsPluginFix;
    if (lastEnsureSuccessTs && Date.now() - lastEnsureSuccessTs < 1200) return false;
    return needsPluginFix;
  })();


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
      xhr.onerror = () => { 
        handleNetworkError(new Error('Failed to fetch'), 'загрузка файла');
        setUploadError('Потеряна связь с сервером при загрузке файла'); 
        setUploadProgress(null); 
      };
      xhr.send(formData);
    } catch (err) {
      const friendlyError = handleNetworkError(err, 'загрузка файла');
      setUploadError(friendlyError);
      setUploadProgress(null);
    }
  };

  const handleExport = async () => {
    setExportStatus(null);
    setExportError(null);
    try {
  const response = await fetch('/api/export', { method: 'POST' });
      const data = await response.json();
      if (response.ok) {
        setExportStatus(data.message);
        handleNetworkSuccess();
      } else {
        setExportError(data.error || 'Ошибка запуска экспорта');
      }
    } catch (err) { 
      const friendlyError = handleNetworkError(err, 'экспорт');
      setExportError(friendlyError); 
    }
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
      handleNetworkSuccess();
    } catch (e) {
      const friendlyError = handleNetworkError(e, 'установка плагина');
      setPlugin((s) => ({ ...s, error: friendlyError }));
    } finally {
      setFixingPlugin(false);
      await refreshPluginStatus();
    }
  };
  // Compute whether modal should be open (close shortly after a success if now healthy)
  // healthy / modal logic already computed above

  // Helper: (legacy detailed divergence UI removed – simplified progress)

  return (
    <div className="app-shell">
      <Header title="Slack → Mattermost Importer" subtitle="Корпоративная панель управления" right={<StatusBadge status={!isOnline ? 'offline' : error ? 'error' : status==='ok' ? 'ok':'pending'} />} />
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
            <div id="upload" className="col" style={{gridColumn:'span 7'}}>
              <Card title="Загрузка бэкапа Slack">
                <form onSubmit={handleSubmit} className="form-row">
                  <FileButton accept=".zip" onChange={handleFileChange} disabled={uploadProgress!==null}>Выбрать архив .zip</FileButton>
                </form>
                {uploadProgress!==null && (
                  <div style={{marginTop:12,maxWidth:360}}>
                    <div style={{height:10,background:'#0b1223',border:'1px solid var(--border)',borderRadius:9999,overflow:'hidden'}}>
                      <div style={{width:`${uploadProgress}%`,height:'100%',background:'linear-gradient(90deg,var(--primary),var(--primary-600))',transition:'width .2s'}} />
                    </div>
                    <div className="small" style={{marginTop:4}}>{uploadProgress}%</div>
                  </div>
                )}
                {uploadResult && <div style={{color:'#34d399',marginTop:8}}>Файл {uploadResult.filename} загружен ({uploadResult.size} байт)</div>}
                {uploadError && <div style={{color:'#f87171',marginTop:8}}>Ошибка загрузки: {uploadError}</div>}
              </Card>
            </div>
            <div id="plugin" className="col" style={{gridColumn:'span 5'}}>
              <Card title="Статус плагина MM-Importer" actions={<Button onClick={refreshPluginStatus} disabled={plugin.loading} variant="secondary">Обновить</Button>}>
                {plugin.loading && <div>Загрузка статуса…</div>}
                {plugin.error && <div style={{color:'#f87171'}}>Ошибка: {plugin.error}</div>}
                {plugin.data && (
                  <div className="small" style={{lineHeight:1.8}}>
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
            <div id="stats" className="col" style={{gridColumn:'span 12'}}>
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
                        const totalsFrozen = !!meta.totals_frozen;
                        const processed = {
                          messages: meta.messages_processed || 0,
                          emojis: meta.emojis_processed || 0,
                          reactions: meta.reactions_processed || 0,
                          attachments: meta.attachments_processed || 0,
                        };
                        // Parsed counters (backend may provide *_parsed keys); fall back to processed if absent
                        // Import-stage file-based progress
                        const jsonTotal = Number(meta.json_files_total) || 0;
                        const jsonDone = Number(meta.json_files_processed) || 0;
                        const importStages = singlePass
                          ? ['extracting','users','channels','messages']
                          : ['extracting','users','channels','messages','emojis','reactions','attachments'];
                        const inImport = importStages.includes(j.current_stage);

                        // Per-element weighting across all mapping items for exporting/done
                        // keys list removed (no longer needed for percentage calc)
                        // (totalsSum/processedSum removed – export phase now uses exporter matrix)

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
                          // EXPORTING / DONE — use exporter status matrix for real progress
                          const matrix = jobStats[j.id]?.data?.matrix || {};
                          // Compute success / total(exportable) counts across exportOrder
                          let successAll = 0; let totalAll = 0;
                          exportOrder.forEach(t => {
                            const row = matrix[t] || {};
                            const succ = Number(row.success || 0);
                            const pend = Number(row.pending || 0);
                            const fail = Number(row.failed || 0);
                            const skip = Number(row.skipped || 0);
                            const localTotal = succ + pend + fail + skip;
                            if (localTotal > 0) {
                              successAll += succ;
                              totalAll += localTotal;
                            }
                          });
                          if (j.status === 'success') {
                            pct = 100;
                          } else if (totalAll > 0) {
                            pct = Math.max(1, Math.min(100, Math.round((successAll / totalAll) * 100)));
                          } else {
                            pct = totalsFrozen ? 100 : 1;
                          }
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
                                  : (<span>{j.current_stage}…</span>)
                              ) : (() => {
                                const matrix = jobStats[j.id]?.data?.matrix || {};
                                const rowMsg = matrix.message || {};
                                const msgDone = Number(rowMsg.success || 0);
                                const msgTotal = msgDone + Number(rowMsg.pending || 0) + Number(rowMsg.failed || 0) + Number(rowMsg.skipped || 0);
                                const msgLabel = msgTotal > 0 ? `${msgDone}/${msgTotal}` : `${processed.messages}/${totals.messages || 0}`;
                                return <span>export msgs {msgLabel}</span>;
                              })()}
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
                {stats.loading && <div>Загрузка…</div>}
                {stats.error && <div style={{color:'#f87171'}}>Ошибка: {stats.error}</div>}
                {stats.data && (
                  <div style={{overflowX:'auto'}}>
                    <table className="table">
                      <thead><tr><th>Тип</th>{stats.data.statuses.map(s=> <th key={s}>{s}</th>)}</tr></thead>
                      <tbody>
                        {stats.data.types.map(t=>{ const row=stats.data.matrix[t]||{}; return <tr key={t}><td>{t}</td>{stats.data.statuses.map(s=> <td key={s}>{row[s]||0}</td>)}</tr>; })}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>
            <div id="export" className="col" style={{gridColumn:'span 12'}}>
              <Card title="Экспорт в Mattermost" actions={<Button onClick={handleExport}>Запустить экспорт</Button>}>
                {exportStatus && <div style={{color:'#34d399'}}>{exportStatus}</div>}
                {exportError && <div style={{color:'#f87171'}}>Ошибка экспорта: {exportError}</div>}
              </Card>
            </div>
          </div>
        </Main>
      </div>
      <PluginModal open={modalShouldBeOpen} healthy={healthy} fixingPlugin={fixingPlugin} handleEnsurePlugin={handleEnsurePlugin} plugin={plugin} pushToast={pushToast} />
      <Toasts items={toasts} onClose={closeToast} />
    </div>
  );
}

function JobsSection({ jobs, jobStats, liveStats, expandedJobs, setExpandedJobs }){
  if(jobs.error) return <div style={{color:'#f87171'}}>Ошибка: {jobs.error}</div>;
  if((!jobs.data||jobs.data.length===0) && !jobs.loading) return <div className="small" style={{color:'#9ca3af'}}>Задач нет</div>;
  if(!jobs.data || jobs.data.length===0) return null;
  const exportOrder=['user','custom_emoji','attachment','channel','message','reaction'];
  const labelMap={ user:'user', custom_emoji:'custom_emoji', attachment:'attachment', channel:'channel', message:'message', reaction:'reaction' };
  return (
    <div style={{marginBottom:12}}>
      <div className="small" style={{marginBottom:6,color:'#9ca3af'}}>Активные и последние задачи</div>
      <div style={{display:'grid',gap:8}}>
        {jobs.data.map(j=>{
          const meta=j.meta||{}; const singlePass=!!meta.single_pass;
            const fallbackTotals = liveStats?.by_type ? { messages: liveStats.by_type.message||0, reactions: liveStats.by_type.reaction||0, attachments: liveStats.by_type.attachment||0, emojis: liveStats.by_type.custom_emoji||0 } : {};
            const totals = meta.totals || fallbackTotals;
            const processed = { messages: meta.messages_processed||0, emojis: meta.emojis_processed||0, reactions: meta.reactions_processed||0, attachments: meta.attachments_processed||0 };
            const jsonTotal=Number(meta.json_files_total)||0; const jsonDone=Number(meta.json_files_processed)||0;
            const importStages = singlePass? ['extracting','users','channels','messages'] : ['extracting','users','channels','messages','emojis','reactions','attachments'];
            const inImport = importStages.includes(j.current_stage);
            const keys=['attachments','messages','reactions','emojis'];
            const totalsSum = keys.reduce((a,k)=>a+(Number(totals[k])||0),0);
            const processedSum = keys.reduce((a,k)=>{ const t=Number(totals[k])||0; const p=Number(processed[k])||0; return a+Math.min(p,t); },0);
            let pct=0; if(inImport){ if(singlePass){ if(j.current_stage==='extracting') pct=5; else if(j.current_stage==='users') pct=15; else if(j.current_stage==='channels') pct=25; else if(j.current_stage==='messages'){ if((totals.messages||0)>0){ const msgPct=Math.min(1,(processed.messages||0)/(totals.messages||1)); pct=25+Math.round(msgPct*75); } else if(jsonTotal>0){ pct=25+Math.round(Math.min(1,jsonDone/jsonTotal)*75);} else { pct=30;} } } else { if(jsonTotal>0) pct=Math.max(1,Math.min(100,Math.round((jsonDone/jsonTotal)*100))); else if((totals.messages||0)>0) pct=Math.max(1,Math.min(100,Math.round(((processed.messages||0)/(totals.messages||1))*100))); else pct=1; } } else { pct= totalsSum>0 ? Math.round((processedSum/totalsSum)*100):0; if(totalsSum===0 && j.current_stage==='exporting') pct=1; }
            const barBg=inImport?'linear-gradient(90deg,#22c55e,#16a34a)':'linear-gradient(90deg,var(--primary),var(--primary-600))';
            const expanded=expandedJobs.has(j.id);
            const toggle=()=>setExpandedJobs(prev=>{ const n=new Set(prev); if(n.has(j.id)) n.delete(j.id); else n.add(j.id); return n; });
            return (
              <div key={j.id} style={{border:'1px solid var(--border)',borderRadius:8,padding:8}}>
                <div className="small" style={{display:'flex',justifyContent:'space-between',marginBottom:6}}>
                  <span style={{display:'flex',gap:8,alignItems:'center'}}>
                    <button onClick={toggle} style={{background:'none',border:'1px solid var(--border)',width:20,height:20,borderRadius:4,display:'flex',alignItems:'center',justifyContent:'center',cursor:'pointer',color:'#9ca3af'}} title={expanded? 'Свернуть':'Развернуть'}>{expanded? '−':'+'}</button>
                    Задача #{j.id} — {j.current_stage || '—'} • {j.status}
                  </span>
                  <span>{new Date(j.created_at||Date.now()).toLocaleString()}</span>
                </div>
                <div style={{height:8,background:'#0b1223',border:'1px solid var(--border)',borderRadius:9999,overflow:'hidden'}}>
                  <div style={{width:`${pct}%`,height:'100%',background:barBg,transition:'width .3s'}} />
                </div>
                <div className="small" style={{marginTop:4,color:'#9ca3af'}}>
                  {inImport ? (
                    jsonTotal>0 ? <span>import files {jsonDone}/{jsonTotal}</span> : <span>import scanning…</span>
                  ) : (
                    <span>files {processed.attachments}/{totals.attachments||0}, msgs {processed.messages}/{totals.messages||0}, reactions {processed.reactions}/{totals.reactions||0}</span>
                  )}
                </div>
                {expanded && (
                  <div style={{marginTop:10}}>
                    {jobStats[j.id]?.error && <div className="tiny" style={{color:'#f87171'}}>Ошибка статистики: {jobStats[j.id].error}</div>}
                    {jobStats[j.id]?.data && (
                      <div style={{overflowX:'auto',position:'relative'}}>
                        {jobStats[j.id]?.updating && <div style={{position:'absolute',top:2,right:4,fontSize:10,color:'#6b7280'}}>upd…</div>}
                        <table className="table tiny" style={{fontSize:11}}>
                          <thead><tr><th style={{textAlign:'left'}}>Тип</th>{jobStats[j.id].data.statuses.map(s=> <th key={s}>{s}</th>)}</tr></thead>
                          <tbody>
                            {exportOrder.filter(t=>(jobStats[j.id].data.types||[]).includes(t)).map(t=>{ const row=jobStats[j.id].data.matrix[t]||{}; return <tr key={t}><td style={{textAlign:'left'}}>{labelMap[t]||t}</td>{jobStats[j.id].data.statuses.map(s=> <td key={s}>{row[s]||0}</td>)}</tr>; })}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {!jobStats[j.id]?.data && !jobStats[j.id]?.error && <div className="tiny" style={{color:'#9ca3af'}}>Загрузка…</div>}
                  </div>
                )}
              </div>
            );
          })}
      </div>
    </div>
  );
}

function PluginModal({ open, healthy, fixingPlugin, handleEnsurePlugin, plugin, pushToast }){
  if(!open) return null;
  return (
    <Modal open={open} title="Плагин MM-Importer" width={640}
      actions={!healthy && (<Button onClick={handleEnsurePlugin} disabled={fixingPlugin}>{fixingPlugin ? <><span className="spinner" style={{marginRight:6}} />Ensure…</> : 'Ensure'}</Button>)}>
      <div className="small" style={{lineHeight:1.8}}>
        <p>Плагин необходим для экспорта. Текущее состояние:</p>
        <ul style={{margin:'6px 0 12px 16px'}}>
          <li>Установлен: <b style={{color: plugin?.data?.installed ? '#34d399' : '#f87171'}}>{plugin?.data?.installed ? 'да' : 'нет'}</b></li>
          <li>Включен: <b style={{color: plugin?.data?.enabled ? '#34d399' : '#f59e0b'}}>{plugin?.data?.enabled ? 'да' : 'нет'}</b></li>
          <li>Нужен апдейт: <b style={{color: plugin?.data?.needs_update ? '#f59e0b' : '#9ca3af'}}>{plugin?.data?.needs_update ? 'да' : 'нет'}</b></li>
          <li>Bundle локально: <b style={{color: plugin?.data?.bundle_exists ? '#34d399' : '#f87171'}}>{plugin?.data?.bundle_exists ? 'есть' : 'нет'}</b></li>
          {plugin?.data?.bundle_exists && <li>Hash: <span title={plugin.data.bundle_sha256 || ''}>{(plugin.data.bundle_sha256 || '').slice(0,12) || '—'}</span> • размер: {plugin.data.bundle_size ? (Math.round(plugin.data.bundle_size/1024)) + ' KB' : '—'}</li>}
        </ul>
        {!plugin?.data?.bundle_exists && (
          <div style={{color:'#f87171'}}>
            <p style={{margin:'0 0 6px'}}>Bundle отсутствует. Соберите и повторите:</p>
            <code style={{fontSize:12,userSelect:'all',display:'block',background:'#0e1a33',padding:'6px 8px',borderRadius:6,border:'1px solid var(--border)'}}>docker compose -f infra/docker-compose.dev.yml up --build mm-plugin-build</code>
            <div style={{marginTop:8}}>
              <Button variant="secondary" onClick={()=>{ const cmd='docker compose -f infra/docker-compose.dev.yml up --build mm-plugin-build'; navigator.clipboard.writeText(cmd).then(()=>{ pushToast({tone:'info',title:'Команда скопирована',message:'Вставьте её в терминал для сборки bundle.'}); }).catch(()=>{ pushToast({tone:'error',title:'Не удалось скопировать',message:'Скопируйте вручную.'}); }); }} style={{fontSize:12}}>Скопировать команду</Button>
            </div>
          </div>
        )}
        <p style={{marginTop:8}}>Кнопка Ensure выполнит установку / обновление / включение при наличии bundle, иначе покажет подсказку.</p>
        {plugin.error && <p style={{color:'#f87171'}}>Ошибка: {plugin.error}</p>}
      </div>
    </Modal>
  );
}

export default App;
