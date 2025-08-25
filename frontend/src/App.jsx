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

  useEffect(() => {
    fetch('http://localhost:8000/healthcheck')
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
      const res = await fetch('http://localhost:8000/plugin/status');
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
        await fetch('http://localhost:8000/plugin/ensure', { method: 'POST' });
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
            const res = await fetch('http://localhost:8000/plugin/status');
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
      const res = await fetch('http://localhost:8000/stats/mappings');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Не удалось получить статистику');
      setStats({loading: false, data, error: null});
    } catch (e) {
      setStats({loading: false, data: null, error: e.message});
    }
  };

  useEffect(() => { refreshStats(); }, []);

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
      xhr.open('POST', 'http://localhost:8000/upload');
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
      const response = await fetch('http://localhost:8000/export', { method: 'POST' });
      const data = await response.json();
      if (response.ok) setExportStatus(data.message); else setExportError(data.error || 'Ошибка запуска экспорта');
    } catch (err) { setExportError(err?.message || 'Ошибка сети'); }
  };

  const handleFixPlugin = async () => {
    setFixingPlugin(true);
    try {
      const res = await fetch('http://localhost:8000/plugin/ensure', { method: 'POST' });
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
