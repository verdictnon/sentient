/* Local video, exact indexed PTS, editable image geometry and independent review. */
const $=id=>document.getElementById(id);
const state={demoVideoIds:[],pickupEvents:[],video:null,geometry:null,geometryId:null,selectedShape:null,tool:null,points:[],drag:null,frame:0,analysis:null,events:[],target:null,revision:0,editing:false,showZones:false,loading:false,selection:0,analysisNavigation:0,reviewNavigation:0,navigation:0};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error)}
async function api(url,options={}){const response=await fetch(url,options);if(!response.ok){let error;try{error=await response.json()}catch{error={detail:response.statusText}}throw Error(typeof error.detail==='string'?error.detail:JSON.stringify(error.detail))}return response.json()}
function jsonOptions(method,body){return {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}}
function guard(fn){return async(...args)=>{try{await fn(...args)}catch(error){notice(error.message,true)}}}
const sourceMs=()=>state.video?$('player').currentTime*1000:0;
function formatMs(ms){return (Number(ms)/1000).toFixed(3)}
function overlayVisibility(){
  const visible=!!state.video&&!!state.geometry&&state.showZones;
  $('overlay').toggleAttribute('hidden',!visible);
  $('overlay').classList.toggle('editing',visible&&state.editing);
  $('zones-toggle').textContent=visible?'Hide zones':'Show zones';
  $('zones-toggle').setAttribute('aria-pressed',String(visible));
  $('stage-label').textContent=visible?'FIXED SCENE GUIDES · NOT PERSON TRACKS':'RECORDED · SOURCE FOOTAGE';
}
function panel(name){
  $('track-panel').hidden=name!=='tracks';$('inspector-tracks').classList.toggle('active',name==='tracks');
  for(const section of ['playback','geometry','review']){
    $(section+'-panel').hidden=name!==section;
    $('inspector-'+section).classList.toggle('active',name===section);
  }
  for(const [id,section] of [['workspace-tab','playback'],['geometry-tab','geometry'],['review-tab','review']])$(id).classList.toggle('active',name===section);
  state.editing=name==='geometry';state.showZones=state.editing;if(state.geometry)renderOverlay();else overlayVisibility();
  $('breadcrumb').textContent=name==='review'?'Human review':name==='geometry'?'Geometry studio':name==='tracks'?'Person tracks':'Recorded footage';
  document.dispatchEvent(new CustomEvent('sentient:panel',{detail:name}));
}
$('zones-toggle').onclick=()=>{if(state.editing)panel('playback');else{state.showZones=!state.showZones;overlayVisibility()}};
$('inspector-playback').onclick=()=>panel('playback');
function openImport(){$('import-dialog').showModal()}
for(const id of ['import-open','import-button','empty-import'])$(id).onclick=openImport;
$('import-close').onclick=()=>$('import-dialog').close();
$('inspector-geometry').onclick=()=>panel('geometry');$('inspector-review').onclick=()=>panel('review');
$('geometry-tab').onclick=()=>panel('geometry');$('review-tab').onclick=()=>panel('review');$('workspace-tab').onclick=()=>panel('playback');

