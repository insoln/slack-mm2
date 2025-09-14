import { useEffect, useState } from 'react';
import './App.css';
import { Header, Sidebar, Main, Card, Button, StatusBadge, Modal, FileButton, Toasts } from './components/UI';
import './components/ui.css';

function App() {
  const [status, setStatus] = useState('pending');
  const [error, setError] = useState(null);
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

  useEffect(()=>{ fetch('/api/healthcheck').then(r=>r.json()).then(d=>setStatus(d.status)).catch(e=>setError(e.message)); },[]);
  const refreshPluginStatus = async ()=>{ setPlugin(s=>({...s,loading:true,error:null})); try{ const r=await fetch('/api/plugin/status'); const d=await r.json(); if(!r.ok) throw new Error(d.error||'plugin status'); setPlugin({loading:false,data:d,error:null}); }catch(e){ setPlugin({loading:false,data:null,error:e.message}); } };
  useEffect(()=>{ refreshPluginStatus(); },[]);
  const refreshStats = async ()=>{ setStats(s=>({...s,loading:true,error:null})); try{ const r=await fetch('/api/stats/mappings'); const d=await r.json(); if(!r.ok) throw new Error(d.error||'stats'); setStats({loading:false,data:d,error:null}); }catch(e){ setStats({loading:false,data:null,error:e.message}); } };
  useEffect(()=>{ refreshStats(); },[]);
  useEffect(()=>{ let m=true; const load=async()=>{ try{ setJobs(s=>({...s,loading:true})); const r=await fetch('/api/jobs'); const d=await r.json(); if(!m) return; if(!r.ok) throw new Error(d.error||'jobs'); setJobs({loading:false,data:d.jobs||[],error:null}); }catch(e){ if(m) setJobs({loading:false,data:[],error:e.message}); } }; load(); const t=setInterval(load,3000); return ()=>{ m=false; clearInterval(t); }; },[]);
  useEffect(()=>{ const es=new EventSource('/api/progress/stream'); es.addEventListener('stats',e=>{ try{ setLiveStats(JSON.parse(e.data)); }catch{ /* ignore malformed stats event */ } }); return ()=>es.close(); },[]);
  useEffect(()=>{ const active=(jobs.data||[]).filter(j=>!['success','failed'].includes(j.status)); if(active.length===0) return; let cancelled=false; active.forEach(job=>{ (async()=>{ setJobStats(p=>({...p,[job.id]:{...(p[job.id]||{}),updating:true,error:null}})); try{ const r=await fetch(`/api/stats/mappings?job_id=${job.id}`); const d=await r.json(); if(!r.ok) throw new Error(d.error||'stat'); if(!cancelled) setJobStats(p=>({...p,[job.id]:{updating:false,data:d,error:null}})); }catch(e){ if(!cancelled) setJobStats(p=>({...p,[job.id]:{...(p[job.id]||{}),updating:false,error:e.message}})); } })(); }); return ()=>{ cancelled=true; }; },[jobs]);

  const pushToast = (t)=>{ const now=Date.now(); setToasts(a=>{ const exists=a.some(x=>x.tone===(t.tone||'info')&&x.title===t.title&&x.message===t.message&&(now-x._ts)<5000); if(exists) return a; const id=now+Math.random(); const toast={id,tone:'info',timeout:4000,_ts:now,...t}; if(toast.timeout) setTimeout(()=>setToasts(c=>c.filter(x=>x.id!==id)),toast.timeout); return [...a,toast]; }); };
  const closeToast = id => setToasts(a=>a.filter(x=>x.id!==id));

  const handleFileChange = (e)=>{ setUploadResult(null); setUploadError(null); const f=e.target.files[0]||null; setSelectedFile(f); if(f){ doUpload(f); e.target.value=''; } };
  const handleSubmit = (e)=>{ e.preventDefault(); if(!selectedFile){ setUploadError('Файл не выбран'); return;} doUpload(selectedFile); };
  const doUpload = (file)=>{ setUploadResult(null); setUploadError(null); setUploadProgress(0); const fd=new FormData(); fd.append('file',file); try{ const xhr=new XMLHttpRequest(); xhr.open('POST','/api/upload'); xhr.upload.onprogress=ev=>{ if(ev.lengthComputable) setUploadProgress(Math.round((ev.loaded/ev.total)*100)); }; xhr.onload=()=>{ const s=xhr.status; const text=xhr.responseText||''; let parsed=null; try{ parsed=text?JSON.parse(text):null;}catch{ /* ignore parse error */ } if(parsed){ if(parsed.error){ setUploadError(parsed.error);} else setUploadResult(parsed); } else { if(s>=200&&s<300) setUploadResult({filename:file.name,size:file.size,raw:text||null,note:'Non-JSON response'}); else setUploadError(`Сервер вернул ${s}${text?': '+text.slice(0,200):''}`); } setUploadProgress(null); }; xhr.onerror=()=>{ setUploadError('Ошибка сети'); setUploadProgress(null); }; xhr.send(fd); }catch(e){ setUploadError(e.message); setUploadProgress(null); } };
  const handleExport = async()=>{ setExportStatus(null); setExportError(null); try{ const r=await fetch('/api/export',{method:'POST'}); const d=await r.json(); if(r.ok) setExportStatus(d.message); else setExportError(d.error||'Ошибка запуска экспорта'); }catch(e){ setExportError(e.message); } };
  const handleEnsurePlugin = async()=>{ setFixingPlugin(true); try{ const r=await fetch('/api/plugin/ensure',{method:'POST'}); const d=await r.json(); if(!r.ok || d.status==='needs_bundle'){ let msg=d.error||'Ensure failed'; if(d.status==='needs_bundle') msg=`Нужен bundle. ${d.hint||''} ${d.expected_path? 'Ожидаемый путь: '+d.expected_path:''}`; pushToast({tone:'error',title:'Ensure',message:msg}); throw new Error(msg); } pushToast({tone:'success',title:'Ensure',message:'Плагин установлен и включен'}); setLastEnsureSuccessTs(Date.now()); }catch(e){ setPlugin(s=>({...s,error:e.message})); }finally{ setFixingPlugin(false); await refreshPluginStatus(); } };
  const healthy = !!(plugin?.data && plugin.data.installed && plugin.data.enabled && !plugin.data.needs_update);
  const needsPluginFix = !!(plugin?.data && (!plugin.data.installed || plugin.data.needs_update || !plugin.data.enabled));
  const modalShouldBeOpen = (()=>{ if(!plugin.data) return false; if(!healthy) return needsPluginFix; if(lastEnsureSuccessTs && Date.now()-lastEnsureSuccessTs<1200) return false; return needsPluginFix; })();

  return (
    <div className="app-shell">
      <Header title="Slack → Mattermost Importer" subtitle="Корпоративная панель управления" right={<StatusBadge status={error ? 'error' : status==='ok' ? 'ok':'pending'} />} />
      <div className="layout">
        <Sidebar>
          <nav>
            <a href="#upload">Загрузка бэкапа</a>
            <a href="#stats">Статистика</a>
            <a href="#plugin" style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
              <span>Плагин MM-Importer</span>
              {plugin.data && (()=>{ const bad=!plugin.data.installed||!plugin.data.enabled; const warn=plugin.data.installed&&plugin.data.enabled&&(plugin.data.needs_update||!plugin.data.bundle_exists); const txt=bad?'✖':warn?'⚠':'OK'; const color=bad?'#f87171':warn?'#f59e0b':'#34d399'; return <span style={{fontSize:11,fontWeight:600,color,border:'1px solid var(--border)',padding:'2px 6px',borderRadius:6,background:'rgba(255,255,255,.04)'}}>{txt}</span>; })()}
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
                    <div>Ожидаемая версия: <b>{plugin.data.expected_version||'n/a'}</b></div>
                    <div>Установлен: <b>{plugin.data.installed?'да':'нет'}</b></div>
                    <div>Включен: <b style={{color: plugin.data.enabled? '#34d399':'#f59e0b'}}>{plugin.data.enabled?'да':'нет'}</b></div>
                    <div>Текущая версия: <b>{plugin.data.installed_version||'n/a'}</b></div>
                    <div>Нужен апдейт: <b style={{color: plugin.data.needs_update? '#f59e0b':undefined}}>{plugin.data.needs_update?'да':'нет'}</b></div>
                    <div>Локальный bundle: <b style={{color: plugin.data.bundle_exists? '#34d399':'#f87171'}}>{plugin.data.bundle_exists?'есть':'нет'}</b></div>
                    {plugin.data.bundle_exists && (<div>Hash: <span title={plugin.data.bundle_sha256||''}>{(plugin.data.bundle_sha256||'').slice(0,12)||'—'}</span> • возраст: {plugin.data.bundle_mtime? Math.max(0,Math.round((Date.now()/1000 - plugin.data.bundle_mtime)/60)):'—'} мин</div>)}
                  </div>
                )}
              </Card>
            </div>
            <div id="stats" className="col" style={{gridColumn:'span 12'}}>
              <Card title="Статистика маппингов" actions={<Button onClick={refreshStats} variant="secondary">Обновить</Button>}>
                <JobsSection jobs={jobs} jobStats={jobStats} liveStats={liveStats} expandedJobs={expandedJobs} setExpandedJobs={setExpandedJobs} />
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
                    jsonTotal>0 ? <span>import files {jsonDone}/{jsonTotal}</span> : ((totals.messages||0)>0 ? <span>import msgs {processed.messages}/{totals.messages||0}</span> : <span>import scanning…</span>)
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
