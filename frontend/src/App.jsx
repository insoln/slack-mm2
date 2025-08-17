import { useEffect, useState } from 'react';
import './App.css';

function App() {
  const [status, setStatus] = useState('pending');
  const [error, setError] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [exportStatus, setExportStatus] = useState(null);
  const [exportError, setExportError] = useState(null);

  useEffect(() => {
    fetch('http://localhost:8000/healthcheck')
      .then((res) => {
        if (!res.ok) throw new Error('Network response was not ok');
        return res.json();
      })
      .then((data) => setStatus(data.status))
      .catch((err) => setError(err.message));
  }, []);

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
    if (!selectedFile) {
      setUploadError('Файл не выбран');
      return;
    }
    const formData = new FormData();
    formData.append('file', selectedFile);
    try {
      const xhr = new window.XMLHttpRequest();
      xhr.open('POST', 'http://localhost:8000/upload');
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          setUploadProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onload = () => {
        try {
          const data = JSON.parse(xhr.responseText);
          console.log('UPLOAD response:', data);
      if (data.error) {
        setUploadError(data.error);
        setUploadResult(null);
      } else {
        setUploadResult(data);
      }
        } catch (err) {
          console.error('UPLOAD parse error:', err);
          setUploadError('Ошибка парсинга ответа');
        }
        setUploadProgress(null);
      };
      xhr.onerror = () => {
        console.error('UPLOAD network error');
        setUploadError('Ошибка сети');
        setUploadProgress(null);
      };
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
      const response = await fetch('http://localhost:8000/export', {
        method: 'POST',
      });
      const data = await response.json();
      if (response.ok) {
        setExportStatus(data.message);
      } else {
        setExportError(data.error || 'Ошибка запуска экспорта');
      }
    } catch (err) {
      setExportError('Ошибка сети');
    }
  };

  return (
    <div className="App">
      <h1>Slack-MM2 Sync</h1>
      <p>
        Backend status: {error ? <span style={{color: 'red'}}>error: {error}</span> : status === 'ok' ? <span style={{color: 'green'}}>connected</span> : 'connecting...'}
      </p>
      <div style={{marginTop: 24}}>
        <h2>Загрузка бэкапа Slack</h2>
        <form onSubmit={handleSubmit}>
          <input type="file" accept=".zip" onChange={handleFileChange} />
          <button type="submit" style={{marginLeft: 8}}>Загрузить</button>
          {uploadProgress !== null && (
            <div style={{marginTop: 8, width: 300}}>
              <div style={{height: 16, background: '#eee', borderRadius: 8, overflow: 'hidden'}}>
                <div style={{width: `${uploadProgress}%`, height: '100%', background: '#4caf50', transition: 'width 0.2s'}} />
              </div>
              <div style={{fontSize: 12, marginTop: 2}}>{uploadProgress}%</div>
            </div>
          )}
        </form>
        {uploadResult && (
          <div style={{color: 'green', marginTop: 8}}>
            Файл {uploadResult.filename} загружен ({uploadResult.size} байт)
          </div>
        )}
        {uploadError && (
          <div style={{color: 'red', marginTop: 8}}>
            Ошибка загрузки: {uploadError}
          </div>
        )}
      </div>
      <div style={{marginTop: 24}}>
        <h2>Экспорт в Mattermost</h2>
        <button onClick={handleExport}>Запустить экспорт</button>
        {exportStatus && (
          <div style={{color: 'green', marginTop: 8}}>
            {exportStatus}
          </div>
        )}
        {exportError && (
          <div style={{color: 'red', marginTop: 8}}>
            Ошибка экспорта: {exportError}
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
