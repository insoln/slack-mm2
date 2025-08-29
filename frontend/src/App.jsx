import { useEffect, useState } from 'react';
import './App.css';
import { Header, Sidebar, Main, Card, Button, StatusBadge, Modal, FileButton } from './components/UI';
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
  const [installingPlugin, setInstallingPlugin] = useState(false);
  const [reloadCountdown, setReloadCountdown] = useState(5);
  const [installSession, setInstallSession] = useState(false);
  const [stats, setStats] = useState({loading: false, data: null, error: null});
  const [liveStats, setLiveStats] = useState(null);
  const [jobs, setJobs] = useState({ loading: false, data: [], error: null });

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
  const pluginNotInstalled = !!(plugin?.data && !plugin.data.installed);
  // Auto-ensure flow: if page opens while plugin is not installed, start ensure in background and show blocking modal
  useEffect(() => {
    const run = async () => {
      if (!pluginNotInstalled || installingPlugin) return;
      setInstallingPlugin(true);
      setInstallSession(true);
      try {
  await fetch('/api/plugin/ensure', { method: 'POST' });
  } catch {
        // swallow; UI will fall back to actionable modal
      } finally {
        // poll status a few times while installing (fetch fresh each attempt)
        let attempts = 0;
        const maxAttempts = 6; // ~30s with 5s interval
        while (attempts < maxAttempts) {
          // wait 5s
          await new Promise(r => setTimeout(r, 5000));
          try {
            const res = await fetch('/api/plugin/status');
            const data = await res.json();
            setPlugin({ loading: false, data, error: null });
            if (data && data.installed && data.enabled && !data.needs_update) {
              break;
            }
          } catch {
            // ignore
          }
          attempts += 1;
        }
        setInstallingPlugin(false);
      }
    };
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pluginNotInstalled]);

  // When install completed, schedule auto-reload after a short countdown to close the modal and refresh state
  useEffect(() => {
    if (!installSession || installingPlugin) return;
    const ok = plugin?.data && plugin.data.installed && plugin.data.enabled && !plugin.data.needs_update;
    if (!ok) return;
    setReloadCountdown(5);
    const timer = setInterval(() => {
      setReloadCountdown((n) => {
        if (n <= 1) {
          clearInterval(timer);
          window.location.reload();
          return 0;
        }
        return n - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [installSession, installingPlugin, plugin]);

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
        try {
          const data = JSON.parse(xhr.responseText);
          if (data.error) { setUploadError(data.error); setUploadResult(null); }
          else { setUploadResult(data); }
        } catch { setUploadError('Ошибка парсинга ответа'); }
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

  const handleFixPlugin = async () => {
    setFixingPlugin(true);
    try {
  const res = await fetch('/api/plugin/ensure', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Ensure failed');
    } catch (e) {
      setPlugin((s) => ({ ...s, error: e.message }));
    } finally {
      setFixingPlugin(false);
      await refreshPluginStatus();
    }
  };

  return (
    <div className="app-shell">
      <Header title="Slack → Mattermost Importer" subtitle="Корпоративная панель управления" right={<StatusBadge status={error ? 'error' : status === 'ok' ? 'ok' : 'pending'} />} />
      <div className="layout">
        <Sidebar>
          <nav>
            <a href="#upload">Загрузка бэкапа</a>
            <a href="#stats">Статистика</a>
            <a href="#plugin">Плагин MM-Importer</a>
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
                        // Import-stage file-based progress
                        const jsonTotal = Number(meta.json_files_total) || 0;
                        const jsonDone = Number(meta.json_files_processed) || 0;
                        const importStages = ['extracting','users','channels','messages','emojis','reactions','attachments'];
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
                          if (jsonTotal > 0) {
                            pct = Math.max(1, Math.min(100, Math.round((jsonDone / jsonTotal) * 100)));
                          } else if ((totals.messages || 0) > 0) {
                            // Fallback: approximate import progress by messages parsed
                            pct = Math.max(1, Math.min(100, Math.round(((processed.messages || 0) / (totals.messages || 1)) * 100)));
                          } else {
                            // Unknown totals yet: show a minimal indeterminate stub
                            pct = 1;
                          }
                        } else {
                          pct = totalsSum > 0 ? Math.round((processedSum / totalsSum) * 100) : 0;
                          if (totalsSum === 0 && j.current_stage === 'exporting') pct = 1;
                        }
                        // Choose bar color: green for import stages, themed primary for export/done
                        const barBg = inImport
                          ? 'linear-gradient(90deg, #22c55e, #16a34a)'
                          : 'linear-gradient(90deg, var(--primary), var(--primary-600))';
                        return (
                          <div key={j.id} style={{border:'1px solid var(--border)', borderRadius:8, padding:8}}>
                            <div className="small" style={{display:'flex', justifyContent:'space-between', marginBottom:6}}>
                              <span>Задача #{j.id} — {j.current_stage || '—'} • {j.status}</span>
                              <span>{new Date(j.created_at || Date.now()).toLocaleString()}</span>
                            </div>
                            <div style={{height: 8, background: '#0b1223', border: '1px solid var(--border)', borderRadius: 9999, overflow: 'hidden'}}>
                              <div style={{width: `${pct}%`, height: '100%', background: barBg, transition: 'width 0.3s'}} />
                            </div>
                            <div className="small" style={{marginTop: 4, color:'#9ca3af'}}>
                              {inImport
                                ? (jsonTotal > 0
                                  ? (<span>import files {jsonDone}/{jsonTotal}</span>)
                                  : ((totals.messages || 0) > 0
                                      ? (<span>import msgs {processed.messages}/{totals.messages || 0}</span>)
                                      : (<span>import scanning…</span>)))
                                : (<span>files {processed.attachments}/{totals.attachments || 0}, msgs {processed.messages}/{totals.messages || 0}, reactions {processed.reactions}/{totals.reactions || 0}</span>)}
                            </div>
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
      {/* Blocking modal during auto-install (no actions) */}
      <Modal
        open={!!plugin.data && installingPlugin}
        title="Устанавливается плагин MM-Importer"
        width={560}
        actions={null}
      >
        <div className="small" style={{lineHeight: 1.8}}>
          <p>Подождите, плагин устанавливается… Это может занять до 30 секунд.</p>
          <p>После завершения страница перезагрузится автоматически{reloadCountdown > 0 ? ` через ${reloadCountdown} с` : ''}.</p>
        </div>
      </Modal>

      {/* Blocking modal for plugin issues with action button */}
      <Modal
        open={!!plugin.data && needsPluginFix && !installingPlugin}
        title="Требуется действие: плагин MM-Importer"
        width={640}
        actions={
          <>
            <Button onClick={handleFixPlugin} disabled={fixingPlugin}>
              {fixingPlugin ? 'Исправляю…' : 'Сделать хорошо'}
            </Button>
          </>
        }
      >
        <div className="small" style={{lineHeight: 1.8}}>
          <p>
            Для работы импорта необходим плагин MM-Importer.
            Сейчас состояние: установлен — <b>{plugin?.data?.installed ? 'да' : 'нет'}</b>,
            включен — <b>{plugin?.data?.enabled ? 'да' : 'нет'}</b>,
            нуждается в обновлении — <b>{plugin?.data?.needs_update ? 'да' : 'нет'}</b>.
          </p>
          <p>
            Нажмите «Сделать хорошо», чтобы выполнить автоустановку/обновление и включение плагина.
          </p>
        </div>
      </Modal>
    </div>
  );
}

export default App;
