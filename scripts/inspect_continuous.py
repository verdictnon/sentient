"""Write source-time contact sheets for an external continuous diagnostic."""
import argparse,json,math
from pathlib import Path
import av
from PIL import Image,ImageDraw

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--video',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--every-seconds',type=float,default=5)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
frames=[];next_time=0;count=0;last=0;dimensions=None
with av.open(str(a.video)) as container:
 stream=container.streams.video[0];stream.thread_count=1
 for i,f in enumerate(container.decode(stream)):
  count+=1;last=float(f.pts*f.time_base);dimensions=[f.width,f.height]
  if last+1e-9>=next_time:
   image=f.to_image();image.thumbnail((300,225));frames.append((i,last,image))
   next_time=last+a.every_seconds
for page,start in enumerate(range(0,len(frames),20)):
 batch=frames[start:start+20];sheet=Image.new('RGB',(1200,250*math.ceil(len(batch)/4)),'#eee')
 draw=ImageDraw.Draw(sheet)
 for n,(i,t,image) in enumerate(batch):
  x=(n%4)*300;y=(n//4)*250;sheet.paste(image,(x,y+24));draw.text((x+6,y+4),f'Frame {i} / source PTS {t:.3f}s',fill='#000')
 sheet.save(a.output/f'contact-{page:02d}.jpg')
(a.output/'media.json').write_text(json.dumps({'video':str(a.video.resolve()),'frames':count,'last_source_pts_seconds':last,'dimensions':dimensions,'sampled_frames':[{'frame_index':i,'media_seconds':t} for i,t,_ in frames]},indent=2)+'\n')
print((a.output/'media.json').read_text())
