/* Additive people tracking workspace. Every overlay refers to one source frame. */
(()=>{
  let job=null,timer=null,requestGeneration=0,selectedTrack=null,pathCache=null,trajectoryOffset=0,trackListOffset=0;
  const svg=$('people-overlay');
  function linkedActionRun(j){
    const link=new URLSearchParams(location.search);
    return j&&link.get('video')===state.video?.id&&link.get('analysis')===state.analysis?.id&&
      link.get('tracking_job')===j.id&&j.video_id===state.video?.id&&j.geometry_id===state.analysis?.geometry_id;
  }
  const active=j=>j&&['queued','running','cancelling'].includes(j.status);
  function clearPeople(){svg.dataset.snapshot=JSON.stringify({valid:false,generation:requestGeneration});svg.innerHTML='';delete svg.dataset.frameIndex;delete svg.dataset.presentedMediaMs;delete svg.dataset.mediaMs;if($('frame-zone-count'))$('frame-zone-count').textContent='Zone: unknown';}
  function label(id){return 'Local '+String(id).split(':').at(-1);}
  function status(j){
    if(state.actionJob?.video_id===state.video?.id)return;
    $('track-people').disabled=!state.video||active(j);
    $('cancel-tracking').hidden=!active(j);
    if(!j){$('tracking-min-area').value=0;$('tracking-nms').value=0;$('tracking-status').textContent='Ready to track this recording';$('tracking-progress').value=0;return;}
    $('tracking-model').value=j.config.detector;
    $('tracking-min-area').value=(j.config.min_person_area_ratio??0)*100;$('tracking-nms').value=j.config.person_nms_iou??0;
    $('tracking-progress').value=j.progress;
    const text={queued:'Queued',running:'Tracking people',cancelling:'Cancelling',completed:'Tracking complete',error:'Tracking failed',cancelled:'Tracking cancelled'}[j.status]??j.status;
    $('tracking-status').textContent=text;
    $('tracking-detail').textContent=j.error??`${j.frames_processed} source frames · ${j.inference_frames} detector frames · ${j.observed_records} observed / ${j.predicted_records} predicted${j.metrics?' · '+j.metrics.processing_seconds.toFixed(2)+' s processing':''}`;
    if(linkedActionRun(j))$('tracking-detail').textContent+=' · Overlay from tracking run '+j.id.slice(0,8)+'; action review run '+state.analysis.id.slice(0,8);
    if(j.detector?.engineering_test)$('tracking-detail').textContent+=' · ENGINEERING TEST ADAPTER';
    $('track-source').textContent=`${j.detector.implementation} · ${j.tracker_version} · ${j.config.sample_period_ms} ms sampling. Camera/session local only.`;
  }
  async function loadJobs(){
    if(!state.video)return;
    const videoId=state.video.id;
    const jobs=await api(`/api/tracking/videos/${videoId}`);
    if(state.video?.id!==videoId)return;
    job=jobs.find(j=>linkedActionRun(j))??jobs.find(j=>state.analysis&&j.analysis_id===state.analysis.id)??jobs.find(j=>j.geometry_id===state.geometryId)??null;
    status(job);
    if(active(job))watch(job.id);
    if(job?.status==='completed'){await loadPaths();await listTracks();await overlay({index:state.frame});}
    else clearPeople();
  }
  async function completed(j){
    if(state.video?.id!==j.video_id)return;
    status(j);
    await selectAnalysis(j.analysis_id);
    await loadPaths();await listTracks();await overlay({index:state.frame});
  }
  function watch(id){
    clearTimeout(timer);
    timer=setTimeout(guard(async()=>{
      const current=await api(`/api/tracking/jobs/${id}`);
      if(state.video?.id!==current.video_id)return;
      job=current;status(job);
      if(active(job))watch(id);
      else if(job.status==='completed'){await completed(job);notice('Person tracking completed. Source-aligned observations drive P0 counts and dwell; queue service inference is disabled.');}
      else notice(job.error??'Tracking cancelled. Incomplete output was not published as a successful analysis.',true);
    }),300);
  }
  $('track-people').onclick=async()=>{
    if(!state.video)return;
    state.actionJob=null;
    $('track-people').disabled=true;$('tracking-status').textContent='Preparing tracking job';
    try{
      const interval=Number($('tracking-interval').value);
      job=await api(`/api/tracking/videos/${state.video.id}`,jsonOptions('POST',{
        geometry_id:state.geometryId,config:{detector:$('tracking-model').value,source_kind:$('analysis-source-kind').value,device:$('tracking-device').value,sample_period_ms:interval,max_lost_ms:Math.max(1000,interval*2),min_person_area_ratio:Number($('tracking-min-area').value)/100,person_nms_iou:Number($('tracking-nms').value)}
      }));
      status(job);panel('tracks');
      if(job.status==='completed'){await completed(job);notice('Loaded the existing completed run for this video, model and saved configuration. No events were duplicated.');}
      else{notice('Automatic person detection and camera-local tracking started. Progress uses actual decoded source frames.');watch(job.id);}
    }catch(error){$('tracking-status').textContent='Tracking could not start';$('tracking-detail').textContent=error.message;notice(error.message,true);$('track-people').disabled=false;}
  };
  $('cancel-tracking').onclick=guard(async()=>{if(job){job=await api(`/api/tracking/jobs/${job.id}/cancel`,{method:'POST'});status(job);watch(job.id);}});
  $('inspector-tracks').onclick=()=>panel('tracks');
  $('show-people').onchange=guard(()=>overlay({index:state.frame}));

  async function loadPaths(){
    if(!job||job.status!=='completed')return;
    const id=job.id,data=await api('/api/tracking/jobs/'+id+'/paths');
    if(job?.id===id)pathCache=data;
  }
  function pathMarkup(frame){
    if(!$('show-paths').checked||!pathCache||pathCache.job_id!==job?.id)return '';
    return pathCache.tracks.filter(t=>!selectedTrack||t.local_track_id===selectedTrack).map(track=>track.segments.map(segment=>{
      const points=segment.filter(p=>p[0]<=frame);
      return points.length>1?'<polyline class="person-path" data-path-track="'+esc(track.local_track_id)+'" points="'+points.map(p=>p[2]+','+p[3]).join(' ')+'" fill="none" stroke="#72b9f5" stroke-width="3" opacity=".8"/>':'';
    }).join('')).join('');
  }
  $('show-paths').onchange=guard(()=>overlay({index:state.frame}));
  async function overlay({index=null,mediaMs=null}){
    const generation=++requestGeneration;
    clearPeople();
    if(!state.video||!job||job.status!=='completed'||!$('show-people').checked)return;
    if(state.analysis&&state.analysis.id!==job.analysis_id&&!linkedActionRun(job))return;
    const identity=job.id;
    const query=index!==null?'frame_index='+index:'media_ms='+encodeURIComponent(mediaMs);
    const data=await api(`/api/tracking/jobs/${identity}/observations?${query}`);
    if(generation!==requestGeneration||job?.id!==identity)return;
    svg.setAttribute('viewBox','0 0 '+state.video.frame_dims_px.join(' '));
    svg.classList.toggle('geometry-mode',state.editing);
    if(data.frame_index===null)return;
    svg.dataset.frameIndex=data.frame_index;
    svg.dataset.mediaMs=data.media_time_ms;
    $('frame-zone-count').textContent=`Zone: ${data.zone_observed_count===null?'unknown':data.zone_observed_count+' observed'} · ${data.zone_predicted_count} predicted`;
    if(mediaMs!==null)svg.dataset.presentedMediaMs=mediaMs;
    const width=state.video.frame_dims_px[0],font=Math.max(10,width*.015);
    svg.innerHTML=pathMarkup(data.frame_index)+data.observations.map(record=>{
      const [x1,y1,x2,y2]=record.bbox_xyxy_px,predicted=record.position_status==='predicted';
      const color=predicted?'#8bbce9':'#d0ee96',selected=record.local_track_id===selectedTrack;
      const name=`${label(record.local_track_id)} · ${record.score.toFixed(2)} · ${record.position_status}`;
      return `<g data-person="${esc(record.local_track_id)}" data-position-status="${record.position_status}"><rect class="person-box" x="${x1}" y="${y1}" width="${x2-x1}" height="${y2-y1}" fill="${selected?'#c7e89b18':'transparent'}" stroke="${color}" stroke-width="${selected?width*.004:width*.0025}" ${predicted?'stroke-dasharray="8 5"':''}/><text class="person-label" x="${x1+3}" y="${Math.max(font,y1-6)}" fill="${color}" font-size="${font}" stroke="#07100b" stroke-width="${font*.15}" paint-order="stroke">${esc(name)}</text></g>`;
    }).join('');
    svg.dataset.snapshot=JSON.stringify({valid:true,generation,job_id:identity,video_id:state.video.id,frame_index:data.frame_index,media_ms:data.media_time_ms,presented_media_ms:mediaMs});
    svg.querySelectorAll('[data-person]').forEach(group=>group.onclick=guard(()=>inspect(group.dataset.person)));
  }
  async function listTracks(offset=0){
    if(!job||job.status!=='completed')return;
    const identity=job.id;
    const tracks=await api(`/api/tracking/jobs/${identity}/tracks?offset=${offset}&limit=100`);
    if(job?.id!==identity)return;
    trackListOffset=offset;
    $('tracks-page-previous').disabled=offset===0;
    $('tracks-page-next').disabled=tracks.length<100;
    $('track-list').innerHTML=tracks.length?tracks.map(track=>`<button class="track-item" data-inspect-track="${esc(track.local_track_id)}">${esc(label(track.local_track_id))}<small>${track.observed_samples} observed · ${track.samples-track.observed_samples} predicted · ${formatMs(track.first_media_ms)}–${formatMs(track.last_media_ms)} s</small></button>`).join(''):'<div class="quiet-box">No supported person tracks in this run. Empty detector output is retained as an explicit result.</div>';
    document.querySelectorAll('[data-inspect-track]').forEach(button=>button.onclick=guard(()=>inspect(button.dataset.inspectTrack)));
  }
  async function inspect(id,offset=0){
    if(!job)return;
    const identity=job.id;
    selectedTrack=id;trajectoryOffset=offset;panel('tracks');
    const rows=await api(`/api/tracking/jobs/${job.id}/tracks/${encodeURIComponent(id)}?offset=${offset}&limit=100`);
    if(job?.id!==identity||selectedTrack!==id||trajectoryOffset!==offset)return;
    $('track-inspection').hidden=false;$('selected-track-label').textContent=label(id);
    $('track-observations').innerHTML=rows.map(row=>`<button class="track-observation" data-track-frame="${row.frame_index}">Frame ${row.frame_index} · ${formatMs(row.media_time_ms)} s · ${row.position_status} · ${row.score.toFixed(2)}</button>`).join('');
    document.querySelectorAll('[data-track-frame]').forEach(button=>button.onclick=guard(()=>setFrame(Number(button.dataset.trackFrame))));
    $('track-previous').disabled=offset===0;$('track-next').disabled=rows.length<100;
    await overlay({index:state.frame});
  }
  $('track-previous').onclick=guard(()=>inspect(selectedTrack,Math.max(0,trajectoryOffset-100)));
  $('track-next').onclick=guard(()=>inspect(selectedTrack,trajectoryOffset+100));
  $('tracks-page-previous').onclick=guard(()=>listTracks(Math.max(0,trackListOffset-100)));
  $('tracks-page-next').onclick=guard(()=>listTracks(trackListOffset+100));
  document.addEventListener('sentient:video-clearing',()=>{++requestGeneration;clearTimeout(timer);job=null;selectedTrack=null;pathCache=null;clearPeople();status(null);$('track-inspection').hidden=true;});
  document.addEventListener('sentient:video',guard(async()=>{clearTimeout(timer);job=null;selectedTrack=null;pathCache=null;clearPeople();$('track-inspection').hidden=true;await loadJobs();}));
  document.addEventListener('sentient:analysis',guard(loadJobs));
  document.addEventListener('sentient:frame',guard(event=>overlay({index:event.detail.frame_index})));
  document.addEventListener('sentient:panel',()=>svg.classList.toggle('geometry-mode',state.editing));
  if('requestVideoFrameCallback' in $('player')){
    const presented=(now,metadata)=>{
      if(!$('player').paused&&$('frame-image').hidden)guard(overlay)({mediaMs:metadata.mediaTime*1000});
      $('player').requestVideoFrameCallback(presented);
    };
    $('player').requestVideoFrameCallback(presented);
  }
})();
