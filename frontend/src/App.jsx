import { useEffect, useState } from 'react';
import './App.css';
import { Header, Sidebar, Main, Card, Button, StatusBadge } from './components/UI';
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
    setSelectedFile(e.target.files[0] || null);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setUploadResult(null);
    setUploadError(null);
    setUploadProgress(null);
    if (!selectedFile) { setUploadError('Файл не выбран'); return; }
    const formData = new FormData();
    formData.append('file', selectedFile);
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
    } catch (err) { setUploadError(err.message); setUploadProgress(null); }
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

  const handleDeployPlugin = async () => {
    try {
      const res = await fetch('http://localhost:8000/plugin/deploy', {method: 'POST'});
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Deploy failed');
      await refreshPluginStatus();
    } catch (e) { setPlugin((s) => ({...s, error: e.message})); }
  };

  const handleEnablePlugin = async () => {
    try {
      const res = await fetch('http://localhost:8000/plugin/enable', {method: 'POST'});
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Enable failed');
      await refreshPluginStatus();
    } catch (e) { setPlugin((s) => ({...s, error: e.message})); }
  };

  const handleEnsurePlugin = async () => {
    setPlugin((s) => ({...s, loading: true, error: null}));
    try {
      const res = await fetch('http://localhost:8000/plugin/ensure', {method: 'POST'});
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Ensure failed');
    } catch (e) { setPlugin((s) => ({...s, error: e.message})); }
    finally { await refreshPluginStatus(); }
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
                  <input type="file" className="input" accept=".zip" onChange={handleFileChange} />
                  <Button type="submit">Загрузить</Button>
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
              <Card title="Статус плагина MM-Importer" actions={
                <div className="form-row">
                  <Button onClick={refreshPluginStatus} disabled={plugin.loading} variant="secondary">Обновить</Button>
                  <Button onClick={handleDeployPlugin} disabled={plugin.loading}>Залить/обновить</Button>
                  <Button onClick={handleEnablePlugin} disabled={plugin.loading}>Включить</Button>
                  <Button onClick={handleEnsurePlugin} disabled={plugin.loading}>Автоустановка</Button>
                </div>
              }>
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
    </div>
  );
}

export default App;