function videoTitle(v){return v.source_profile?.dataset==='RetailAction'?'RetailAction '+v.collection_session.split('/').at(-1)+' · '+v.display_name.replace('_video.mp4',''):v.display_name;}
async function library(selectId){let videos=await api('/api/videos');if(selectId&&state.demoVideoIds.length&&!state.demoVideoIds.includes(selectId))$('library-mode').value='all';if($('library-mode').value==='demo'&&videos.some(v=>state.demoVideoIds.includes(v.id)))videos=videos.filter(v=>state.demoVideoIds.includes(v.id));$('library').innerHTML=videos.length?videos.map(v=>`<button class="asset ${state.video?.id===v.id?'active':''}" data-video="${v.id}"><b>◫ &nbsp;${esc(videoTitle(v))}</b><small>${esc(v.source_profile?.dataset??v.camera_id)} · ${v.frame_dims_px.join('×')} · ${v.source_profile?.source_kind==='sparse_clip'?'SPARSE · ':''}${v.frame_count} frames ${v.variable_frame_rate?'· VFR':''}</small></button>`).join(''):'<p style="font-size:10px;padding:10px 0">Your recordings will appear here.</p>';document.querySelectorAll('[data-video]').forEach(button=>button.onclick=guard(()=>loadVideo(button.dataset.video)));if(selectId)await loadVideo(selectId)}
$('library-mode').onchange=guard(()=>library());
function playbackControls(disabled){for(const key of ['play','previous-frame','next-frame','seek','zones-toggle'])$(key).disabled=disabled}
async function loadVideo(id){
  const selection=++state.selection;++state.navigation;++state.analysisNavigation;++state.reviewNavigation;
  state.loading=true;$('player').pause();playbackControls(true);
  state.pickupEvents=[];$('pickup-events').innerHTML='';$('pickup-summary').textContent='Analyze video to find pickups.';$('pickup-indicator').hidden=true;
  state.video=null;state.geometry=null;state.geometryId=null;state.analysis=null;state.events=[];state.target=null;state.drag=null;
  document.dispatchEvent(new CustomEvent('sentient:video-clearing'));
  $('player').removeAttribute('src');$('player').load();
  $('frame-image').hidden=true;$('frame-image').removeAttribute('src');$('overlay').innerHTML='';
  $('shape-list').innerHTML='';$('shape-count').textContent='0';$('shape-edit').hidden=true;
  $('source-profile').hidden=true;
  $('video-name').textContent='Loading recording…';$('video-source').textContent='';
  $('stage-label').hidden=true;$('frame-label').textContent='FRAME —';$('pts-label').textContent='Loading source timing…';
  $('review-form').hidden=true;$('review-empty').hidden=false;$('model-scores').hidden=true;
  renderEvents();panel('playback');notice('Loading recording and source timing…');
  try{
    const v=await api('/api/videos/'+id);if(selection!==state.selection)return;
    state.video=v;state.frame=0;state.revision=0;
    const profile=v.source_profile;
    if(profile){$('source-profile').hidden=false;$('source-profile').textContent=profile.dataset==='CARR'?'HD retail demo · '+v.frame_dims_px.join(' × ')+' · CARR':profile.source_kind==='sparse_clip'?'RetailAction · '+v.frame_count+' supplied action frames · physical dwell/counts unavailable':v.frame_dims_px.join(' × ')+' · recorded video';}
    $('video-name').textContent=videoTitle(v);$('video-source').textContent=v.store_id;
    $('camera-label').textContent=v.camera_id+' / '+v.stream_session_id;
    $('dimensions-label').textContent=v.frame_dims_px.join(' × ')+' px';$('capture-label').textContent=v.capture_time_utc??'Unknown';
    $('timing-label').textContent='Source PTS · '+(v.variable_frame_rate?'variable rate':'indexed frames');
    $('vfr-badge').textContent=v.variable_frame_rate?'VFR · SOURCE PTS':'SOURCE PTS';
    $('stage').style.aspectRatio=v.frame_dims_px[0]+'/'+v.frame_dims_px[1];
    $('overlay').setAttribute('viewBox','0 0 '+v.frame_dims_px.join(' '));
    $('empty-state').hidden=true;$('stage-label').hidden=false;
    $('player').src='/api/videos/'+id+'/content';$('player').load();
    $('seek').max=(v.last_media_time_ms-v.first_media_time_ms)/1000;
    $('export').href='/api/annotations/export?video_id='+id;$('geometry-export').href='/api/videos/'+id+'/geometry/export';$('attach-records').checked=true;
    await reloadGeometry();if(selection!==state.selection)return;
    await setFrame(0);if(selection!==state.selection)return;
    await loadAnalyses();if(selection!==state.selection)return;
    await library();if(selection!==state.selection)return;
    document.dispatchEvent(new CustomEvent('sentient:video',{detail:v}));
    notice('Recording indexed. '+v.frame_count+' frames. Press Play to watch; Analyze video runs tracking and action scoring. Loaded action scores are suggestions for review.');
  }finally{if(selection===state.selection){state.loading=false;playbackControls(!state.video)}}
}
$('import-form').onsubmit=guard(async event=>{event.preventDefault();const file=$('video-file').files[0],path=$('video-path').value.trim();if(!file&&!path)throw Error('Choose an MP4 file or enter its absolute local path.');$('import-submit').disabled=true;notice('Importing and indexing source PTS. Frames are decoded sequentially without loading the full video into memory.');try{let video;const fields={store_id:$('store-id').value,camera_id:$('camera-id').value,stream_session_id:$('session-id').value,collection_session:$('collection-session').value,collection_day:$('collection-day').value||null};if(file){const body=new FormData();body.append('file',file);Object.entries(fields).forEach(([k,v])=>{if(v!==null)body.append(k,v)});video=await api('/api/videos/upload',{method:'POST',body})}else video=await api('/api/videos/import',jsonOptions('POST',{path,...fields}));$('import-dialog').close();await library(video.id)}finally{$('import-submit').disabled=false}});

