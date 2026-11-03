/* One application action coordinates people tracking, action scoring and review. */
(()=>{
  let timer=null,generation=0;
  const active=j=>j&&['queued','running','cancelling'].includes(j.status);
  function render(j){
    state.actionJob=j;
    $('analyze-video').disabled=!state.video||active(j);
    $('cancel-analysis').hidden=!active(j);
    if(!j)return;
    $('track-people').disabled=active(j);
    $('cancel-tracking').hidden=true;
    $('tracking-status').textContent={queued:'Analysis queued',running:'Analyzing video',cancelling:'Cancelling analysis',completed:'Ready to review',error:'Analysis failed',cancelled:'Analysis cancelled'}[j.status]??j.status;
    const stage={tracking_people:'Detecting and tracking people',scoring_actions:'Scoring action windows',building_review:'Preparing review evidence',ready_for_review:'Pickup markers and person paths are ready'}[j.stage]??j.stage;
    $('tracking-detail').textContent=j.error??(stage+(j.status==='completed'?` · ${j.action_windows} scored windows · ${j.elapsed_seconds.toFixed(1)} s · scores need human review`:''));
    $('tracking-progress').value=j.progress;
  }
  async function showResult(j){
    if(state.video?.id!==j.video_id)return;
    const url=new URL(location.href);url.search='';url.searchParams.set('video',j.video_id);
    url.searchParams.set('analysis',j.analysis_id);url.searchParams.set('tracking_job',j.tracking_job_id);
    history.replaceState(null,'',url);
    await selectAnalysis(j.analysis_id);
    panel('playback');
    if(state.pickupEvents.length)await seekMedia(Math.max(state.video.first_media_time_ms,state.pickupEvents[0].start_media_ms-400));
    render(j);notice('Analysis complete · '+(j.pickup_events??0)+' predicted pickups. Press Play to watch pickups and person paths.');
  }
  function watch(identity,token){
    clearTimeout(timer);
    timer=setTimeout(guard(async()=>{
      const j=await api('/api/action-analysis/jobs/'+identity);
      if(token!==generation||state.video?.id!==j.video_id)return;
      render(j);
      if(active(j))watch(identity,token);
      else if(j.status==='completed')await showResult(j);
      else if(j.error)notice(j.error,true);
    }),350);
  }
  $('analyze-video').onclick=guard(async()=>{
    if(!state.video)return;
    const token=++generation;
    $('analyze-video').disabled=true;
    const period=Number($('tracking-interval').value);
    try{
      const j=await api('/api/action-analysis/videos/'+state.video.id,jsonOptions('POST',{
        geometry_id:state.geometryId,source_kind:$('analysis-source-kind').value,
        tracking_config:{detector:$('tracking-model').value,device:$('tracking-device').value,sample_period_ms:period,max_lost_ms:Math.max(1000,period*2),
          min_person_area_ratio:Number($('tracking-min-area').value)/100,person_nms_iou:Number($('tracking-nms').value)}
      }));
      if(token!==generation)return;
      render(j);
      if(j.status==='completed')await showResult(j);else{notice('Analyzing this recording: people tracking, action scoring, then review evidence.');watch(j.id,token);}
    }catch(error){$('analyze-video').disabled=false;throw error;}
  });
  $('cancel-analysis').onclick=guard(async()=>{if(state.actionJob){render(await api('/api/action-analysis/jobs/'+state.actionJob.id+'/cancel',{method:'POST'}));}});
  document.addEventListener('sentient:video-clearing',()=>{++generation;clearTimeout(timer);state.actionJob=null;$('analyze-video').disabled=true;$('cancel-analysis').hidden=true;});
  document.addEventListener('sentient:video',guard(async event=>{
    const token=generation,video=event.detail;
    $('analysis-source-kind').value=video.source_profile?.source_kind??'continuous';
    $('analysis-source-kind').disabled=video.source_profile?.source_kind==='sparse_clip';
    const [ready,jobs]=await Promise.all([api('/api/action-analysis/status'),api('/api/action-analysis/videos/'+video.id)]);
    if(token!==generation||state.video?.id!==video.id)return;
    const link=new URLSearchParams(location.search),selected=link.get('analysis');
    const j=jobs.find(j=>active(j))??jobs.find(j=>j.status==='completed'&&(!selected||selected===j.analysis_id))??null;
    if(j){
      $('tracking-min-area').value=(j.config.min_person_area_ratio??0)*100;
      $('tracking-nms').value=j.config.person_nms_iou??0;
      $('tracking-device').value=j.config.device;
      $('tracking-model').value=j.config.detector;
      $('tracking-interval').value=j.config.sample_period_ms;
      $('analysis-source-kind').value=j.source_kind;
    }
    render(j);$('analyze-video').disabled=!ready.available||active(j);
    if(!ready.available)$('analyze-video').title='Configure the local action worker first';
    if(active(j))watch(j.id,token);
  }));
})();