async function setFrame(index,setVideo=true,token=++state.navigation){if(!state.video)return;const video=state.video;index=Math.max(0,Math.min(state.video.frame_count-1,index));$('player').pause();const rows=await api(`/api/videos/${video.id}/frames?offset=${index}&limit=1`);if(!rows.length||token!==state.navigation||state.video?.id!==video.id)return;const frame=rows[0];state.frame=index;$('player').pause();if(setVideo)$('player').currentTime=frame.playback_time_ms/1000;$('frame-image').src=`/api/videos/${state.video.id}/frame/${index}`;$('frame-image').hidden=false;updateTiming(frame.media_ms);$('frame-label').textContent=`FRAME ${String(index).padStart(3,'0')} / ${state.video.frame_count-1}`;$('pts-label').textContent=`PTS ${frame.pts} × ${frame.tb_num}/${frame.tb_den} · ${frame.media_ms.toFixed(3)} ms`;document.dispatchEvent(new CustomEvent('sentient:frame',{detail:frame}));}
function updateTiming(time){if(!state.video)return;const pickup=state.pickupEvents.find(e=>e.start_media_ms<=time&&time<=e.end_media_ms);$('pickup-indicator').hidden=!pickup;if(pickup)$('pickup-indicator').textContent='Pickup · Local '+pickup.local_track_id.split(':').at(-1)+' · take score '+pickup.score.toFixed(3);const relative=time-state.video.first_media_time_ms;$('seek').value=relative/1000;$('time-label').textContent=formatMs(relative)+' / '+formatMs(state.video.last_media_time_ms-state.video.first_media_time_ms)+' s'}
async function seekMedia(ms){if(!state.video)throw Error('Attach a video to seek this event.');const video=state.video,token=++state.navigation;const frame=await api(`/api/videos/${video.id}/seek?media_ms=${encodeURIComponent(ms)}`);if(token!==state.navigation||state.video?.id!==video.id)return;await setFrame(frame.frame_index,true,token)}
async function stepFrame(delta){
  if(!state.video)return;
  const video=state.video,selection=state.selection,token=++state.navigation;
  let index=state.frame;
  if(!$('player').paused||$('frame-image').hidden){
    const ms=sourceMs();$('player').pause();
    const frame=await api(`/api/videos/${video.id}/seek?media_ms=${encodeURIComponent(ms)}`);
    if(selection!==state.selection||token!==state.navigation)return;
    index=frame.frame_index;
  }
  await setFrame(index+delta,true,token);
}
$('previous-frame').onclick=guard(()=>stepFrame(-1));$('next-frame').onclick=guard(()=>stepFrame(1));
$('play').onclick=guard(async()=>{if($('player').paused){++state.navigation;$('frame-image').hidden=true;await $('player').play()}else{$('player').pause();await seekMedia(sourceMs())}});
$('player').onplay=()=>{++state.navigation;$('play').textContent='Ⅱ';$('play').setAttribute('aria-label','Pause');$('frame-image').hidden=true};$('player').onpause=()=>{$('play').textContent='▶';$('play').setAttribute('aria-label','Play')};$('player').ontimeupdate=()=>{updateTiming(sourceMs());if(state.video&&!$('player').paused){$('frame-label').textContent='PLAYING';$('pts-label').textContent='SOURCE MEDIA '+sourceMs().toFixed(3)+' ms · pause for exact indexed PTS'}};
$('player').onended=guard(()=>{if(!state.loading)return seekMedia(sourceMs())});
$('player').onerror=()=>{if(state.video)notice('Browser playback is unavailable for this file or codec. Indexed source-frame navigation remains available; check whether the registered file moved.',true)};
$('seek').onchange=guard(()=>seekMedia(Number($('seek').value)*1000+state.video.first_media_time_ms));

async function reloadGeometry(){if(!state.video)throw Error('Import a video before editing geometry.');const video=state.video,selection=state.selection;const saved=await api(`/api/videos/${video.id}/geometry`);if(selection!==state.selection||state.video?.id!==video.id)return;state.geometry=saved.geometry;state.geometryId=saved.id;state.selectedShape=state.geometry.shapes[0]?.id;state.points=[];state.tool=null;$('max-gap').value=state.geometry.max_gap_ms;$('geometry-revision').textContent='Saved geometry · revision '+saved.revision;renderShapes()}
const colors={line:'#d1ed98',zone:'#7bc8b1',queue:'#dfb777',checkout:'#bca0dd'};
function renderShapes(){if(!state.geometry)return;const g=state.geometry;$('shape-count').textContent=g.shapes.length;$('shape-list').innerHTML=g.shapes.map(s=>`<div class="shape-row ${s.id===state.selectedShape?'selected':''}" data-shape="${esc(s.id)}"><i style="background:${colors[s.kind]}"></i><div><b>${esc(s.name)}</b><small>${esc(s.id)}</small></div><span>${esc(s.kind.toUpperCase())}</span></div>`).join('');document.querySelectorAll('[data-shape]').forEach(row=>row.onclick=()=>{state.selectedShape=row.dataset.shape;renderShapes()});const selected=g.shapes.find(s=>s.id===state.selectedShape);$('shape-edit').hidden=!selected;if(selected){$('shape-name').value=selected.name;$('shape-id').value=selected.id;$('reverse-line').hidden=selected.kind!=='line';$('hysteresis').value=selected.kind==='line'?selected.hysteresis_px:g.shapes.find(s=>s.kind==='line')?.hysteresis_px??5;}$('active-selects').innerHTML=['line','zone','queue','checkout'].map(kind=>`<label>Active ${kind}<select data-active="${kind}">${kind==='queue'||kind==='checkout'?'<option value="">Disabled</option>':''}${g.shapes.filter(s=>s.kind===kind).map(s=>`<option value="${esc(s.id)}" ${g.active[kind]===s.id?'selected':''}>${esc(s.name)}</option>`).join('')}</select></label>`).join('');document.querySelectorAll('[data-active]').forEach(select=>select.onchange=()=>{g.active[select.dataset.active]=select.value||null});renderOverlay()}
function renderOverlay(){if(!state.geometry)return;const g=state.geometry,w=state.video.frame_dims_px[0],radius=Math.max(3,w*.005);let svg='<defs><marker id="direction-arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#d1ed98"/></marker></defs>';for(const shape of g.shapes){const points=shape.points.map(p=>p.join(',')).join(' '),color=colors[shape.kind];if(shape.kind==='line'){svg+=`<polyline points="${points}" fill="none" stroke="${color}" stroke-width="${w*.0025}" stroke-dasharray="${w*.006} ${w*.004}"/>`;const [a,b]=shape.points,dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy)||1,m=[(a[0]+b[0])/2,(a[1]+b[1])/2];svg+=`<line x1="${m[0]}" y1="${m[1]}" x2="${m[0]-dy/length*w*.06}" y2="${m[1]+dx/length*w*.06}" stroke="${color}" stroke-width="${w*.002}" marker-end="url(#direction-arrow)"/>`}else svg+=`<polygon points="${points}" fill="${color}18" stroke="${color}" stroke-width="${w*.002}"/>`;svg+=`<text x="${shape.points[0][0]+w*.008}" y="${Math.max(w*.022,shape.points[0][1]-w*.01)}" fill="${color}" font-size="${w*.014}" font-family="sans-serif">${esc(shape.name)}</text>`;if(state.editing&&shape.id===state.selectedShape)shape.points.forEach((p,index)=>{svg+=`<circle data-vertex="${index}" cx="${p[0]}" cy="${p[1]}" r="${radius}" fill="${color}" stroke="#0d1b12" stroke-width="${w*.002}"/>`})}if(state.points.length){svg+=`<polyline points="${state.points.map(p=>p.join(',')).join(' ')}" fill="none" stroke="#fff" stroke-dasharray="4 3" stroke-width="${w*.002}"/>`;for(const p of state.points)svg+=`<circle cx="${p[0]}" cy="${p[1]}" r="${radius}" fill="white"/>`}$('overlay').innerHTML=svg;overlayVisibility();$('drawing-actions').hidden=!state.tool;document.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('active',b.dataset.tool===state.tool));}
function pointFor(event){const r=$('overlay').getBoundingClientRect(),[w,h]=state.video.frame_dims_px;return [Math.max(0,Math.min(w,(event.clientX-r.left)/r.width*w)),Math.max(0,Math.min(h,(event.clientY-r.top)/r.height*h))].map(n=>Math.round(n*100)/100)}
$('overlay').onpointerdown=event=>{if(!state.video||!state.editing)return;const vertex=event.target.dataset.vertex;if(vertex!==undefined&&!state.tool){state.drag=Number(vertex);$('overlay').setPointerCapture(event.pointerId);event.preventDefault();return}if(state.tool){state.points.push(pointFor(event));if(state.tool==='line'&&state.points.length===2)finishShape();else renderOverlay()}};
$('overlay').onpointermove=event=>{if(state.drag===null)return;const shape=state.geometry.shapes.find(s=>s.id===state.selectedShape);shape.points[state.drag]=pointFor(event);renderOverlay()};$('overlay').onpointerup=()=>{state.drag=null};$('overlay').onpointercancel=()=>{state.drag=null};
document.querySelectorAll('[data-tool]').forEach(button=>button.onclick=guard(()=>{if(!state.video)throw Error('Import a video before drawing.');panel('geometry');state.tool=button.dataset.tool;state.points=[];$('drawing-help').textContent=state.tool==='line'?'Click two endpoints. The arrow shows the inward direction.':'Click vertices on the frame, then choose Finish shape.';renderOverlay()}));
function finishShape(){if(!state.tool)return;if(state.points.length<(state.tool==='line'?2:3)){notice('Add '+(state.tool==='line'?'two endpoints.':'at least three vertices.'),true);return}let number=1;while(state.geometry.shapes.some(s=>s.id===`${state.tool}-${number}`))number++;const shape={id:`${state.tool}-${number}`,name:state.tool[0].toUpperCase()+state.tool.slice(1)+' '+number,kind:state.tool,points:state.points};if(state.tool==='line')shape.hysteresis_px=5;state.geometry.shapes.push(shape);state.selectedShape=shape.id;if(!state.geometry.active[shape.kind])state.geometry.active[shape.kind]=shape.id;state.tool=null;state.points=[];renderShapes();notice('Shape added. Save geometry to create a persisted revision.')}
$('finish-shape').onclick=finishShape;$('undo-point').onclick=()=>{state.points.pop();renderOverlay()};$('cancel-shape').onclick=()=>{state.tool=null;state.points=[];renderOverlay()};
$('shape-name').oninput=()=>{const shape=state.geometry?.shapes.find(s=>s.id===state.selectedShape);if(shape){shape.name=$('shape-name').value;renderOverlay();document.querySelector('.shape-row.selected b').textContent=shape.name}};
$('shape-id').onchange=guard(()=>{const shape=state.geometry.shapes.find(s=>s.id===state.selectedShape),value=$('shape-id').value.trim();if(!value||state.geometry.shapes.some(s=>s!==shape&&s.id===value))throw Error('Choose a unique, nonempty shape identifier.');if(state.geometry.active[shape.kind]===shape.id)state.geometry.active[shape.kind]=value;shape.id=value;state.selectedShape=value;renderShapes()});
$('reverse-line').onclick=()=>{const shape=state.geometry.shapes.find(s=>s.id===state.selectedShape);shape.points.reverse();renderOverlay()};$('delete-shape').onclick=()=>{const shape=state.geometry.shapes.find(s=>s.id===state.selectedShape);state.geometry.shapes=state.geometry.shapes.filter(s=>s!==shape);if(state.geometry.active[shape.kind]===shape.id)state.geometry.active[shape.kind]=state.geometry.shapes.find(s=>s.kind===shape.kind)?.id??null;state.selectedShape=state.geometry.shapes[0]?.id;renderShapes()};
$('hysteresis').onchange=()=>{const shape=state.geometry?.shapes.find(s=>s.id===state.geometry.active.line);if(shape)shape.hysteresis_px=Number($('hysteresis').value)};
$('save-geometry').onclick=guard(async()=>{if(!state.video)throw Error('Import a recording first.');state.geometry.max_gap_ms=Number($('max-gap').value);const saved=await api(`/api/videos/${state.video.id}/geometry`,jsonOptions('PUT',state.geometry));state.geometryId=saved.id;$('geometry-revision').textContent='Saved geometry · revision '+saved.revision;notice('Geometry saved as revision '+saved.revision+'. Tracking replays use this exact configuration.');renderShapes()});$('reload-geometry').onclick=guard(reloadGeometry);

async function loadAnalyses(){
  const selection=state.selection,videoId=state.video?.id;
  const rows=await api('/api/analyses'+(videoId?'?video_id='+videoId:''));
  if(selection!==state.selection)return;
  if(rows.length)await selectAnalysis(rows[0].id);
  else{state.analysis=null;state.events=[];renderEvents();$('metric-crossings').textContent='— crossings';$('metric-dwell').textContent='— observed dwell';$('health-label').textContent='No health records'}
}
$('records-open').onclick=()=>$('records-file').click();$('records-file').onchange=guard(async()=>{const file=$('records-file').files[0];if(!file)return;const body=new FormData();body.append('file',file);if($('attach-records').checked&&state.video){body.append('video_id',state.video.id);body.append('geometry_id',state.geometryId)}notice('Validating v1 records and replaying into SQLite…');const result=await api('/api/replays',{method:'POST',body});await selectAnalysis(result.id);notice(`${result.summary.records_inserted} records added · ${result.summary.records_duplicate} exact retries · ${result.summary.events_added} events added. Model scores remain review evidence.`);$('records-file').value=''});
async function selectAnalysis(id){const selection=state.selection,token=++state.analysisNavigation;const data=await api(`/api/analyses/${id}/events`);if(selection!==state.selection||token!==state.analysisNavigation)return;await refreshAnalysisMenu(id);if(selection!==state.selection||token!==state.analysisNavigation)return;if(state.analysis?.id!==id){++state.reviewNavigation;state.target=null;state.revision=0;$('review-form').hidden=true;$('review-empty').hidden=false;$('model-scores').hidden=true;}state.analysis=data.analysis;state.events=data.events;$('event-count').textContent=data.events.length;$('metric-crossings').textContent=data.analysis.summary.physical_analytics_supported===false?'Crossings unavailable · sparse source':data.analysis.summary.inward_crossings+' inward / '+data.analysis.summary.outward_crossings+' outward';$('metric-dwell').textContent=data.analysis.summary.physical_analytics_supported===false?'Physical dwell unavailable':Number(data.analysis.summary.complete_observed_dwell_seconds).toFixed(3)+' s observed dwell';$('health-label').textContent=data.health.length?'Source health: '+data.health.at(-1).state+' · '+data.clock_statuses.join(', '):'No health records · coverage unknown';renderEvents();document.dispatchEvent(new CustomEvent('sentient:analysis',{detail:data}));await loadPickups(id)}
function renderEvents(){$('event-count').textContent=state.events.length;$('events-body').innerHTML=state.events.length?state.events.map((row,index)=>`<tr data-event="${index}" class="${state.target?.target_id===row.target_id?'selected':''}"><td class="mono">${formatMs(row.media_time_ms)} s</td><td class="kind">${esc(row.kind)}${row.kind==='action.scored'?' / '+esc(row.payload.action):''}</td><td>${esc(row.track_key[3])} <small class="muted">${esc(row.track_key[2])}</small></td><td><span class="badge">${esc(row.quality)}</span></td><td><span class="review-status">${esc(row.review?.human.decision??'Unreviewed')}</span></td><td>↗</td></tr>`).join(''):'<tr><td colspan="6" class="empty-table">No tracking records loaded. Video playback works independently.</td></tr>';document.querySelectorAll('[data-event]').forEach(row=>row.onclick=guard(()=>selectEvent(state.events[Number(row.dataset.event)])))}
async function loadPickups(id){
  const data=await api('/api/action-analysis/analyses/'+id+'/pickups');
  if(state.analysis?.id!==id)return;
  state.pickupEvents=data.events;
  $('pickup-summary').textContent=data.policy?data.events.length+' predicted pickup'+(data.events.length===1?'':'s'):'Analyze video to find pickups.';
  $('pickup-events').innerHTML=data.events.map((event,index)=>'<button class="pickup-event" data-pickup="'+index+'">Pickup · Local '+esc(event.local_track_id.split(':').at(-1))+'<br><small>'+formatMs(event.start_media_ms)+'–'+formatMs(event.end_media_ms)+' s · take score '+event.score.toFixed(3)+'</small></button>').join('');
  document.querySelectorAll('[data-pickup]').forEach(button=>button.onclick=guard(async()=>{panel('playback');await seekMedia(state.pickupEvents[Number(button.dataset.pickup)].start_media_ms);}));
}
function suggestedLabel(row){if(row.kind==='entrance.inward')return'inward';if(row.kind==='entrance.outward')return'outward';if(row.kind.startsWith('zone.'))return'zone_visit';return row.payload.action??'unknown'}
async function selectEvent(row){const selection=state.selection,token=++state.reviewNavigation,analysisId=state.analysis?.id;state.target=row;state.revision=0;$('review-form').hidden=true;$('model-scores').hidden=true;panel('review');renderEvents();if(!state.video||state.analysis.video_id!==state.video.id){notice('This analysis has no attached footage. Records and events are available independently; import the matching video to review evidence.');return}await seekMedia(row.start_media_ms);if(selection!==state.selection||token!==state.reviewNavigation)return;$('review-empty').hidden=true;$('review-context').textContent=row.kind+' · local track '+row.track_key[3]+' · '+row.quality+' source evidence';$('review-start').value=row.start_media_ms;$('review-end').value=row.end_media_ms;$('review-label').value=suggestedLabel(row);$('decision').value='accepted';$('review-notes').value='';const history=await api(`/api/analyses/${analysisId}/reviews?target_id=${encodeURIComponent(row.target_id)}`);if(selection!==state.selection||token!==state.reviewNavigation)return;state.revision=history.length?history.at(-1).revision:0;if(history.length){const last=history.at(-1);$('review-start').value=last.start_media_ms;$('review-end').value=last.end_media_ms;for(const [id,key] of [['reviewer','reviewer'],['decision','decision'],['review-label','label'],['visibility','visibility'],['uncertainty','uncertainty'],['review-notes','notes']])$(id).value=last.human[key]}$('review-form').hidden=false;
  if(row.kind==='action.scored'&&row.payload.scores){
    const scores=row.payload.scores;
    $('model-scores').innerHTML='<b>Raw model scores · uncalibrated</b><div class="score-grid">'+['take','put','touch','background'].map(name=>'<span>'+name+' <b>'+(Number.isFinite(scores[name])?scores[name].toFixed(3):'—')+'</b></span>').join('')+'</div><p>These scores do not confirm an action. Choose a human label from the footage.</p><code>'+esc(row.payload.model_id)+' · '+esc(row.payload.task)+'</code>';
    $('model-scores').hidden=false;
  }
  $('revision-history').innerHTML=history.length?history.slice().reverse().map(r=>`<div class="revision"><b>Revision ${r.revision}</b> · ${esc(r.human.decision)} / ${esc(r.human.label)}<br><small>${esc(r.human.reviewer)} · ${esc(r.created_at_utc)}</small></div>`).join(''):'<p style="font-size:10px">No human decisions yet.</p>'}
$('evidence-before').onclick=guard(()=>seekMedia(state.target.evidence_start_ms));$('evidence-during').onclick=guard(()=>seekMedia((Number($('review-start').value)+Number($('review-end').value))/2));$('evidence-after').onclick=guard(()=>seekMedia(state.target.evidence_end_ms));
for(const label of ['take','put','touch','background','unknown','inward','outward','zone_visit','queue_abandoned','service_completed','other']){const option=document.createElement('option');option.value=label;option.textContent=label.replaceAll('_',' ');$('review-label').append(option)}
$('review-form').onsubmit=guard(async event=>{event.preventDefault();const body={analysis_id:state.analysis.id,target_id:state.target.target_id,request_id:crypto.randomUUID(),expected_revision:state.revision,reviewer:$('reviewer').value,decision:$('decision').value,label:$('review-label').value,visibility:$('visibility').value,uncertainty:$('uncertainty').value,notes:$('review-notes').value,start_media_ms:Number($('review-start').value),end_media_ms:Number($('review-end').value)};const result=await api(`/api/videos/${state.video.id}/reviews`,jsonOptions('POST',body));await selectAnalysis(state.analysis.id);await selectEvent(state.events.find(r=>r.target_id===body.target_id));notice('Human review saved as revision '+result.revision+'. Original analytics and model scores are unchanged.');});
guard(async()=>{
  const status=await api('/api/status');state.demoVideoIds=status.demo_library??[];const videos=await api('/api/videos');
  const link=new URLSearchParams(location.search);
  if(!link.get('video')&&status.demo&&videos.some(v=>v.id===status.demo.video_id)){
    link.set('video',status.demo.video_id);link.set('analysis',status.demo.analysis_id);link.set('tracking_job',status.demo.tracking_job_id);history.replaceState(null,'','/?'+link);
  }
  const requested=link.get('video');
  if(requested&&!videos.some(v=>v.id===requested))throw Error('The linked recording is not available in this workspace.');
  const selected=requested||videos[0]?.id;await library(selected);
  if(!videos.length)await loadAnalyses();
  if(link.get('analysis')&&state.video?.id===selected)await selectAnalysis(link.get('analysis'));
  const target=link.get('target');
  if(target&&state.video?.id===selected){
    const row=state.events.find(r=>r.target_id===target);
    if(row)await selectEvent(row);else notice('The linked review target is not available for this recording.',true);
  }
})();

$('geometry-import').onclick=guard(()=>{if(!state.video)throw Error('Import a video first.');$('geometry-file').click()});$('geometry-file').onchange=guard(async()=>{const file=$('geometry-file').files[0];if(!file)return;const geometry=JSON.parse(await file.text());const saved=await api(`/api/videos/${state.video.id}/geometry`,jsonOptions('PUT',geometry));await reloadGeometry();$('geometry-file').value='';notice('Geometry JSON loaded as revision '+saved.revision+'.')});

async function refreshAnalysisMenu(selected){const selection=state.selection;const rows=await api('/api/analyses'+(state.video?'?video_id='+state.video.id:''));if(selection!==state.selection)return;$('analysis-select').innerHTML=rows.map(r=>`<option value="${r.id}">Run ${r.id.slice(0,8)} · ${esc(new Date(r.created_utc).toLocaleString())}</option>`).join('');if(selected&&!rows.some(r=>r.id===selected)){const option=document.createElement('option');option.value=selected;option.textContent='Independent JSONL run';$('analysis-select').append(option)}$('analysis-select').value=selected??rows[0]?.id??'';}$('analysis-select').onchange=guard(()=>selectAnalysis($('analysis-select').value));
